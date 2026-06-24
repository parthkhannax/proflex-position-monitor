#!/usr/bin/env python3
"""
Close a Proflex position — moves it from positions.json to closed_positions.json
with the closing details needed to compute realized returns.

Usage:
  python3 close_position.py <id> [--price X] [--date YYYY-MM-DD] [--reason "..."]

Examples:
  # Expired worthless (kept full premium) — the common winning close
  python3 close_position.py nvda-short-put-jul17 --reason "expired worthless"

  # Bought back to close at $1.20 per share
  python3 close_position.py aapl-covered-call-aug21 --price 1.20 --reason "bought to close"

Notes:
  --price  is the option's per-share buyback price (what you paid to close).
           Omit or 0 = expired/assigned worthless, full premium kept.
  --date   defaults to today (UTC).
  The dashboard computes P/L, absolute return %, and simple annualized return
  from these fields, so no return math is stored here.
"""

import argparse
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
POSITIONS_FILE = BASE_DIR / "positions.json"
CLOSED_FILE = BASE_DIR / "closed_positions.json"


def main():
    ap = argparse.ArgumentParser(description="Close a Proflex position and archive it with realized-return inputs.")
    ap.add_argument("id", help="Position id from positions.json (e.g. nvda-short-put-jul17)")
    ap.add_argument("--price", type=float, default=0.0, help="Option buyback price per share (default 0 = expired worthless)")
    ap.add_argument("--date", default=date.today().isoformat(), help="Close date YYYY-MM-DD (default: today)")
    ap.add_argument("--reason", default="closed", help="Close reason note (e.g. 'expired worthless', 'bought to close', 'rolled')")
    ap.add_argument("--push", action="store_true", help="git add/commit/push both JSON files after closing")
    args = ap.parse_args()

    positions_doc = json.loads(POSITIONS_FILE.read_text())
    closed_doc = json.loads(CLOSED_FILE.read_text()) if CLOSED_FILE.exists() else {"closed": []}

    open_positions = positions_doc.get("positions", [])
    match = next((p for p in open_positions if p.get("id") == args.id), None)
    if match is None:
        ids = ", ".join(p.get("id", "?") for p in open_positions)
        raise SystemExit(f"No open position with id '{args.id}'. Open ids: {ids}")

    # Remove from open, enrich with close details, append to closed.
    positions_doc["positions"] = [p for p in open_positions if p.get("id") != args.id]
    closed_entry = {
        **match,
        "close_date": args.date,
        "close_price": round(args.price, 2),
        "close_reason": args.reason,
        "closed_at": datetime.now(timezone.utc).isoformat(),
    }
    closed_doc.setdefault("closed", []).append(closed_entry)

    POSITIONS_FILE.write_text(json.dumps(positions_doc, indent=2) + "\n")
    CLOSED_FILE.write_text(json.dumps(closed_doc, indent=2) + "\n")

    # Quick echo of the realized P/L so the operator sees it immediately.
    pl = (match["premium_collected"] - args.price) * 100 * match.get("contracts", 1)
    print(f"✓ Closed {match['ticker']} {match['strategy']} (id={args.id})")
    print(f"  Premium collected: ${match['premium_collected']:.2f}  |  Buyback: ${args.price:.2f}")
    print(f"  Realized P/L: ${pl:,.2f}  ({args.reason})")
    print(f"  Moved to closed_positions.json — dashboard 'Closed' & 'Dashboard' tabs will reflect it.")

    if args.push:
        try:
            subprocess.run(["git", "add", "positions.json", "closed_positions.json"], cwd=BASE_DIR, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"positions: close {match['ticker']} {match['strategy']}"],
                cwd=BASE_DIR, check=True,
            )
            subprocess.run(["git", "push"], cwd=BASE_DIR, check=True)
            print("✓ Committed and pushed.")
        except subprocess.CalledProcessError as e:
            print(f"  [git ERROR] {e}")


if __name__ == "__main__":
    main()
