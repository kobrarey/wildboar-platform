from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from decimal import Decimal

from app.navcalc.exceptions import FundDisabledError, NavCalcError
from app.navcalc.fund_registry import require_runnable_fund
from app.navcalc.portfolio_nav import compute_nav


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fund-code", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        cfg = require_runnable_fund(args.fund_code)
    except FundDisabledError as exc:
        print(f"[SKIP] {exc}")
        return 0
    except NavCalcError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        result = compute_nav(cfg)
    except NavCalcError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: unexpected failure: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, default=_json_default))
    else:
        print(
            f"[OK] fund={result.fund_code} "
            f"nav_usd={result.nav_usd:.2f} "
            f"snapshot_ts={result.snapshot_ts.isoformat()} "
            f"sanity_check_passed={result.sanity_check_passed}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())