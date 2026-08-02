# CODEX Report

## Summary

The decision system now keeps four layers separate:

1. `market_direction` is market-only.
2. `setup_action_verdict` and `setup_quality` are current-price, trigger, level, and R:R quality.
3. `account_execution_permission` and `account_overlay` are fee/account/position constraints.
4. `final_action` is the transparent combination.

Legacy API/UI fields remain available: `action_verdict`, `execution_permission`, `entry_expectancy`, and `forbidden_actions`.

## Behavior changes

- Conditional phrases such as support confirmation, breakout close, retest, and do-not-chase force `wait_for_trigger`.
- `good`/ `excellent` is restricted to an immediate valid setup; conditional setups are `conditional_good`, `acceptable`, or `poor`.
- R:R below 1.0 cannot produce `good` or immediate entry.
- Fee pressure uses conservative net fee ratio for policy while preserving gross fee as a display warning.
- High gross fee warning cannot leave permission at `allow`.
- Existing conflicting positions may still be reduced/closed when new entries are blocked, except account-data-integrity cases.
- The final report receives one authoritative seven-section Decision Support block after deterministic setup/account calculation.

## Fee example

For gross fee 384.007332, received rebate 243.98274, pending rebate 24.8223924, and equity 1195.25:

- Gross ratio is a display warning.
- Conservative net fee ratio exceeds the hard threshold.
- Permission is `blocked`/ `hard_block`, never `allow`.
- Market direction remains unchanged; a bullish conditional setup remains `wait_for_trigger` rather than becoming `no_trade`.

## Files materially changed in this maintenance pass

- `decision_bridge.py`: decision layers, trigger extraction, fee overlay, final-action composition, API compatibility.
- `analyzer.py`: shared Decision Support context, final setup application, authoritative seven-section report block.
- `agents/judge.py`, `agents/risk_triad.py`, `agents/pipeline.py`: structured market/setup/account/final outputs and shared object forwarding.
- `agents/prompts.py`, `agents/risk_prompts.py`: role-specific separation without weakening decisive analysis.
- `static/index.html`: safe Decision Support formatting and fallback rendering.
- `analysis_performance.py`, `scripts/backfill_performance.py`: migration-safe history performance enrichment.
- `scripts/smoke_test.py` and dedicated decision tests: regression coverage.

## Verification

- Core compile command passed.
- `scripts/smoke_test.py`: passed.
- `pytest -q -s`: **105 passed, 4 FastAPI deprecation warnings, 5 subtests passed**.
- `git diff --check`: no whitespace errors.
- Local runtime: server started; `/health`, `/`, `/api/setup/status`, and `/api/account` returned 200.
- Static UI JavaScript syntax check passed with Node.

## Model default audit

No runtime `gpt-5.5` reference remains outside the explicit historical goal document and excluded snapshot. Intended config, README, example environment, startup script, UI, and tests use `gpt-5.6-sol`.

## Commit status and risk

No commit was made. The worktree was already dirty and the tracked files contain intertwined prior user changes. It would be unsafe to stage whole files such as `analyzer.py`, `config.py`, `server.py`, or `static/index.html` automatically.

Recommended safe review flow:

```bash
git add -p decision_bridge.py analyzer.py agents/judge.py agents/pipeline.py agents/prompts.py agents/risk_prompts.py agents/risk_triad.py config.py static/index.html
git add analysis_performance.py scripts/backfill_performance.py scripts/smoke_test.py tests/test_decision_action_semantics.py tests/test_fee_permission_policy.py tests/test_trigger_extraction.py tests/test_agent_decision_layers.py tests/test_model_defaults.py
git diff --cached --check
```

Keep `.env` unstaged and do not commit snapshot/junk files without manual review.

## Resume here next time

Restart the normal server, run a fresh browser analysis, then compare the card and report against the test fixture: conditional text must show `wait_for_trigger`, not immediate entry; high fee pressure must not show `allow`.
