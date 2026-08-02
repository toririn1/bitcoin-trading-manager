# CODEX V2 final implementation report

## 1. Scope

Added a multi-asset, multi-source, point-in-time market engine while preserving the existing FastAPI, UI, read-only account/Gate, history, and settings contracts. The integration is additive; the existing application was not deleted.

## 2. Removed or replaced behavior

- Disabled the legacy Binance private force-close feed as market liquidation data.
- Removed the hardcoded 80000.0 BTC price fallback. Missing source price stops dependent calculations instead of inventing a value.
- Renamed the legacy taker bucket fields to taker_bucket_cumulative_24h and taker_bucket_delta_4h. Only actual trade stream data is trade_cvd.
- Options rr_25d is produced only when actual option Greek delta is available. The legacy proxy is named estimated_skew_proxy.
- Bull/Bear debate and text/agent memory recall/write are disabled by default and can be explicitly enabled with environment variables.

## 3. Implemented V2 capabilities

- Domain models for assets, products, observations, candles, trades, liquidation classes, events, opportunities, and decisions.
- Source priority, freshness/quality gates, explicit no_trade/data_unavailable states, and provider health.
- Point-in-time fields: source_event_time, source_publish_time, first_seen_at, collected_at, available_at, and processed_at.
- Raw JSONL partition storage, optional DuckDB/Parquet path, and explicit SQLite/JSONL fallback.
- Read-only Binance, Bybit, Gate Futures, and Deribit public adapters, plus explicit capability boundaries for Gate Stock/CFD, KIS, CoinGlass, official events, and manual intake.
- Closed-candle technicals, trade CVD, orderbook features, weighted open interest, funding/basis, actual-delta option surface, liquidation actual/partial/estimated separation, dynamic relationships, factors, regimes, and quality.
- Event normalization, deduplication, status/impact fields, deterministic fees/slippage/funding/borrow/FX cost, opportunity scoring, product guards, and portfolio constraints.
- Structured snapshot, explanation-only LLM layer, claim critic, PIT replay, fills/outcome record, and calibration utilities.
- Additive FastAPI routes and a V2 UI panel.

## 4. Main changed files

- engine_v2/ contains the V2 package.
- server.py registers the V2 routes.
- static/index.html adds the V2 dashboard panel and polling.
- market_context.py, analysis_context.py, agents/situation_digest.py, decision_support.py, and decision_bridge.py separate legacy semantic names and sources.
- tests/test_engine_v2_semantics.py and tests/test_api_v2.py cover semantic, PIT, provider safety, and API smoke behavior.
- CODEX_V2_PLAN.md, CODEX_V2_SOURCE_AUDIT.md, CODEX_V2_SCHEMA.md, CODEX_V2_STATE.md, README.md, .env.example, and requirements.txt document operation.

## 5. Test results

Command:

    .venv/bin/python -m pytest -q -s

Result: 117 passed, 5 warnings, 5 subtests passed.

Additional compileall and Node.js inline JavaScript syntax checks passed. The V2 semantic/API subset passed with 12 tests.

## 6. Actual endpoint validation

With V2_LIVE_ENABLED=true, public REST backfill produced:

- 1,702 facts/observations.
- Aggregate quality partial.
- Missing quality reason: rest_orderbook_event_time_unavailable.
- 9 ranked candidates.
- Binance, Bybit, Gate Futures, and Deribit public paths succeeded and recorded health.
- Gate Stock/CFD/CoinGlass reported disabled or plan boundaries.
- KIS reported authentication_required.
- SOXL and SK Hynix remained waiting for authoritative product discovery.

No order, liquidation, leverage, transfer, or withdrawal call was made. The only V2 POST surface is /api/v2/events/manual-intake.

## 7. Fixture and fallback validation

The default live=false path is a deterministic fixture, not a live-market fallback. Forming candles are excluded from closed-candle features. Missing source timestamps become timestamp_unknown and are not replaced by collection time.

DuckDB and PyArrow are not installed in the current environment, so the storage layer explicitly reported SQLite/JSONL fallback. Installing the optional packages enables the configured DuckDB/Parquet path.

## 8. Remaining limitations and next steps

- Websocket sequence/reconnect and long-running raw retention operations are next-stage work.
- KIS, Gate Stock/CFD, and CoinGlass require account/region/plan configuration before live validation.
- Outcome persistence, walk-forward evaluation, and ablation are represented by evaluation utilities; a production long-running learning pipeline is separate work.
- Factor/cross-asset calculations are connected, but the current public smoke is BTC-centered, so relationships without enough overlap return insufficient_data.

## 9. Run instructions

    cp .env.example .env
    .venv/bin/uvicorn server:app --reload

For public live opt-in:

    V2_LIVE_ENABLED=true .venv/bin/uvicorn server:app --reload

Primary routes:

    /api/v2/status
    /api/v2/universe
    /api/v2/products
    /api/v2/provider-health
    /api/v2/data-health
    /api/v2/snapshot
    /api/v2/cross-asset
    /api/v2/factors
    /api/v2/events
    /api/v2/opportunities
    /api/v2/decision
    /api/v2/evaluation/summary
    /api/v2/evaluation/calibration

## 10. Rollback and safety

- V2 is controlled by ENGINE_V2_ENABLED, LEGACY_ENGINE_ENABLED, ENGINE_V2_SHADOW_MODE, and V2_LIVE_ENABLED.
- Pre-V2 files are recorded in artifacts/pre_v2_worktree.patch and artifacts/pre_v2_untracked_files.txt.
- Rollback can use git switch codex/decision-support-5.6-refactor or a deployment commit. No destructive reset was used.
- Credential-like values were blanked from the public .env.example. If those old values were real, revoke and reissue them outside GitHub.
