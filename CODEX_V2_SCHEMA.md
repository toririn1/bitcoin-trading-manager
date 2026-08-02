# V2 schema contract

## Observation

```json
{
  "observation_id": "...",
  "provider": "binance",
  "venue": "binance_futures",
  "product_id": "BTC_BINANCE_PERP",
  "data_type": "candle_15m",
  "source_event_time": "2026-08-02T00:00:00Z",
  "source_publish_time": null,
  "first_seen_at": "2026-08-02T00:00:02Z",
  "collected_at": "2026-08-02T00:00:02Z",
  "available_at": "2026-08-02T00:00:02Z",
  "processed_at": null,
  "quality": "ok",
  "schema_version": "2.0",
  "payload": {}
}
```

`source_event_time` is never replaced with collection time. Missing timestamps produce `timestamp_unknown` and `is_fresh=false`.

## Products and candidates

- `AssetSpec` identifies an underlying asset.
- `ProductSpec` identifies a tradeable venue product and is registered only from a known/returned catalog.
- `OpportunityCandidate` contains long, short, and no-trade candidates with deterministic component scores, gross/cost/net edge, confidence, reason codes, risk codes, and source snapshot.

## Snapshot envelope

All V2 endpoints return:

```json
{
  "schema_version": "2.0",
  "generated_at": "...",
  "data": {}
}
```

Snapshot `data` contains `facts`, `computed_features`, `data_quality`, `factor_state`, `event_state`, `ranked_candidates`, `portfolio_constraints`, and `unsupported_data`.

## Semantic labels

- `actual_liquidation_event` / `liquidation_aggregate_actual`
- `public_liquidation_snapshot` (pulse only, not market total)
- `estimated_liquidation_cluster`
- `trade_cvd`
- `taker_bucket_delta_1h`
- `rr_25d` only with actual option Greek delta
- `estimated_skew_proxy` for any non-Greek estimate
- `closed_candles` and `forming_candle`

## Decision package

The deterministic decision contains `market_view`, `setup_verdict`, `setup_quality`, `candidate_rank`, `account_overlay`, `portfolio_overlay`, `product_guard`, `execution_permission`, and `final_action`. The final action may be `long`, `short`, `reduce`, `close`, `hold`, `watch`, `no_trade`, or `data_unavailable`.
