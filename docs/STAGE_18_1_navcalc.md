# Stage 18.1 — navcalc local integration

## What is implemented
- app/navcalc module extracted from standalone NAV logic
- multi-fund registry for:
  - btc_fund
  - defi_sniper
  - wb10
  - wb_test
  - wb_defi
  - wb_web3
- wb_defi and wb_web3 are disabled by design on this stage
- no DB writes
- no server deploy
- no HTML changes

## NAV formula
NAV = cash + spot + funding + earn

Where:
- cash + spot use Bybit `coin.usdValue`
- `coin.usdValue` is assumed to already include MTM via `coin.equity`
- derivatives notional is NOT included in NAV

## Local run: one-shot
```bash
python -m app.navcalc.run_nav_once --fund-code wb_test
```

## Local run: collector
```bash
python -m app.navcalc.run_collector_local --fund-code wb_test
```

## Output files
Raw samples:
`data/nav_samples/wb_test_samples.jsonl`

Minute candles:
`data/nav_samples/wb_test_ohlc_1m.jsonl`

## Typical sanity-check error
Example:
```text
Sanity-check failed: cash+spot=..., uta_equity=..., diff_pct=..., tol_pct=...
```

## Disabled funds
- wb_defi
- wb_web3

They are intentionally disabled on Stage 18.1 until actual launch.