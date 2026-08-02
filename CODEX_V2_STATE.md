# CODEX V2 state

## Completed

- Created branch codex/v2-market-engine from the WSL repository at /home/toririn/bitcoin-trading-manager.
- Preserved the pre-V2 worktree and untracked list under artifacts/.
- Added engine_v2 domain, registry, settings, source priority, provider health, PIT timestamps, raw/feature/decision storage, read-only providers, features, events, opportunities, intelligence, evaluation, and API layers.
- Added Binance, Bybit, Gate Futures, and Deribit public read-only paths; Gate Stock/CFD, KIS, CoinGlass, official events, and manual intake report explicit unsupported or authentication boundaries.
- Connected additive /api/v2 routes and an additive V2 dashboard panel without deleting legacy FastAPI/UI/account/history/settings surfaces.
- Removed legacy private force-close liquidation semantics and the 80000.0 price fallback. Legacy names now distinguish taker_bucket_* and estimated_skew_proxy from actual trade_cvd and actual-delta rr_25d.
- Disabled legacy Bull/Bear debate and text/agent memory recall/write by default. They can be explicitly enabled with environment variables.
- Removed credential-like values from .env.example. If the old values were real credentials, revoke and reissue them outside the repository.

## Verification

- .venv/bin/python -m pytest -q -s: 117 passed, 5 warnings, 5 subtests passed.
- python3 -m compileall -q on V2, legacy touched modules, and server.py: passed.
- Node.js syntax check on the two real inline UI scripts: passed.
- V2 semantic/API subset: 12 passed.
- V2_LIVE_ENABLED=true public REST smoke: 1,702 observations, partial quality, 9 candidates. Binance, Bybit, Gate Futures, and Deribit public paths returned successfully.
- Live provider health distinguishes disabled, unsupported, and authentication_required results.
- DuckDB/PyArrow are not installed in the current environment, so storage explicitly used SQLite/JSONL fallback.

## Intentional limits

- Default execution is deterministic fixture mode; it is not a live-market fallback.
- SOXL and SK Hynix symbols are not guessed; they remain waiting for authoritative discovery/configuration.
- Gate Stock/CFD, KIS, CoinGlass, and official event calendar require account, region, plan, or endpoint configuration.
- REST backfill is verified; websocket sequence/reconnect operations and long-running outcome ingestion are next-stage work.
- No V2 route or provider adapter calls order create, cancel, modify, leverage, transfer, or withdrawal operations.
