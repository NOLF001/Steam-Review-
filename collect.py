"""
Steam Review Collector
Collects all user reviews for a Steam game using the official Store API.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://store.steampowered.com/appreviews/{app_id}"
NUM_PER_PAGE = 100
DEFAULT_SLEEP = 1.5       # seconds between requests
SLEEP_ON_429 = 30         # seconds to wait on rate limit
MAX_429_RETRIES = 3
BACKOFF_BASE = 2          # seconds; doubles each 5xx retry
MAX_BACKOFF_RETRIES = 3
EMPTY_RETRIES = 5         # retries when reviews array is empty
CHECKPOINT_EVERY = 100    # pages

EXTRACT_FIELDS = [
    "recommendationid",
    "author_steamid",
    "author_playtime_forever_min",
    "author_playtime_at_review_min",
    "author_num_games_owned",
    "author_num_reviews",
    "language",
    "review",
    "timestamp_created",
    "timestamp_updated",
    "timestamp_created_dt",
    "timestamp_updated_dt",
    "voted_up",
    "votes_up",
    "votes_funny",
    "weighted_vote_score",
    "comment_count",
    "steam_purchase",
    "received_for_free",
    "written_during_early_access",
]

SUMMARY_FIELDS = [
    "num_reviews",
    "review_score",
    "review_score_desc",
    "total_positive",
    "total_negative",
    "total_reviews",
]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logger(appid: str) -> logging.Logger:
    log_path = Path(f"reviews_{appid}_errors.log")
    logger = logging.getLogger("steam_collector")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.WARNING)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.ERROR)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ---------------------------------------------------------------------------
# API layer
# ---------------------------------------------------------------------------

def build_params(cursor: str, language: str) -> dict:
    return {
        "json": 1,
        "filter": "recent",
        "language": language,
        "cursor": cursor,
        "review_type": "all",
        "purchase_type": "all",
        "num_per_page": NUM_PER_PAGE,
    }


def fetch_page(
    session: requests.Session,
    appid: str,
    cursor: str,
    language: str,
    logger: logging.Logger,
    consecutive_429: list,          # mutable counter passed in
) -> dict | None:
    """
    Fetch one page of reviews. Handles 429 and 5xx internally.
    Returns parsed JSON dict or None on unrecoverable error.
    """
    url = BASE_URL.format(app_id=appid)
    params = build_params(cursor, language)
    backoff = BACKOFF_BASE

    for attempt in range(MAX_BACKOFF_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            logger.error("Network error (attempt %d): %s", attempt + 1, exc)
            if attempt < MAX_BACKOFF_RETRIES:
                time.sleep(backoff)
                backoff *= 2
                continue
            return None

        if resp.status_code == 200:
            consecutive_429[0] = 0
            try:
                return resp.json()
            except ValueError as exc:
                logger.error("JSON decode error: %s", exc)
                return None

        if resp.status_code == 429:
            consecutive_429[0] += 1
            if consecutive_429[0] >= MAX_429_RETRIES:
                logger.error("429 received %d times consecutively. Stopping.", consecutive_429[0])
                return None
            print(
                f"\n  [Rate Limit] HTTP 429. Waiting {SLEEP_ON_429}s "
                f"(attempt {consecutive_429[0]}/{MAX_429_RETRIES})..."
            )
            time.sleep(SLEEP_ON_429)
            # Reset attempt loop for 429 (don't count against backoff retries)
            backoff = BACKOFF_BASE
            continue

        if 500 <= resp.status_code < 600:
            logger.warning("HTTP %d on attempt %d. Backing off %ds.", resp.status_code, attempt + 1, backoff)
            if attempt < MAX_BACKOFF_RETRIES:
                time.sleep(backoff)
                backoff *= 2
                continue
            logger.error("HTTP %d after %d retries. Giving up on this page.", resp.status_code, MAX_BACKOFF_RETRIES)
            return None

        logger.error("Unexpected HTTP %d. Aborting.", resp.status_code)
        return None

    return None


def fetch_summary(session: requests.Session, appid: str, language: str, logger: logging.Logger) -> dict:
    """Fetch query_summary from a single API call."""
    url = BASE_URL.format(app_id=appid)
    params = build_params("*", language)
    params["num_per_page"] = 0
    try:
        resp = session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("query_summary", {})
    except Exception as exc:
        logger.warning("Could not fetch summary: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Parsing layer
# ---------------------------------------------------------------------------

def unix_to_dt(ts: int | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_review(raw: dict) -> dict:
    author = raw.get("author", {})
    return {
        "recommendationid": raw.get("recommendationid"),
        "author_steamid": author.get("steamid"),
        "author_playtime_forever_min": author.get("playtime_forever"),
        "author_playtime_at_review_min": author.get("playtime_at_review"),
        "author_num_games_owned": author.get("num_games_owned"),
        "author_num_reviews": author.get("num_reviews"),
        "language": raw.get("language"),
        "review": raw.get("review", "").replace("\n", " ").replace("\r", " "),
        "timestamp_created": raw.get("timestamp_created"),
        "timestamp_updated": raw.get("timestamp_updated"),
        "timestamp_created_dt": unix_to_dt(raw.get("timestamp_created")),
        "timestamp_updated_dt": unix_to_dt(raw.get("timestamp_updated")),
        "voted_up": raw.get("voted_up"),
        "votes_up": raw.get("votes_up"),
        "votes_funny": raw.get("votes_funny"),
        "weighted_vote_score": raw.get("weighted_vote_score"),
        "comment_count": raw.get("comment_count"),
        "steam_purchase": raw.get("steam_purchase"),
        "received_for_free": raw.get("received_for_free"),
        "written_during_early_access": raw.get("written_during_early_access"),
    }


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------

def save_csv(rows: list[dict], path: Path) -> None:
    df = pd.DataFrame(rows, columns=EXTRACT_FIELDS)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def save_checkpoint(rows: list[dict], appid: str, output_path: Path) -> None:
    checkpoint_path = output_path.parent / f"reviews_{appid}_checkpoint.csv"
    save_csv(rows, checkpoint_path)
    print(f"  [Checkpoint] Saved {len(rows)} reviews → {checkpoint_path}")


def save_summary(summary: dict, appid: str) -> None:
    path = Path(f"reviews_{appid}_summary.json")
    filtered = {k: summary.get(k) for k in SUMMARY_FIELDS}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)
    print(f"  [Summary] Saved → {path}")


# ---------------------------------------------------------------------------
# Progress display
# ---------------------------------------------------------------------------

def make_progress_bar(total: int | None):
    """Returns a tqdm bar or a no-op stub."""
    if HAS_TQDM:
        return tqdm(total=total, unit="review", desc="Collecting", dynamic_ncols=True)
    return None


class _NoopBar:
    def update(self, n=1): pass
    def close(self): pass


# ---------------------------------------------------------------------------
# Main collection loop
# ---------------------------------------------------------------------------

def collect_reviews(
    appid: str,
    lang: str,
    output: str,
    max_pages: int | None,
) -> None:
    output_path = Path(output)
    logger = setup_logger(appid)
    start_time = time.time()

    session = requests.Session()
    session.headers.update({"User-Agent": "SteamReviewCollector/1.0"})

    # Fetch and save metadata
    print(f"\nFetching game summary for App ID {appid} ...")
    summary = fetch_summary(session, appid, lang, logger)
    if summary:
        save_summary(summary, appid)
        total_est = summary.get("total_reviews")
        print(f"  Game: {summary.get('review_score_desc', '?')} | "
              f"Total reviews: {total_est:,}" if total_est else "  (total unknown)")
    else:
        total_est = None

    bar = make_progress_bar(total_est) if HAS_TQDM else _NoopBar()

    cursor = "*"
    prev_cursor = None
    all_reviews: list[dict] = []
    page = 0
    consecutive_429 = [0]          # mutable list for pass-by-reference
    empty_streak = 0

    print(f"\nStarting collection | lang={lang} | output={output_path}\n")

    try:
        while True:
            if max_pages and page >= max_pages:
                print(f"\nReached --max-pages limit ({max_pages}). Stopping.")
                break

            elapsed = int(time.time() - start_time)
            cursor_preview = cursor[:20] + "..." if len(cursor) > 20 else cursor
            print(
                f"Page {page + 1:>5} | Cursor: {cursor_preview:<23} | "
                f"Collected: {len(all_reviews):>6} | Elapsed: {elapsed}s"
            )

            data = fetch_page(session, appid, cursor, lang, logger, consecutive_429)

            if data is None:
                # Unrecoverable error or too many 429s
                print("\nUnrecoverable error. Saving collected data and exiting.")
                break

            if data.get("success") != 1:
                logger.warning("API returned success!=1: %s", data)
                empty_streak += 1
                if empty_streak >= EMPTY_RETRIES:
                    print(f"\nAPI returned non-success {EMPTY_RETRIES} times. Stopping.")
                    break
                time.sleep(DEFAULT_SLEEP)
                continue

            reviews_raw = data.get("reviews", [])

            if not reviews_raw:
                empty_streak += 1
                if empty_streak >= EMPTY_RETRIES:
                    print(f"\nEmpty reviews array {EMPTY_RETRIES} times in a row. End of data.")
                    break
                time.sleep(DEFAULT_SLEEP)
                continue

            empty_streak = 0
            new_cursor = data.get("cursor", "")

            # Pagination termination condition
            if new_cursor and new_cursor == prev_cursor:
                print("\nCursor unchanged. Reached end of reviews.")
                break

            parsed = [parse_review(r) for r in reviews_raw]
            all_reviews.extend(parsed)

            if HAS_TQDM:
                bar.update(len(parsed))

            prev_cursor = cursor
            cursor = new_cursor
            page += 1

            # Checkpoint every N pages
            if page % CHECKPOINT_EVERY == 0:
                save_checkpoint(all_reviews, appid, output_path)

            time.sleep(DEFAULT_SLEEP)

    except KeyboardInterrupt:
        print("\n\n[Interrupted] Ctrl+C detected. Saving collected data...")

    finally:
        bar.close()
        _finalize(all_reviews, appid, output_path, start_time)


def _finalize(
    all_reviews: list[dict],
    appid: str,
    output_path: Path,
    start_time: float,
) -> None:
    elapsed = time.time() - start_time

    if not all_reviews:
        print("No reviews collected. No file written.")
        return

    save_csv(all_reviews, output_path)
    print(f"\n{'='*60}")
    print(f"  Final file saved → {output_path}")
    print(f"{'='*60}")

    df = pd.DataFrame(all_reviews)
    total = len(df)
    pos = df["voted_up"].sum() if "voted_up" in df.columns else 0
    neg = total - pos
    avg_pt = df["author_playtime_forever_min"].dropna().mean()

    print(f"  Total reviews collected : {total:,}")
    print(f"  Recommended (voted_up)  : {pos:,} ({pos/total*100:.1f}%)" if total else "")
    print(f"  Not recommended         : {neg:,} ({neg/total*100:.1f}%)" if total else "")
    print(f"  Avg playtime            : {avg_pt:.0f} min ({avg_pt/60:.1f} h)" if avg_pt else "  Avg playtime: N/A")
    print(f"  Total elapsed           : {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect all Steam user reviews for a given App ID.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python collect.py 489830
  python collect.py 489830 --lang all --output skyrim_reviews.csv
  python collect.py 292030 --lang korean --max-pages 100
        """,
    )
    parser.add_argument("appid", help="Steam App ID (e.g. 489830 for Skyrim SE)")
    parser.add_argument(
        "--lang",
        default="all",
        metavar="LANGUAGE",
        help="Review language filter: korean / english / all (default: all)",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Output CSV filename (default: reviews_{appid}.csv)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of pages to fetch (default: unlimited)",
    )

    args = parser.parse_args()
    appid = args.appid
    output = args.output or f"reviews_{appid}.csv"

    collect_reviews(
        appid=appid,
        lang=args.lang,
        output=output,
        max_pages=args.max_pages,
    )


if __name__ == "__main__":
    main()
