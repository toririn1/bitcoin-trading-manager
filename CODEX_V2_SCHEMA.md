# V2 schema contract

## Product

ProductSpec includes:

- role: tradable or reference
- execution_venue
- market_data_provider
- is_tradable
- short_supported
- explicit fee/slippage configuration fields

Reference products can provide factors and cross-asset context but are excluded from candidate generation.

## Candidate

OpportunityCandidate includes:

- candidate_status: research_only_long, research_only_short, actionable_long, actionable_short, no_trade, or data_unavailable
- valid_for_shadow (requires data quality and the configured heuristic threshold; only these candidates enter shadow storage)
- valid_for_user_execution
- execution_permission
- setup_type
- entry_plan
- trigger_price, stop_price, target_price, time_expiry
- invalidation_reason
- calibration_group

All directional candidates that have sufficient price data contain a deterministic trigger/stop/target plan. A missing plan is an explicit data-unavailable state; replay must not silently invent a trigger. Weak directional candidates remain research-only but lose shadow eligibility, so no_trade can win.

## Time and quality

Observation timestamps are UTC and preserve source event/publish/availability time when the source supplies them. BLS/BEA economic-series rows keep measurement period in payload and intentionally leave source event/publish timestamps unset. Candle features exclude forming candles. Cross-asset features floor timestamps to the configured timeframe, apply session masks, use filtered-count overlap, nearest tolerance, and matched-pair delayed/current confirmation.

## Snapshot and decision

The snapshot contains facts, computed_features, data_quality, factor_state, event_state, ranked_candidates, portfolio_constraints, and unsupported_data.
ranked_candidates is globally ordered across every tradable product. The final decision uses the same ranking key rather than trusting the first valid directional row.

Decision final_action is:
actionable_long, actionable_short, research_only_long, research_only_short, no_trade, or data_unavailable.

The decision also contains execution_permission, which is derived from the selected candidate and account/data state.
It exposes selected_product_id for the selected candidate; equal score keys resolve by product_id, direction, and setup_type ascending, never candidate_id.

## Outcome and calibration

Shadow outcomes contain trigger/fill/exit status, fees, slippage, funding, gross/net return, MFE, MAE, holding time, setup, horizon, regime, and predicted probability. Candidates remain open until time_expiry when no trigger is seen; only expiry then becomes a terminal not_filled outcome. Open candidates are deduplicated by product/direction/setup. Calibration groups by product/direction/setup/horizon/regime and exposes sample count, success rate, net/gross edge, confidence interval, Brier score, and insufficient_sample status.

## Storage

Normalized decisions, open shadow candidates, and outcomes are stored in DuckDB (or explicit SQLite development mode). Raw observations are partitioned Parquet batches with temp-file to atomic-rename writes. Payload hashes make restart ingestion idempotent. JSONL is audit-only when explicitly enabled.

## Provider boundary

Capability declarations list only implemented endpoints. CoinGlass liquidation, heatmap, futures/spot CVD, OI, and basis are actual opt-in endpoints; funding and ETF are plan-not-available. BLS and BEA are period-series connectors only and do not provide release event timestamps; Fed/OpenDART, Gate Stock/CFD, and KIS live remain status boundaries. SOXL/SK Hynix are not tradable in the default registry.
