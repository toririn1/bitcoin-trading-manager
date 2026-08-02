# Change log

## 2026-07-11 KST

- Replaced decision-policy coupling with explicit market/setup/account/final layers.
- Added conservative-net fee overlay thresholds and fee/rebate transparency.
- Added conditional trigger extraction, R:R gating, resistance/entry-zone checks, and conflict-position action handling.
- Added Judge and Risk Triad structured market/setup/account/final fields and shared Decision Support forwarding.
- Preserved decisive Bull/Bear/Risk analysis while removing absolute account-block phrase collapse.
- Added deterministic seven-section Decision Support report block.
- Hardened Decision Support UI fallback formatting for null, missing, list, number, and long-text cases.
- Added performance evaluation/backfill support using existing public 5m OHLCV, with migration-safe null fallback.
- Added dedicated decision, trigger, fee, agent-layer, model-default, smoke, and backfill checks.

## Commands run

- `.venv/bin/python -m py_compile analyzer.py server.py market_context.py decision_support.py decision_bridge.py analysis_performance.py scripts/backfill_performance.py agents/judge.py agents/risk_triad.py agents/pipeline.py`
- `.venv/bin/python scripts/smoke_test.py`
- `.venv/bin/python -m pytest -q -s`
- Local `.venv/bin/python server.py` plus HTTP 200 checks for health, main, setup status, and account endpoints.
- `git diff --check`
- JavaScript syntax check over `static/index.html`.

## Results

- Smoke test: passed.
- Pytest: 105 passed; 4 FastAPI `on_event` deprecation warnings; 5 subtests passed.
- No commit created because existing dirty changes are interleaved.
