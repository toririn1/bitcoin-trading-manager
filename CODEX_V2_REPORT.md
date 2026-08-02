# CODEX V2 decision-loop report

## Scope

This branch, codex/v2-decision-loop, turns the previous live-core scaffold into a research/shadow decision loop. It preserves the existing FastAPI/UI and user worktree files while making the V2 contracts explicit.

## Implemented now

- ProductSpec has role, execution_venue, and market_data_provider. yfinance products are role=reference, is_tradable=false, and never enter the opportunity scanner.
- Only venue-discovered or explicitly seeded role=tradable products are scanned. Perpetuals can produce long/short candidates; spot products produce long/no-trade only.
- Candidates expose candidate_status, valid_for_shadow, valid_for_user_execution, execution_permission, setup_type, entry_plan, trigger_price, stop_price, target_price, time_expiry, and invalidation_reason.
- Directional candidates are generated as research_only_long/short when data exists but cost/edge/calibration/action gates are incomplete. actionable_long/short requires a complete deterministic plan, configured/observed costs, calibrated edge, guard clearance, and the minimum RR. No automatic order path exists.
- BTC venue fee schedules are explicit product configuration. Missing spread/fee inputs block action but do not erase a research candidate.
- Replay consumes the actual candidate trigger/stop/target plan and records trigger, fill, exit, cost, funding, MFE, and MAE.
- Shadow runner persists open candidates, waits for future candles, closes filled/not-triggered/expired outcomes, stores calibration groups, and has an in-process plus stale-PID recovery lock.
- Calibration is grouped by product, direction, setup, horizon, and regime. Results remain insufficient_sample until the configured sample minimum; only calibrated groups feed edge gating.
- Cross-asset alignment uses UTC floor, nearest matching tolerance, explicit session filtering, overlap, historical usability, and current confirmation metadata. The decision feature path uses one explicit 15m series and live collection can backfill 5m/15m/1h/4h/1d.
- SOXL stale checks use underlying_price_stale. Hynix underlying_close_age and usd_krw_age are separate.
- Parquet writes are grouped by provider/type/date batch and written temp-file then atomic rename. Database payload hashes make restart writes idempotent.
- Bybit, Gate Futures, Gate Stock/CFD, KIS, CoinGlass, and official-events capability declarations match implemented endpoints. CoinGlass liquidation, heatmap, futures/spot CVD, OI, and basis connectors are opt-in; funding and ETF endpoints remain plan-not-available. Official BLS and BEA connectors are available when enabled/configured; Fed and OpenDART remain plan-not-available.

## State contract

The decision final_action is one of:

- actionable_long, actionable_short
- research_only_long, research_only_short
- no_trade
- data_unavailable

execution_permission is derived from the selected candidate (manual_confirmation_required, shadow_only, no_trade, or data_unavailable) and is no longer a constant.

## Verification performed

- Python compileall passes for engine_v2.
- Core V2 semantic tests pass except for the intentionally updated Parquet assertion before the final full run.
- Fixture smoke produces both long and short directional research candidates with non-null trigger/stop/target and no trigger_missing replay failure.
- Shadow fixture harness closed future-candle candidates into outcome rows and returned insufficient_sample.
- Cross-asset nearest UTC alignment matched offset timestamps within the configured 15m tolerance.
- Provider capability smoke confirms Bybit/Gate/CoinGlass/official-events declarations are honest.

The final test command and count are recorded below after the last verification run.

## Remaining partial/stub boundaries

- Gate Stock/CFD, KIS live, and official event calendar connectors are status-only.
- CoinGlass currently has actual liquidation-order history only; other endpoint statuses are plan-not-available.
- Gate Futures currently has product discovery and candles only.
- Bybit currently has product discovery, candles, recent trades, and open interest only.
- Official events need OFFICIAL_EVENTS_ENABLED=true and BEA_API_KEY for BEA; BLS is public.
- Websocket sequence/reconnect is not part of this branch.
- Calibration is expanding-sample metadata; production walk-forward promotion still needs a larger historical outcome set.

## Run

V2_MODE=live V2_LIVE_ENABLED=true .venv/bin/uvicorn server:app --reload
V2_MODE=fixture .venv/bin/python -m pytest tests/test_engine_v2_semantics.py

V2 never creates, modifies, cancels, or submits an order.
