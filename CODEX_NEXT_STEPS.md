# Next steps

## P0 manual runtime confirmation

After restarting the normal dashboard server, run one fresh analysis and confirm:

- Conditional support/breakout/retest wording produces `setup_action_verdict=wait_for_trigger`.
- `entry_expectancy` is `conditional_good`/ `acceptable`, not `good`.
- High fee pressure does not show `execution_permission=allow`.
- Existing conflicting short shows reduce/close guidance separately from new-entry restriction.
- The raw report ends with exactly one `🧩 최종 Decision Support` seven-section block.

## P1 commit hygiene

The worktree is intentionally not committed. Do not stage `.env`, snapshots, or unreviewed broad UI/server changes.

Suggested review:

```bash
git status --short
git diff --stat
git add -p decision_bridge.py analyzer.py agents/judge.py agents/pipeline.py agents/prompts.py agents/risk_prompts.py agents/risk_triad.py config.py static/index.html
git add analysis_performance.py scripts/backfill_performance.py scripts/smoke_test.py tests/test_decision_action_semantics.py tests/test_fee_permission_policy.py tests/test_trigger_extraction.py tests/test_agent_decision_layers.py tests/test_model_defaults.py
git diff --cached --check
```

## P2 optional follow-up

Use the performance backfill script only for timestamps within the fetched 5m candle window, or pass a historical candle JSON file:

```bash
.venv/bin/python scripts/backfill_performance.py data/analysis_history.jsonl --no-fetch
.venv/bin/python scripts/backfill_performance.py data/analysis_history.jsonl --candles-json candles.json
```

## Resume here next time

```bash
.venv/bin/python scripts/smoke_test.py
.venv/bin/python -m pytest -q -s
```

Then inspect [CODEX_REPORT.md](CODEX_REPORT.md) before staging any changes.
