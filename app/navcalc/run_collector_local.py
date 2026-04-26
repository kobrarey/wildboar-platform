from __future__ import annotations

import argparse
import logging
import sys

from app.config import settings
from app.navcalc.collector import run_collector_forever
from app.navcalc.exceptions import FundDisabledError, NavCalcError
from app.navcalc.fund_registry import require_runnable_fund


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fund-code", required=True)
    parser.add_argument("--interval-sec", type=int, default=int(settings.NAV_POLL_INTERVAL_SEC))
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    try:
        cfg = require_runnable_fund(args.fund_code)
    except FundDisabledError as exc:
        print(f"[SKIP] {exc}")
        return 0
    except NavCalcError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        run_collector_forever(cfg, interval_sec=args.interval_sec)
    except KeyboardInterrupt:
        print("Collector stopped by user.")
        return 0
    except NavCalcError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: unexpected failure: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())