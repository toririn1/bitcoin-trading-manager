# CODEX V2 decision-loop report

## Scope

This branch, codex/v2-decision-loop, turns the previous live-core scaffold into a research/shadow decision loop. It preserves the existing FastAPI/UI and user worktree files while making the V2 contracts explicit.

## Implemented now

- ProductSpec has role, execution_venue, and market_data_provider. yfinance products are role=reference, is_tradable=false, and never enter the opportunity scanner.
- Only venue-discovered or explicitly seeded role=tradable products are scanned. Perpetuals can produce long/short candidates; spot products produce long/no-trade only.
- Candidate scans are globally re-ranked after all products are combined. The decision selector uses the same deterministic ranking, so registry/product order cannot outrank a better candidate.
- Equal score keys are resolved by product_id, direction, and setup_type ascending; candidate_id/UUID and input order are never tie-breakers.
- V2 route registration is import-safe: DuckDB, providers, directories, and tasks are created only in FastAPI startup and released in shutdown. run.sh is single-process by default; reload is opt-in via UVICORN_RELOAD=true.
- Candidates expose candidate_status, valid_for_shadow, valid_for_user_execution, execution_permission, setup_type, entry_plan, trigger_price, stop_price, target_price, time_expiry, and invalidation_reason.
- Directional candidates are generated as research_only_long/short when data exists but cost/edge/calibration/action gates are incomplete. A directional candidate must also clear the configured heuristic threshold (default 3.0) and data-quality gate before valid_for_shadow=true; otherwise no_trade wins and the weak candidate is not stored for calibration. actionable_long/short requires a complete deterministic plan, configured/observed costs, calibrated edge, guard clearance, and the minimum RR. No automatic order path exists.
- BTC venue fee schedules are explicit product configuration. Missing spread/fee inputs block action but do not erase a research candidate.
- Replay consumes the actual candidate trigger/stop/target plan and records trigger, fill, exit, cost, funding, MFE, and MAE.
- Shadow runner persists only shadow-eligible candidates, waits through the full candidate validity window, closes filled outcomes or terminal expiry outcomes, and leaves not_triggered candidates open before expiry. It deduplicates open candidates by product/direction/setup, stores calibration groups, and has an in-process plus stale-PID recovery lock.
- Calibration is grouped by product, direction, setup, horizon, and regime. Results remain insufficient_sample until the configured sample minimum; only calibrated groups feed edge gating.
- Cross-asset alignment uses UTC floor, nearest matching tolerance, explicit session filtering, and overlap denominators based on the filtered source/target counts. Current confirmation uses the latest matched pair timestamp, never an unmatched fresh row. The decision feature path uses one explicit 15m series and live collection can backfill 5m/15m/1h/4h/1d.
- SOXL stale checks use underlying_price_stale. Hynix underlying_close_age and usd_krw_age are separate.
- Parquet writes are grouped by provider/type/date batch and written temp-file then atomic rename. Database payload hashes make restart writes idempotent.
- Bybit, Gate Futures, Gate Stock/CFD, KIS, CoinGlass, and official-series capability declarations match the implemented/status-only boundaries. CoinGlass liquidation, heatmap, futures/spot CVD, OI, and basis connectors are opt-in; funding and ETF endpoints remain plan-not-available. BLS and BEA provide period observations only; release timestamps are unavailable, so they are not post-release event connectors. Fed, OpenDART, Gate Stock/CFD, and KIS live remain unavailable.

## State contract

The decision final_action is one of:

- actionable_long, actionable_short
- research_only_long, research_only_short
- no_trade
- data_unavailable

execution_permission is derived from the selected candidate (manual_confirmation_required, shadow_only, no_trade, or data_unavailable) and is no longer a constant.

## Verification performed

- Python compileall passes for engine_v2.
- Core V2 semantic and blocker tests pass, including expiry, open-candidate deduplication, heuristic no-trade gating, session-filtered overlap/freshness, and BLS/BEA timestamp semantics.
- Fixture smoke produces directional research candidates with deterministic plans, but the default heuristic threshold keeps weak fixture directions out of shadow and selects no_trade.
- Shadow fixture harness closes a genuinely triggered future-candle candidate into an outcome row and returns insufficient_sample; a future candle that misses the trigger leaves the candidate open before expiry.
- Cross-asset nearest UTC alignment matched offset timestamps within the configured 15m tolerance.
- Provider capability smoke confirms the live/status-only boundaries, including that the official-series provider refuses release-event fetches.

Final regression: TMPDIR=/tmp V2_DATA_DIR=/tmp/v2-lifecycle-full-20260803 V2_DUCKDB_PATH=/tmp/v2-lifecycle-full-20260803/engine.duckdb V2_PARQUET_ROOT=/tmp/v2-lifecycle-full-20260803/raw .venv/bin/python -m pytest -q -> 142 passed, 5 warnings, 5 subtests passed. compileall . also passed. Lifecycle tests cover import safety, route registration, startup/shutdown close, 503 before initialization, sequential clients, run.sh flags, and reload subprocess startup. A public-data live shadow one-shot returned data_unavailable=false, final_action=no_trade, execution_permission=no_trade, settled_outcomes=0, and open_shadow_candidates=0; SOXL/SK_HYNIX_KRX remained waiting_for_official_product_discovery.

## Remaining partial/stub boundaries

- Gate Stock/CFD and KIS live trading products are still status-only. SOXL and SK Hynix remain temporarily unavailable as actual trading products; their reference data/guards do not make them tradable. Official release-event/calendar connectors are also status-only.
- CoinGlass currently has actual liquidation-order history only; other endpoint statuses are plan-not-available.
- Gate Futures currently has product discovery and candles only.
- Bybit currently has product discovery, candles, recent trades, and open interest only.
- Optional economic series ingestion uses OFFICIAL_SERIES_ENABLED=true and BEA_API_KEY for BEA; BLS is public. It must not be used for post-release reaction timing because release timestamps are unavailable.
- Websocket sequence/reconnect is not part of this branch.
- Calibration is expanding-sample metadata; production walk-forward promotion still needs a larger historical outcome set.

## Run

V2_MODE=live V2_LIVE_ENABLED=true ./run.sh
UVICORN_RELOAD=true V2_MODE=live V2_LIVE_ENABLED=true ./run.sh
V2_MODE=fixture .venv/bin/python -m pytest tests/test_engine_v2_semantics.py

V2 never creates, modifies, cancels, or submits an order.
