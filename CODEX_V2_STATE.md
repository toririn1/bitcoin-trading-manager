# CODEX V2 state

## Branch

codex/v2-decision-loop, normal ancestry from the existing V2 implementation commit. Existing user-created untracked files were preserved and are not staged by this work.

## Current behavior

- Live mode is explicit and never falls back to fixture.
- yfinance is delayed reference data only; reference products are absent from candidates.
- Venue products have explicit long/short support and spot short is prohibited.
- Directional candidates retain research value even when action is blocked by missing calibration, costs, guards, account state, or synthetic mode. A minimum heuristic score and data-quality gate are required before a directional candidate can enter shadow; otherwise no_trade is selected.
- Candidate entry plans are deterministic and replayable.
- Shadow outcomes are stored only for eligible, deduplicated open candidates. A not-triggered candidate stays open until expiry; the calibration endpoint reports insufficient_sample until enough terminal outcomes exist.
- Cross-asset relationships are session-aware and timestamp-tolerant.
- Parquet is batch/atomic; database hashes prevent duplicate restart writes.
- Provider health distinguishes implemented, disabled, authentication-required, unsupported, and plan-not-available boundaries.

## Verified

- python3 -m compileall -q engine_v2
- V2 semantic and fee/permission test subsets
- Heuristic no-trade and deterministic-plan smoke
- Shadow expiry, deduplication, outcome, and insufficient-sample smoke
- Provider capability and cross-asset alignment smoke

Final regression: .venv/bin/python -m pytest -q -> 133 passed, 5 warnings, 5 subtests passed. Live shadow one-shot: data_unavailable=false, final_action=no_trade, execution_permission=no_trade, settled_outcomes=0, open_shadow_candidates=0.
