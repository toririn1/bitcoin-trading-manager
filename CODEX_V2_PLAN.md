# Bitcoin Trading Manager V2 plan

## Objective

Build a multi-asset, multi-source, point-in-time decision-support engine while preserving the existing FastAPI server, UI, Gate read-only account views, fee/rebate handling, Decision Support separation, history, and user settings.

## Design decisions

1. `engine_v2` is isolated from legacy root modules. Legacy imports remain available and are labeled if explicitly used.
2. Every observation carries source event/publish/first-seen/collected/available/processed timestamps and a `DataQuality` value.
3. Closed candles and forming candles are separate. Technical features use closed candles by default.
4. Actual liquidation events, partial public snapshots, and modeled clusters are different types. Binance `forceOrders` is not used.
5. Trade-stream CVD is named `trade_cvd`; hourly taker buckets remain `taker_bucket_delta_1h` if retained by a legacy adapter.
6. Option risk reversals require actual Greek delta. A strike-distance estimate is never emitted as `rr_25d`.
7. Cross-asset relationships are usable only after sample, session overlap, staleness, and stability gates.
8. Candidate scores, costs, data gates, portfolio guards, and execution permission are deterministic. LLM output is explanatory only.
9. `no_trade` and `data_unavailable` are first-class outcomes.
10. No V2 provider has order mutation, leverage, transfer, or withdrawal methods.

## Implementation phases

- Foundation: domain schema, registry, settings, storage, point-in-time, source audit.
- Ingestion: read-only provider adapters, discovery, health, rate-limit boundary, explicit unsupported/auth states.
- Features: technical, microstructure, derivatives, liquidation typing, options, dynamic relations, factors, regimes, event reaction, quality.
- Decision: product guards, cost model, deterministic candidate scoring, no-trade, portfolio/account overlays.
- Intelligence: structured snapshot, explanation, claim validation, legacy adapter.
- Integration: `/api/v2/*` routes and additive UI contract.
- Evaluation: replay, fills, calibration, shadow-mode outcome storage, summary.
- Verification: semantic tests, API smoke, forbidden-write scan, secret scan, docs and report.
