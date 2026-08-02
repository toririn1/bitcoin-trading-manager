# Bitcoin Trading Manager V2 source audit

## Baseline

- Local WSL path: `/home/toririn/bitcoin-trading-manager`
- Baseline branch: `codex/decision-support-5.6-refactor`
- V2 branch: `codex/v2-market-engine`
- Baseline commit: `bcc1aa3` (`feat: add decision support and account analysis layers`)
- Existing dirty/untracked material was preserved. A pre-worktree diff and untracked-file list are in `artifacts/`.
- `.env`, `data/`, virtualenv, caches, and account snapshots are not stage targets.

## Existing providers and actual meanings

| Area | Existing implementation | Meaning / limitation |
|---|---|---|
| OHLCV and price | `data_fetcher.py` | Binance USD-M futures klines and ticker. Kline `close_time` is parsed then discarded from the returned DataFrame; final/forming state is therefore not preserved. |
| Funding / mark / index | `market_context.py` | Binance `premiumIndex`, funding history, and open interest. Source timestamps are not carried into the output context. |
| OI | `market_context.py` | Binance and Bybit OI are collected. Aggregated changes are an arithmetic mean of venues, not USD-notional weighted. |
| Taker delta | `market_context.py` | Binance `takerlongshortRatio` hourly buy/sell buckets are cumulatively summed. This is a taker bucket delta, not trade-stream CVD or footprint CVD. |
| Order book | `market_context.py` | Binance top-20 quantity ratio only; no price-distance bands, persistence, cancellation, or sequence validation. |
| Liquidations | `market_context.py` | Binance private `fapi/v1/forceOrders` is called and totalized as market liquidation. This has invalid market-wide semantics and is a V2 removal target. |
| Options | `market_context.py` | Deribit public book-summary data is paired with a guessed strike distance from DVOL. This is an estimated skew proxy, not actual 25-delta risk reversal. |
| Macro | `macro_fetcher.py` | yfinance and public crypto/macro sources are normalized into the legacy macro context. The source timestamp and delayed status are not consistently preserved. |
| Account | `account_context.py`, `account_providers/gateio.py` | Gate account, position, fee, and rebate views are read-only and must be retained. V2 consumes them as an account overlay. |
| LLM | `llm_client.py`, `analyzer.py`, `agents/` | Legacy analyzer, Bull/Bear debate, Judge/Risk Triad, and memory layers are active by default. V2 moves them behind an explicit legacy flag. |

## Timestamp and freshness audit

- `data_fetcher.fetch_ohlcv` returns a pandas index containing only the kline open timestamp. Close time and final status are discarded.
- `decision_support.freshness` substitutes the current time when a source timestamp is absent, making a just-collected stale value appear fresh. V2 never substitutes timestamps.
- `market_context.fetch_market_context` creates a fresh context timestamp at collection time but does not attach source event/publish/available timestamps to individual observations.
- Legacy caches exist for market sentiment (five minutes), macro snapshots (about one hour), and JSONL analysis history. Cache age is not a first-class quality field in the legacy API.

## Fallback and hard-coded market assumptions

- `market_context.fetch_market_context` uses `mark_price or index_price or 80000.0` before Deribit analysis. The `80000.0` fallback is removed in V2; missing price becomes `missing_underlying_price`.
- Individual legacy provider failures commonly become `None`, which is useful for keeping the UI alive but does not distinguish stale, unsupported, authentication-required, or provider-error states.
- Legacy macro prompts contain fixed textbook interpretations. V2 computes relationship statistics for the active window and regime instead of applying fixed signs.

## LLM-owned values and memory

- Legacy prompts describe price structure, momentum, derivatives, macro, and account context to an LLM which produces the main narrative and signal.
- The legacy agent pipeline runs Bull/Bear debate, Judge, Risk Triad, and reflection/memory recall. `agents/memory.py` persists text records and uses BM25/Jaccard-like retrieval.
- `MEMORY_WRITE_ENABLED`, `MEMORY_ENABLED`, and `DEBATE_ENABLED` default to enabled in the legacy path. V2 defaults these flows off and passes only structured deterministic snapshots to an explainer.
- V2 computes score, freshness, timestamp validity, cost, and execution permission in code. The LLM cannot mutate these values.

## Account and order permissions

- Existing Gate account/position and fee/rebate reads are preserved.
- Existing API POST routes include schedule, analysis start, reflection, setup save, owner message, and cheers. These are application/configuration actions, not market orders.
- V2 provider interfaces expose only discovery and market/account reads. No order creation, amendment, cancellation, leverage, transfer, or withdrawal method is implemented.
- `POST /api/v2/events/manual-intake` is intentionally the only V2 write route and accepts a local/manual event report, not a trading action.

## Existing API endpoints

The server currently exposes health/debug, market and account streams, connections, schedule, analysis start/status, reflection, macro, market sentiment, account, setup status/save, analysis history, performance, Gate realized PnL, owner message, cheers, Chzzk, and the static UI routes. The existing response shapes remain available. The V2 router is additive and uses `/api/v2/*` with an explicit `schema_version`.

## UI JSON dependencies

`static/index.html` consumes the legacy analysis payload, chart series, macro/traditional-market payload, market sentiment payload, account summary/positions/open orders, setup status, analysis history, performance, schedule, stream/debug status, and Decision Support aliases. V2 adds independent panels and does not remove those keys. Legacy adapter fields are marked with an explicit engine/source label when V2 data is unavailable.

## Test coverage before V2

- There are 19 `test_*.py` files covering indicators, market context, macro history, Gate provider/account behavior, analyzer structure, agent layers, decision-support semantics, fees, model defaults, and trigger extraction.
- The existing suite had passed before V2 work, but it did not prove: forceOrders semantic exclusion, source timestamp preservation, forming-candle isolation, point-in-time replay, weighted OI, actual-versus-estimated liquidation typing, plan-unavailable handling, dynamic cross-asset relations, factor de-duplication, deterministic score immutability, or the complete V2 API/UI contract.

## V2 audit conclusions

1. Keep FastAPI, static UI, Gate account reads, fee/rebate logic, Decision Support separation, history/performance, and user settings.
2. Build a separate `engine_v2` with strict domain models, source timestamps, explicit quality, provider health, registry/discovery, point-in-time storage, deterministic features/scoring, structured intelligence, and replay evaluation.
3. Keep legacy modules importable behind explicit configuration. Do not silently fall back from V2 to legacy.
4. Remove the unsafe market semantics in the V2 path rather than relabeling the old values.
