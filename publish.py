#!/usr/bin/env python3
"""
publish.py — Rebuild reports/index.html and push to GitHub Pages.

Usage:
    python publish.py                       # update index + commit + push
    python publish.py --dry-run             # update index only, no git
    python publish.py --message "Add Elden Ring"   # custom commit message
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPORTS_DIR = Path("reports")
INDEX_PATH  = REPORTS_DIR / "index.html"

# Mapping from steam_rating string → CSS badge class
BADGE_CLASS = {
    "Overwhelmingly Positive": "overwhelmingly-positive",
    "Very Positive":           "very-positive",
    "Mostly Positive":         "mostly-positive",
    "Mixed":                   "mixed",
    "Mostly Negative":         "mostly-negative",
    "Very Negative":           "very-negative",
    "Overwhelmingly Negative": "overwhelmingly-negative",
}


# ---------------------------------------------------------------------------
# Game scanning
# ---------------------------------------------------------------------------

def scan_games() -> list[dict]:
    """Read reports/*.json and return a list of game metadata dicts."""
    games = []
    for json_file in sorted(REPORTS_DIR.glob("*.json")):
        slug = json_file.stem
        html_file = REPORTS_DIR / f"{slug}.html"
        if not html_file.exists():
            print(f"  [skip] {json_file.name} — no matching {slug}.html")
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  [warn] Could not parse {json_file.name}: {exc}")
            continue

        # Support both analyze.py JSON format and the slim index-metadata format
        total = (
            data.get("total_analyzed")
            or data.get("total_reviews_analyzed")
            or 0
        )
        generated_at = (
            data.get("generated_at")
            or data.get("metadata", {}).get("generated_at", "")
        )

        games.append({
            "slug":         slug,
            "game_name":    data.get("game_name", slug),
            "steam_rating": data.get("steam_rating", "Unknown"),
            "positive_ratio": float(data.get("positive_ratio", 0)),
            "total_analyzed": int(total),
            "generated_at": generated_at,
        })

    return games


# ---------------------------------------------------------------------------
# Index HTML generator
# ---------------------------------------------------------------------------

def _badge_class(rating: str) -> str:
    return BADGE_CLASS.get(rating, "mixed")


def _card(game: dict) -> str:
    slug        = game["slug"]
    name        = game["game_name"].replace("&", "&amp;").replace("<", "&lt;")
    rating      = game["steam_rating"]
    ratio       = game["positive_ratio"]
    total       = game["total_analyzed"]
    badge_cls   = _badge_class(rating)
    total_fmt   = f"{total:,}" if total else "—"
    ratio_fmt   = f"{ratio:.1f}%"

    return f"""\
      <a class="game-card" href="{slug}.html">
        <span class="card-badge {badge_cls}">{rating}</span>
        <div class="card-game-name">{name}</div>
        <div class="card-stats">
          <div class="card-stat">
            <span class="card-stat-label">Positive</span>
            <span class="card-stat-value">{ratio_fmt}</span>
          </div>
          <div class="card-stat">
            <span class="card-stat-label">Analyzed</span>
            <span class="card-stat-value">{total_fmt}</span>
          </div>
        </div>
        <span class="card-btn">View Analysis &rarr;</span>
      </a>"""


def generate_index(games: list[dict]) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    n       = len(games)

    if games:
        cards_html = "\n".join(_card(g) for g in games)
    else:
        cards_html = (
            '      <div class="empty-state">'
            "No analyses found yet. Run <code>python analyze.py ... --slug &lt;name&gt;</code> to add one."
            "</div>"
        )

    game_rows = "\n".join(
        f"      <tr><td>{g['game_name']}</td>"
        f"<td>{g['steam_rating']}</td>"
        f"<td>{g['positive_ratio']:.1f}%</td>"
        f"<td><a href=\"{g['slug']}.html\">/reports/{g['slug']}.html</a></td></tr>"
        for g in games
    )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="Steam review analyses for {n} games, powered by Claude API">
  <meta property="og:title" content="Steam Review Analysis Dashboard">
  <title>Steam Review Analysis Dashboard</title>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <div class="dashboard">

    <header class="dash-header">
      <div class="dash-title">Steam Review Analysis Dashboard</div>
      <div class="dash-subtitle">Game review analysis powered by Claude API &nbsp;|&nbsp; {n} game{"s" if n != 1 else ""} analyzed</div>
    </header>

    <div class="card-grid">
{cards_html}
    </div>

    <footer class="dash-footer">
      Last updated: {now_str}<br>
      Powered by <a href="https://www.anthropic.com/claude" style="color:#66c0f4">Claude API</a>
      &nbsp;|&nbsp; Steam review data via Steam Web API
    </footer>

  </div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  [error] {' '.join(cmd)}", file=sys.stderr)
        print(f"  stdout: {result.stdout.strip()}", file=sys.stderr)
        print(f"  stderr: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result


def run_git(message: str, dry_run: bool) -> None:
    # Check if we're in a git repo
    result = _run(["git", "rev-parse", "--git-dir"], check=False)
    if result.returncode != 0:
        print("  [warn] Not a git repository. Skipping git operations.")
        print("  Run `git init` and set up a remote to enable auto-deployment.")
        return

    # Stage reports/
    _run(["git", "add", "reports/"])

    # Check if there's anything to commit
    status = _run(["git", "status", "--porcelain", "reports/"])
    if not status.stdout.strip():
        print("  Nothing to commit in reports/ — already up to date.")
        return

    if dry_run:
        print(f"  [dry-run] Would commit: {message}")
        print(f"  Staged changes:\n{status.stdout.rstrip()}")
        return

    _run(["git", "commit", "-m", message])
    print(f"  Committed: {message}")

    # Push
    push = _run(["git", "push"], check=False)
    if push.returncode == 0:
        print("  Pushed to remote.")
    else:
        stderr = push.stderr.strip()
        if "no upstream" in stderr.lower() or "no remote" in stderr.lower():
            print("  [warn] No upstream configured. Set one with:")
            print("    git remote add origin https://github.com/{user}/{repo}.git")
            print("    git push -u origin main")
        else:
            print(f"  [warn] Push failed: {stderr}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rebuild index.html and deploy to GitHub Pages")
    p.add_argument("--dry-run",  action="store_true",
                   help="Regenerate index.html but skip git commit/push")
    p.add_argument("--message", default=None, metavar="MSG",
                   help="Custom git commit message")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if not REPORTS_DIR.exists():
        print(f"Error: {REPORTS_DIR}/ directory not found. Run analyze.py with --slug first.")
        sys.exit(1)

    print("Scanning games...")
    games = scan_games()
    print(f"  Found {len(games)} game(s): {', '.join(g['slug'] for g in games) or '(none)'}")

    print("Generating index.html...")
    INDEX_PATH.write_text(generate_index(games), encoding="utf-8")
    print(f"  Written: {INDEX_PATH}  ({INDEX_PATH.stat().st_size:,} bytes)")
    print(f"  Games in dashboard: {len(games)}")

    commit_msg = args.message or (
        f"Update dashboard: {', '.join(g['game_name'] for g in games)}"
        if games else "Update Steam review dashboard"
    )

    print("\nGit deployment...")
    run_git(commit_msg, dry_run=args.dry_run)

    # Try to show GitHub Pages URL
    remote = _run(["git", "remote", "get-url", "origin"], check=False)
    if remote.returncode == 0:
        url = remote.stdout.strip()
        if "github.com" in url:
            # Extract user/repo from https or ssh URL
            url = url.replace("git@github.com:", "https://github.com/")
            url = url.removesuffix(".git")
            parts = url.rstrip("/").split("/")
            if len(parts) >= 2:
                user, repo = parts[-2], parts[-1]
                pages_url = f"https://{user}.github.io/{repo}/reports/"
                print(f"\n  Live URL: {pages_url}")

    print("\nDone.")


if __name__ == "__main__":
    main()
