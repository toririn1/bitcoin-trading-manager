Continue the existing bitcoin-trading-manager Codex session.
Read CODEX_GOAL_ULTRA.md and existing CODEX_STATE/REPORT/NEXT_STEPS first.
Fix the decision-system layer confusion:
market_direction independent, setup_action wait_for_trigger for conditional text, entry_expectancy not good unless immediate, execution_permission not allow under high fee warning, final_action combines setup and account overlay.
Keep gpt-5.6-sol defaults.
Add tests for trigger semantics, fee permission thresholds, action/expectancy consistency, and model defaults.
Preserve user changes, avoid trading/order execution, avoid giant UI/server refactors.
Update CODEX_STATE.md, CODEX_REPORT.md, CODEX_CHANGELOG.md, CODEX_NEXT_STEPS.md, CODEX_REVIEW_PACKET.md.
Run smoke tests and pytest.
Commit only safe separable passing changes; otherwise document why and give exact next steps.
