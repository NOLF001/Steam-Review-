"""
Steam Review Analyzer
Analyzes collected Steam reviews using Claude API → Steam dark-theme HTML report.

Usage:
  python analyze.py reviews.csv --game-name "The Witcher 3" --top-n 1500
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL               = "claude-sonnet-4-5"
INPUT_PRICE_PER_M   = 3.0    # USD per 1M input tokens
OUTPUT_PRICE_PER_M  = 15.0   # USD per 1M output tokens

MAX_CHARS_PER_REVIEW = 500
MAX_REVIEWS_PER_CALL = 100
MIN_REVIEW_LENGTH    = 50
API_CALL_GAP         = 1.0   # seconds between API calls
API_RETRY_DELAYS     = [2, 4, 8]

STEAM_RATINGS = [
    (95, "Overwhelmingly Positive"),
    (80, "Very Positive"),
    (70, "Mostly Positive"),
    (40, "Mixed"),
    (20, "Mostly Negative"),
    (0,  "Very Negative"),
]

# ---------------------------------------------------------------------------
# CSS (Steam dark theme)
# ---------------------------------------------------------------------------

CSS = """\
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: #1b2838;
  color: #c7d5e0;
  font-family: 'Motiva Sans', Arial, sans-serif;
  font-size: 16px;
  line-height: 1.6;
  padding: 40px 20px;
}

.container {
  max-width: 800px;
  margin: 0 auto;
}

/* ── Header ── */
.header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  padding-bottom: 28px;
  margin-bottom: 32px;
  border-bottom: 1px solid #2a475e;
}

.header-left {}

.game-name {
  font-size: 32px;
  font-weight: bold;
  color: #ffffff;
  line-height: 1.2;
  margin-bottom: 6px;
}

.subtitle {
  font-size: 16px;
  color: #8f98a0;
}

.header-right {
  text-align: right;
  flex-shrink: 0;
}

.steam-rating {
  font-size: 20px;
  font-weight: bold;
  margin-bottom: 4px;
}

.review-count {
  font-size: 13px;
  color: #8f98a0;
}

/* ── Sections ── */
.section {
  margin-bottom: 36px;
}

.section-heading {
  font-size: 24px;
  font-weight: bold;
  margin-bottom: 16px;
}

.section-heading.pros   { color: #5c7e10; }
.section-heading.cons   { color: #a34c25; }
.section-heading.verdict { color: #66c0f4; }

/* ── Pros / Cons list ── */
.item-list {
  list-style: none;
}

.item-list li {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid rgba(255,255,255,0.05);
  line-height: 1.6;
}

.item-list li:last-child {
  border-bottom: none;
}

.marker {
  flex-shrink: 0;
  margin-top: 1px;
  font-size: 14px;
}

.marker.pros   { color: #5c7e10; }
.marker.cons   { color: #a34c25; }

/* ── Verdict ── */
.verdict-body {
  border-left: 4px solid #66c0f4;
  padding-left: 20px;
  font-size: 16px;
  line-height: 1.7;
  color: #c7d5e0;
}

/* ── Footer ── */
.footer {
  margin-top: 40px;
  padding-top: 16px;
  border-top: 1px solid #2a475e;
  font-size: 12px;
  color: #566b7d;
  text-align: center;
  line-height: 1.8;
}
"""

# ---------------------------------------------------------------------------
# Token / cost tracker
# ---------------------------------------------------------------------------

class UsageTracker:
    def __init__(self):
        self.input_tokens  = 0
        self.output_tokens = 0

    def add(self, usage) -> None:
        self.input_tokens  += usage.input_tokens
        self.output_tokens += usage.output_tokens

    @property
    def cost_usd(self) -> float:
        return (
            self.input_tokens  / 1_000_000 * INPUT_PRICE_PER_M
            + self.output_tokens / 1_000_000 * OUTPUT_PRICE_PER_M
        )

    def print_summary(self) -> None:
        print(f"  Input  : {self.input_tokens:>10,} tokens")
        print(f"  Output : {self.output_tokens:>10,} tokens")
        print(f"  Est.   : ${self.cost_usd:.4f} USD")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Analyze Steam reviews with Claude API → HTML report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python analyze.py witcher3_reviews.csv --game-name "The Witcher 3" --slug witcher3
  python analyze.py reviews.csv --game-name "Elden Ring" --top-n 1500 --output elden.html
        """,
    )
    p.add_argument("csv_file",
                   help="Path to CSV produced by collect.py")
    p.add_argument("--slug", default=None, metavar="SLUG",
                   help="URL-safe short name (e.g. witcher3). Outputs to reports/{slug}.html "
                        "with nav bar and shared CSS. Takes precedence over --output.")
    p.add_argument("--output", default=None, metavar="FILE",
                   help="Output HTML filename (default: reports/{slug}.html or review_analysis.html)")
    p.add_argument("--game-name", default=None, metavar="NAME",
                   help="Game name shown in the report header")
    p.add_argument("--top-n", type=int, default=1500, metavar="N",
                   help="Number of reviews to analyze (default: 1500)")
    p.add_argument("--api-key", default=None, metavar="KEY",
                   help="Anthropic API key (overrides ANTHROPIC_API_KEY env var)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Data loading & filtering
# ---------------------------------------------------------------------------

def load_and_filter(csv_path: str, min_votes: int = 5) -> tuple:
    """
    Returns (filtered_df, full_pos_ratio).
    All languages are included. full_pos_ratio is captured before the quality
    filter so sampling mirrors the true corpus sentiment distribution.
    """
    print("Loading CSV...")
    if not Path(csv_path).exists():
        print(f"Error: File not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    total = len(df)
    print(f"Total rows loaded: {total:,}")

    # Language distribution
    if "language" in df.columns:
        top_langs = df["language"].value_counts().head(5)
        lang_str  = "  ".join(f"{l}:{n:,}" for l, n in top_langs.items())
        print(f"Top languages: {lang_str}")

    # All languages included — no language filter
    pool = df.copy()

    # Capture pos ratio BEFORE quality filter (true corpus baseline)
    full_pos_ratio = float(pool["voted_up"].mean()) if len(pool) else 0.5

    filtered = pool[
        (pool["votes_up"].fillna(0) >= min_votes) &
        (pool["review"].fillna("").str.len() >= MIN_REVIEW_LENGTH)
    ].copy()
    print(f"After quality filter (votes≥{min_votes}, len≥{MIN_REVIEW_LENGTH}): {total:,} → {len(filtered):,}")

    if len(filtered) < 10:
        print(f"\nWARNING: Only {len(filtered)} reviews passed filtering.")
        answer = input("Continue anyway? [y/N] ").strip().lower()
        if answer != "y":
            sys.exit(0)

    return filtered, full_pos_ratio


# ---------------------------------------------------------------------------
# Stratified sampling
# ---------------------------------------------------------------------------

def stratified_sample(
    df: pd.DataFrame,
    top_n: int,
    full_pos_ratio: float = None,
) -> pd.DataFrame:
    """
    4-dimension stratified sample (priority order):
    1. pos/neg ratio  — matches full_pos_ratio
    2. score tier     — top 30% (high) | mid 40% (random) | low 30% (neg-heavy)
    3. time period    — early / mid / recent  (quantile thirds of timestamp_created)
    4. playtime band  — low (<50h) / mid (50-200h) / high (200h+)
    """
    from datetime import datetime as _dt

    SCORE_COL = "weighted_vote_score"
    TS_COL    = "timestamp_created"
    PT_COL    = "author_playtime_forever_min"
    SEED      = 42

    if len(df) <= top_n:
        print(f"Using all {len(df):,} reviews (<= --top-n {top_n})")
        return df.sort_values(SCORE_COL, ascending=False).reset_index(drop=True)

    # ── Priority 1: pos/neg target counts ──
    pos_ratio    = full_pos_ratio if full_pos_ratio is not None else df["voted_up"].mean()
    n_pos_target = round(top_n * pos_ratio)
    n_neg_target = top_n - n_pos_target

    df = df.copy()

    # ── Priority 3: time period (quantile thirds) ──
    ts_filled = df[TS_COL].fillna(df[TS_COL].median())
    tq33 = float(ts_filled.quantile(1 / 3))
    tq67 = float(ts_filled.quantile(2 / 3))

    def _period(ts):
        if ts <= tq33: return "early"
        if ts <= tq67: return "mid"
        return "recent"

    df["_period"] = ts_filled.map(_period)

    # ── Priority 2: score tier (30/40/30 percentile split) ──
    scores = df[SCORE_COL].fillna(0)
    sq30   = float(scores.quantile(0.30))
    sq70   = float(scores.quantile(0.70))

    def _tier(s):
        if s >= sq70: return "high"
        if s >= sq30: return "mid"
        return "low"

    df["_tier"] = scores.map(_tier)

    # ── Priority 4: playtime band ──
    pt_minutes = df[PT_COL].fillna(0)

    def _pt_band(m):
        h = m / 60
        if h < 50:   return "low"
        if h < 200:  return "mid"
        return "high"

    df["_pt_band"] = pt_minutes.map(_pt_band)

    # Allocation weights
    TIER_FRAC   = {"high": 0.30, "mid": 0.40, "low": 0.30}
    PERIOD_FRAC = {"early": 1 / 3, "mid": 1 / 3, "recent": 1 / 3}
    PT_FRAC     = {"low": 1 / 3, "mid": 1 / 3, "high": 1 / 3}

    def _sample_group(group_df: pd.DataFrame, target: int) -> pd.DataFrame:
        pieces = []
        for tier, tf in TIER_FRAC.items():
            tier_df = group_df[group_df["_tier"] == tier]
            for period, pf in PERIOD_FRAC.items():
                per_df = tier_df[tier_df["_period"] == period]
                for pt_b, bf in PT_FRAC.items():
                    cell = per_df[per_df["_pt_band"] == pt_b]
                    if cell.empty:
                        continue
                    n = max(1, round(target * tf * pf * bf))
                    if tier == "high":
                        picks = cell.nlargest(min(n, len(cell)), SCORE_COL)
                    elif tier == "low":
                        picks = cell.nsmallest(min(n, len(cell)), SCORE_COL)
                    else:
                        picks = cell.sample(min(n, len(cell)), random_state=SEED)
                    pieces.append(picks)

        if not pieces:
            return group_df.head(target)

        result = pd.concat(pieces).drop_duplicates()

        # Fill shortfall randomly from remaining pool
        if len(result) < target:
            pool   = group_df.drop(index=result.index, errors="ignore")
            needed = min(target - len(result), len(pool))
            if needed > 0:
                result = pd.concat([result, pool.sample(needed, random_state=SEED + 1)])

        return result.head(target)

    pos_sampled = _sample_group(df[df["voted_up"] == True],  n_pos_target)
    neg_sampled = _sample_group(df[df["voted_up"] == False], n_neg_target)

    sampled = (
        pd.concat([pos_sampled, neg_sampled])
        .sort_values(SCORE_COL, ascending=False)
        .reset_index(drop=True)
    )

    # ── Sampling report ──
    def _ts2date(ts):
        try:    return _dt.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
        except: return "?"

    actual_pos     = int(sampled["voted_up"].sum())
    actual_pos_pct = actual_pos / len(sampled) * 100

    period_counts = (
        sampled["_period"]
        .value_counts()
        .reindex(["early", "mid", "recent"], fill_value=0)
    )
    pt_counts = (
        sampled["_pt_band"]
        .value_counts()
        .reindex(["low", "mid", "high"], fill_value=0)
    )

    print(f"\nStep 2/3: Sampling {top_n:,} reviews with weighted strategy...")
    print(f"  전체 리뷰 수          : {len(df):,}")
    print(f"  전체 pos ratio        : {pos_ratio*100:.1f}%")
    print(f"  ─────────────────────────────────────────")
    print(f"  샘플 pos ratio        : {actual_pos_pct:.1f}%  (목표 {pos_ratio*100:.1f}%)")
    print(f"    Positive (REC)      : {actual_pos:,}")
    print(f"    Negative (NOT REC)  : {len(sampled) - actual_pos:,}")
    print(f"  시기 분포:")
    print(f"    Early  (~ {_ts2date(tq33)})  : {period_counts['early']:,}")
    print(f"    Mid    ({_ts2date(tq33)} ~ {_ts2date(tq67)}) : {period_counts['mid']:,}")
    print(f"    Recent ({_ts2date(tq67)} ~)  : {period_counts['recent']:,}")
    print(f"  플레이타임 분포:")
    print(f"    <50h   : {pt_counts['low']:,}")
    print(f"    50~200h: {pt_counts['mid']:,}")
    print(f"    200h+  : {pt_counts['high']:,}")

    return sampled.drop(columns=["_period", "_tier", "_pt_band"])


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_steam_rating(pos_ratio: float) -> str:
    for threshold, label in STEAM_RATINGS:
        if pos_ratio >= threshold:
            return label
    return "Very Negative"


def compute_stats(df: pd.DataFrame, game_name: str) -> dict:
    total  = len(df)
    n_pos  = int(df["voted_up"].sum())
    ratio  = n_pos / total * 100 if total else 0.0
    return {
        "game_name":      game_name,
        "total_analyzed": total,
        "n_positive":     n_pos,
        "n_negative":     total - n_pos,
        "positive_ratio": round(ratio, 1),
        "steam_rating":   get_steam_rating(ratio),
    }


# ---------------------------------------------------------------------------
# Review text builder
# ---------------------------------------------------------------------------

def build_review_block(
    df: pd.DataFrame,
    max_reviews: int = MAX_REVIEWS_PER_CALL,
    max_chars: int   = MAX_CHARS_PER_REVIEW,
) -> str:
    lines = []
    for _, row in df.head(max_reviews).iterrows():
        tag  = "RECOMMENDED" if row.get("voted_up") else "NOT RECOMMENDED"
        text = str(row.get("review", ""))[:max_chars].replace("\n", " ")
        lines.append(f"[{tag}] {text}")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------

def call_claude(
    client: anthropic.Anthropic,
    prompt: str,
    max_tokens: int,
    tracker: UsageTracker,
) -> str:
    delays = [0] + API_RETRY_DELAYS
    last_exc = None
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            tracker.add(response.usage)
            time.sleep(API_CALL_GAP)
            return response.content[0].text
        except anthropic.RateLimitError as exc:
            last_exc = exc
            if attempt < len(API_RETRY_DELAYS):
                print(f"  Rate limit — waiting {API_RETRY_DELAYS[attempt]}s...")
        except anthropic.APIError as exc:
            last_exc = exc
            if attempt < len(API_RETRY_DELAYS):
                print(f"  API error (attempt {attempt + 1}): {exc} — retrying...")
    raise RuntimeError(f"All API retry attempts failed: {last_exc}")


def parse_pros_cons(raw: str) -> tuple:
    """Extract pros/cons lists from a JSON response."""
    raw = raw.strip()
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start == -1 or end == 0:
        return [], []
    try:
        data = json.loads(raw[start:end])
        pros = [str(x) for x in data.get("pros", [])]
        cons = [str(x) for x in data.get("cons", [])]
        return pros, cons
    except json.JSONDecodeError:
        return [], []


def run_pros_cons(
    client: anthropic.Anthropic,
    df: pd.DataFrame,
    stats: dict,
    tracker: UsageTracker,
) -> tuple:
    """
    Step 1: Extract pros and cons.
    If >100 reviews, batch-process and merge results.
    """
    print("Calling Claude API: Pros and Cons...")

    game  = stats["game_name"]
    ratio = stats["positive_ratio"]
    total = len(df)

    def _single_call(batch_df: pd.DataFrame) -> tuple:
        n            = min(len(batch_df), MAX_REVIEWS_PER_CALL)
        review_block = build_review_block(batch_df)
        prompt = (
            f"Below are {n} English Steam reviews for {game}. "
            f"Positive ratio: {ratio:.1f}%.\n\n"
            "Analyze these reviews and extract:\n"
            "1. The 5-7 most frequently mentioned positive aspects (Pros)\n"
            "2. The 4-5 most frequently mentioned negative aspects (Cons)\n\n"
            "Each item should be:\n"
            "- One concise sentence in English\n"
            "- Specific and actionable (not vague)\n\n"
            "Return as JSON only, no other text:\n"
            "{\n"
            '  "pros": ["...", "..."],\n'
            '  "cons": ["...", "..."]\n'
            "}\n\n"
            f"[Review data]\n{review_block}"
        )
        raw = call_claude(client, prompt, max_tokens=1024, tracker=tracker)
        return parse_pros_cons(raw)

    if total <= MAX_REVIEWS_PER_CALL:
        return _single_call(df)

    # Batch mode: split into chunks, collect all results, then merge
    n_batches = (total + MAX_REVIEWS_PER_CALL - 1) // MAX_REVIEWS_PER_CALL
    all_pros, all_cons = [], []

    for i in range(n_batches):
        batch = df.iloc[i * MAX_REVIEWS_PER_CALL:(i + 1) * MAX_REVIEWS_PER_CALL]
        p, c  = _single_call(batch)
        all_pros.extend(p)
        all_cons.extend(c)

    # Consolidation call
    print(f"  Consolidating {n_batches} batches...")
    pros_text = "\n".join(f"- {x}" for x in all_pros)
    cons_text = "\n".join(f"- {x}" for x in all_cons)
    prompt = (
        f"Multiple batches of {game} reviews produced these raw findings:\n\n"
        f"Pros (raw):\n{pros_text}\n\nCons (raw):\n{cons_text}\n\n"
        "Consolidate into the 5-7 most important pros and 4-5 most important cons. "
        "Merge duplicates, keep each item as one concise specific sentence.\n\n"
        "Return as JSON only:\n"
        "{\n"
        '  "pros": ["...", "..."],\n'
        '  "cons": ["...", "..."]\n'
        "}"
    )
    raw = call_claude(client, prompt, max_tokens=1024, tracker=tracker)
    merged_pros, merged_cons = parse_pros_cons(raw)
    return (merged_pros or all_pros[:7]), (merged_cons or all_cons[:5])


def run_verdict(
    client: anthropic.Anthropic,
    pros: list,
    cons: list,
    stats: dict,
    tracker: UsageTracker,
) -> str:
    """Step 2: Generate analyst verdict."""
    print("Calling Claude API: Analyst Verdict...")

    game  = stats["game_name"]
    ratio = stats["positive_ratio"]

    pros_text = "\n".join(f"- {x}" for x in pros)
    cons_text = "\n".join(f"- {x}" for x in cons)

    prompt = (
        f"Based on the following analysis of {game} Steam reviews:\n\n"
        f"Pros:\n{pros_text}\n\n"
        f"Cons:\n{cons_text}\n\n"
        f"Positive ratio: {ratio:.1f}%\n\n"
        "Write an 'Analyst Verdict' - a 4-5 sentence professional analysis that:\n"
        "- Summarizes the game's core strengths and weaknesses\n"
        "- Identifies the target audience\n"
        "- Notes any trade-offs in design philosophy\n"
        "- Concludes with whether the game is worth playing and for whom\n\n"
        "Write in English, professional tone, like a game analyst report.\n"
        "Output only the verdict text, no preamble."
    )
    return call_claude(client, prompt, max_tokens=512, tracker=tracker)


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def _he(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _rating_color(pos_ratio: float) -> str:
    if pos_ratio >= 80: return "#66c0f4"
    if pos_ratio >= 40: return "#c1b04b"
    return "#c2504a"


def _list_items(items: list, marker_class: str) -> str:
    if not items:
        return '<li><span class="marker ' + marker_class + '">▸</span>No data.</li>'
    rows = []
    for item in items:
        rows.append(
            f'    <li>'
            f'<span class="marker {marker_class}">&#9658;</span>'
            f'{_he(str(item))}'
            f'</li>'
        )
    return "\n".join(rows)


def generate_html(
    stats: dict,
    pros: list,
    cons: list,
    verdict: str,
    metadata: dict,
    slug: str = None,
    use_external_css: bool = False,
) -> str:
    game   = stats["game_name"]
    total  = stats["total_analyzed"]
    ratio  = stats["positive_ratio"]
    rating = stats["steam_rating"]
    r_col  = _rating_color(ratio)
    gen_dt = metadata.get("generated_at", "")[:10]
    cost   = metadata.get("api_cost_usd", 0.0)

    pros_html    = _list_items(pros, "pros")
    cons_html    = _list_items(cons, "cons")
    verdict_html = _he(verdict).replace("\n\n", "</p><p>").replace("\n", " ")

    # Head
    head = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f'  <meta name="description" content="Steam review analysis of {_he(game)} — {rating} ({ratio:.1f}% positive)">',
        f'  <meta property="og:title" content="{_he(game)} – Steam Review Analysis">',
        f'  <meta property="og:description" content="{rating} · {ratio:.1f}% positive · {total:,} reviews analyzed">',
        f'  <title>{_he(game)} – Steam Review Analysis</title>',
    ]
    if use_external_css:
        head.append('  <link rel="stylesheet" href="assets/style.css">')
    else:
        head += ["  <style>", CSS, "  </style>"]
    head += ["</head>", "<body>"]

    # Nav bar (only in slug / reports/ mode)
    nav = []
    if use_external_css:
        nav = [
            '  <nav class="nav-bar">',
            '    <a href="index.html" class="nav-back">&#8592; Back to Dashboard</a>',
            f'    <span class="nav-game">{_he(game)}</span>',
            '  </nav>',
        ]

    body = [
        '  <div class="container">',
        # Header
        '    <div class="header">',
        '      <div class="header-left">',
        f'        <div class="game-name">{_he(game)}</div>',
        '        <div class="subtitle">Steam Review Analysis</div>',
        "      </div>",
        '      <div class="header-right">',
        f'        <div class="steam-rating" style="color:{r_col}">{rating}</div>',
        f'        <div class="review-count">{total:,} reviews analyzed</div>',
        "      </div>",
        "    </div>",
        # Pros
        '    <div class="section">',
        '      <div class="section-heading pros">Pros</div>',
        '      <ul class="item-list">',
        pros_html,
        "      </ul>",
        "    </div>",
        # Cons
        '    <div class="section">',
        '      <div class="section-heading cons">Cons</div>',
        '      <ul class="item-list">',
        cons_html,
        "      </ul>",
        "    </div>",
        # Analyst Verdict
        '    <div class="section">',
        '      <div class="section-heading verdict">Analyst Verdict</div>',
        f'      <div class="verdict-body"><p>{verdict_html}</p></div>',
        "    </div>",
        # Footer
        '    <div class="footer">',
        f'      Generated by Claude API ({MODEL}) &nbsp;|&nbsp; '
        f'Reviews analyzed: {total:,} &nbsp;|&nbsp; '
        f'Positive: {ratio:.1f}% &nbsp;|&nbsp; '
        f'Est. cost: ${cost:.4f} &nbsp;|&nbsp; {gen_dt}',
        "    </div>",
        "  </div>",
        "</body>",
        "</html>",
    ]

    return "\n".join(head + nav + body)


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def generate_json(stats: dict, pros: list, cons: list, verdict: str, metadata: dict) -> dict:
    return {
        "game_name":       stats["game_name"],
        "total_analyzed":  stats["total_analyzed"],
        "positive_ratio":  stats["positive_ratio"],
        "steam_rating":    stats["steam_rating"],
        "pros":            pros,
        "cons":            cons,
        "analyst_verdict": verdict,
        "metadata":        metadata,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    # API key
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "Error: Anthropic API key not found.\n"
            "Set ANTHROPIC_API_KEY environment variable or use --api-key.",
            file=sys.stderr,
        )
        sys.exit(1)

    game_name = args.game_name or Path(args.csv_file).stem.replace("_", " ").title()
    start_time = time.time()

    # ── Output path resolution ──
    use_external_css = False
    if args.slug:
        reports_dir = Path("reports")
        reports_dir.mkdir(exist_ok=True)
        html_path        = reports_dir / f"{args.slug}.html"
        json_path        = reports_dir / f"{args.slug}.json"
        use_external_css = True
        print(f"Slug mode: output → {html_path}")
    elif args.output:
        html_path = Path(args.output)
        json_path = html_path.with_suffix(".json")
    else:
        html_path = Path("review_analysis.html")
        json_path = html_path.with_suffix(".json")

    # ── Data pipeline ──
    df_filtered, full_pos_ratio = load_and_filter(args.csv_file)
    df_sampled                  = stratified_sample(df_filtered, args.top_n, full_pos_ratio)
    stats                       = compute_stats(df_sampled, game_name)

    # ── Claude API ──
    client  = anthropic.Anthropic(api_key=api_key)
    tracker = UsageTracker()

    pros, cons = run_pros_cons(client, df_sampled, stats, tracker)
    verdict    = run_verdict(client, pros, cons, stats, tracker)

    # ── Outputs ──
    elapsed  = time.time() - start_time
    metadata = {
        "input_file":   str(args.csv_file),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "api_cost_usd": round(tracker.cost_usd, 6),
        "input_tokens": tracker.input_tokens,
        "output_tokens": tracker.output_tokens,
    }

    print("Generating HTML...")
    html_path.write_text(
        generate_html(stats, pros, cons, verdict, metadata,
                      slug=args.slug, use_external_css=use_external_css),
        encoding="utf-8",
    )

    json_path.write_text(
        json.dumps(generate_json(stats, pros, cons, verdict, metadata),
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Done! Saved to {html_path}")
    print(f"      JSON  → {json_path}")
    if use_external_css:
        print(f"      Run `python publish.py` to update the dashboard index.")
    print(f"\nToken usage:")
    tracker.print_summary()
    print(f"  Time   : {elapsed:.1f}s")


if __name__ == "__main__":
    main()
