CODEX GOAL ULTRA: BTC Analyzer Decision System Full Maintenance
0. Mission

You are continuing the existing Codex session for this repository:

/home/toririn/bitcoin-trading-manager

This project is a local BTC analyzer derived from likegyu/bitcoin-trading-manager and heavily adapted for the user's WSL/local environment.

This is not an auto-trading bot.
Do not add order execution.
Do not add POST/PUT/DELETE trading endpoints.
Do not add auto-buy, auto-sell, order placement, cancel order, leverage change, or position mutation functionality.

The goal is to make the analyzer useful for real trading decisions without becoming an overprotective babysitter.

The system must separate:

Market direction
Setup quality
Account / fee / position overlay
Final user-facing action

The key problem is not simply “too conservative” or “too aggressive.”
The key problem is layer confusion.

Market direction should not be overwritten by account restrictions.
Setup action should not be overwritten by account restrictions.
Account restrictions should not pretend to be market direction.
Final action should combine them transparently.

1. Existing Session Context

This is a continuation. Do not start from scratch.

Previous Codex work already happened:

Branch was created:
codex/decision-support-5.6-refactor
Some reports may already exist:
CODEX_REPORT.md
CODEX_CHANGELOG.md
CODEX_NEXT_STEPS.md
CODEX_STATE.md
CODEX_REVIEW_PACKET.md
before_codex_status.txt
before_codex_diffstat.txt
before_codex_pytest.txt
after_codex_pytest.txt
GPT model defaults were partially changed from gpt-5.5 to gpt-5.6-sol.
Some Decision Support fields were added:
market_signal
setup_quality
market_action
final_action
account_overlay
allowed_actions
There were known issues:
decision_support / decision_bridge logic may still be inconsistent.
action_verdict, execution_permission, entry_expectancy may contradict the LLM body.
fee logic may swing between too strict and too loose.
static/index.html may have been touched.
.env may have been touched.
Worktree is dirty and contains user changes plus Codex changes.

Your first job is to read existing state files and current diffs before editing.

Read, if present:

CODEX_GOAL_ULTRA.md
CODEX_GOAL.md
CODEX_STATE.md
CODEX_REPORT.md
CODEX_CHANGELOG.md
CODEX_NEXT_STEPS.md
CODEX_REVIEW_PACKET.md
after_codex_pytest.txt
before_codex_status.txt
before_codex_diffstat.txt

Then inspect:

git branch --show-current
git status --short
git diff --stat
git diff -- decision_bridge.py decision_support.py
git diff -- agents/risk_prompts.py agents/prompts.py analyzer.py
git diff -- config.py .env.example README.md run.sh static/index.html
grep -R "gpt-5.5" -n . excluding .git, .venv, pycache, analyzer_snapshot.txt
grep -R "execution_permission" -n .
grep -R "action_verdict" -n .
grep -R "entry_expectancy" -n .
grep -R "fee_to_equity" -n .
grep -R "overtrading_fee_warning" -n .
grep -R "신규 진입 금지" -n .
grep -R "no_trade" -n .

Do not blindly trust previous implementation.
Reconcile it with the desired behavior below.

2. Current Critical Bug From Browser Validation

Browser validation showed the Decision Support card mostly displays fee/rebate values correctly.

However, a serious mismatch remains between:

market_direction
action_verdict
entry_expectancy
execution_permission
LLM body
multi-agent body
fee warning

Observed actual UI example:

market_direction: 상방 우위
action_verdict: enter_long_now
entry_expectancy: good
execution_permission: allow
today_fee_paid: 384.007332
today_rebate_received: 243.98274
expected_rebate_pending: 24.8223924
fee_to_equity_ratio: 0.3097955
overtrading_fee_warning: true

This is wrong.

Why wrong:

LLM body and multi-agent body did not say immediate entry.
They said conditional entry:

$63,120~$63,220 support confirmation
$63,610 15m close breakout
$63,480~$63,606 retest hold
no chasing unfinished candle
wait for support / breakout / retest / close confirmation

That is wait_for_trigger, not enter_long_now.

Fee-to-equity ratio is extremely high.
fee_to_equity_ratio around 0.309 means 30.9%.
overtrading_fee_warning=true.
execution_permission=allow is inappropriate.
entry_expectancy=good is too strong if the report says resistance is nearby and entry requires confirmation.
For conditional setups, use conditional_good, pending_good, or acceptable.
good should mean the setup is good for immediate entry now, not merely good after a trigger.
3. Required Semantic Model

Implement or enforce the following conceptual layers.

Layer A: market_direction

This is the market-only direction.

Examples:

bullish
bearish
neutral
상방 우위
하방 우위
중립

Rules:

Account status must not change market_direction.
Fee warning must not change market_direction.
Existing position must not change market_direction.
market_direction is derived from market data, indicators, derivatives, macro, and LLM market interpretation.
Layer B: setup_action_verdict

This is the setup-only action based on current price, entry zone, trigger state, conditions, and R:R.

Allowed examples:

enter_long_now
enter_short_now
wait_for_trigger
wait_for_retest
wait_for_support_confirmation
wait_for_breakout_close
hold_existing
no_edge
avoid_chase

Rules:

If LLM body says “지지 확인 시”, “돌파 후”, “리테스트”, “종가 돌파 시”, “눌림 시”, “확인 후”, “되밟기”, “15m close”, “1h close”, “support confirmation”, “breakout confirmation”, “retest hold”, “wait for trigger”, then setup_action_verdict must be wait_for_trigger or a more specific wait_* value.
enter_long_now is only allowed when:
current price is inside a valid entry zone, or the setup is explicitly immediate;
no additional confirmation condition remains;
R:R is acceptable;
entry is not merely chasing into nearby resistance;
account permission is not blocked/hard_block if legacy action_verdict is final-facing.
If current price is outside entry_zone, action must be wait_for_trigger.
If trigger price has not been reached, action must be wait_for_trigger.
If report says “do not chase,” action must not be enter_long_now.
If resistance is very near and breakout confirmation is required, action must not be enter_long_now.
Layer C: entry_expectancy

This is setup quality.

Allowed examples:

poor
acceptable
conditional_good
pending_good
good
excellent

Rules:

entry_expectancy=good is only allowed for immediate actionable setups.
If action_verdict or setup_action_verdict is wait_for_trigger, entry_expectancy must be conditional_good, pending_good, acceptable, or poor.
good must not be used for “good if trigger happens later.”
If current price is near resistance and waiting for breakout/retest, use conditional_good or acceptable.
If R:R is below 1.0, use poor or wait_for_trigger.
If immediate entry is chasing unfinished candle, do not use good.
If setup requires support confirmation, do not use good now; use conditional_good.
Layer D: account_execution_permission

This is account overlay only.

Allowed examples:

allow
reduce_size_only
reduced_size
cooldown_required
manual_confirm_required
blocked
hard_block

Rules:

This does not change market_direction.
This does not rewrite the market setup into no_edge.
This limits final execution.
This can forbid opening new positions while still allowing reduce_position or close_position.
This can require manual confirmation.
This can reduce size.
This can impose cooldown.
Layer E: final_action

This is the combined user-facing action.

Examples:

wait_for_trigger_with_size_limit
wait_for_trigger_but_account_blocked
manage_existing_position_only
enter_long_reduced_size_after_trigger
enter_long_manual_confirm_after_trigger
no_new_entry_due_to_hard_block
reduce_or_close_conflicting_short_on_breakout
hold_and_wait

Rules:

final_action should explain the combination.
Do not collapse everything into “no_trade” unless truly appropriate.
If setup says wait_for_trigger and account is blocked, final_action should be something like wait_for_trigger_but_no_new_entry_until_fee_cooldown, not market no_trade.
If existing position conflicts with market direction, final_action should mention reduce/close conditions.
“no_trade” may still exist for backward compatibility, but avoid using it as a universal sink.
4. Legacy Field Mapping

The code likely already has legacy fields:

action_verdict
execution_permission
entry_expectancy
forbidden_actions

Do not break existing UI/API.

But clarify semantics.

Preferred mapping:

market_direction:
market-only direction
setup_action_verdict:
setup-only action
action_verdict:
backward-compatible public setup verdict.
It should follow setup_action_verdict more than account permission.
It must not be enter_long_now if the body requires trigger confirmation.
execution_permission:
account permission only.
It must not be allow when overtrading_fee_warning=true and fee ratio exceeds configured thresholds.
final_action:
combined account-adjusted action.
account_overlay:
structured account restrictions.
allowed_actions:
concrete allowed action enum list.
forbidden_actions:
concrete forbidden action enum list.

If changing this mapping would break UI, keep old fields and add new fields rather than removing old ones.

5. Fee Logic Requirements

Use config thresholds.

Required config defaults:
FEE_TO_EQUITY_REDUCE_THRESHOLD = 0.02
FEE_TO_EQUITY_BLOCK_THRESHOLD = 0.05
HARD_BLOCK_THRESHOLD = 0.10

If names already exist, reuse them.
If they do not exist, add them in config.py with env overrides.

Fee fields:

today_fee_paid
today_rebate_received
expected_rebate_pending
gross_fee
received_rebate
expected_rebate
net_fee
gross_fee_to_equity_ratio
net_fee_to_equity_ratio
conservative_net_fee_to_equity_ratio
fee_to_equity_ratio
overtrading_fee_warning

Rules:

gross fee ratio is warning/display.
net fee ratio should be used for nuanced decisions.
conservative net fee ratio may count expected rebate partially.
expected rebate is not guaranteed, so expose it separately.
missing fee data is not by itself a block reason.
missing fee data may produce a warning reason.
overtrading_fee_warning=true means execution_permission cannot be allow.
if fee_to_equity_ratio or relevant effective ratio >= reduce threshold, execution_permission must be at least reduce_size_only or cooldown_required.
if fee ratio >= block threshold, execution_permission should be blocked or cooldown_required depending on net ratio and recent trading.
if fee ratio >= hard threshold, execution_permission should be hard_block or blocked, unless data is clearly invalid.
current example ratio 0.309 must not produce allow.
current example ratio 0.309 should usually produce blocked or hard_block for new entries.
But market_direction must remain bullish if market is bullish.
setup_action_verdict may remain wait_for_trigger.
allowed_actions may still include reduce_position and close_position.

Important nuance:
Do not reintroduce babysitter behavior by making market_direction=no_trade.
Instead:
market_direction=bullish
setup_action_verdict=wait_for_trigger
entry_expectancy=conditional_good
execution_permission=blocked or hard_block
final_action=wait_for_trigger_but_no_new_entry_until_fee_cooldown / manage_existing_position_only

6. Forbidden Actions Semantics

Do not use forbidden_actions as vague Korean-only prose if there is a better structured alternative.

Prefer structured enums plus display labels.

Forbidden action enum candidates:

open_new_position
open_new_full_size
add_to_position
add_to_losing_position
revenge_trade
repeated_reentry
high_leverage
no_stop_entry
chase_entry
short_chase
long_chase
reverse_position
increase_risk
new_entry_when_blocked

Allowed action enum candidates:

wait_for_trigger
reduce_position
close_position
hold_existing
monitor_breakout
monitor_support
manual_review
enter_reduced_size_after_trigger
enter_after_manual_confirm
no_new_entry

Rules:

reduce_size_only:
forbidden_actions should include:
high_leverage
open_new_full_size
repeated_reentry
no_stop_entry
revenge_trade
chase_entry
It should not necessarily include generic “신규 진입 금지.”
blocked:
forbidden_actions may include:
new_entry_when_blocked
open_new_position
open_new_full_size
high_leverage
repeated_reentry
no_stop_entry
revenge_trade
hard_block:
same as blocked plus manual_review if data/account/liquidation risk.
“신규 진입 금지” display label should appear only for blocked/hard_block, not reduce_size_only.
reduce_size_only should show size/leverage/cooldown restrictions, not absolute prohibition.
close_position/reduce_position should generally remain allowed unless account/data integrity is unknown.
7. Conditional Trigger Extraction

Implement or improve trigger extraction.

If LLM body or agent output contains conditional phrases, action should be wait_for_trigger.

Korean trigger phrases:

지지 확인 시
지지 확인
돌파 후
돌파 확인
리테스트
되밟기
종가 돌파
종가 상회
종가 하회
15m 종가
1h 종가
눌림 시
확인 후
재돌파
이탈 후
이탈 확인
지켜주면
안착하면
회복하면
유지하면
실패하면
트리거
미완성봉 추격 금지
추격 금지

English trigger phrases:

after breakout
breakout confirmation
close above
close below
retest
retest hold
support confirmation
resistance break
wait for trigger
pullback
do not chase
unfinished candle
confirmation required

If these are present:

setup_action_verdict = wait_for_trigger
action_verdict = wait_for_trigger, unless a more specific wait_* exists
entry_expectancy = conditional_good or acceptable, not good
trigger_condition should be filled
trigger_price should be filled if extractable
invalidation should be filled if extractable

Trigger condition examples:

“$63,610 15m close breakout and $63,480~$63,606 retest hold”
“$63,120~$63,220 support confirmation”
“$62,930 breakdown invalidates long setup”

If extraction is uncertain:

preserve raw_trigger_text
set trigger_condition from source sentence
set confidence low
do not invent precise levels.
8. Price Level Extraction

Improve robust extraction but avoid hallucination.

Extract:

entry_zone
trigger_price
trigger_zone
support_zone
resistance_zone
invalidation
stop
target

From texts like:

$63,120~$63,220
$63,480~$63,606
$63,610 15m 종가 돌파
$62,930 이탈
64,700 돌파
64,560~64,700 되밟기

Rules:

If current price is outside entry_zone, do not enter_now.
If price is below trigger_price and trigger requires breakout, wait_for_trigger.
If price is near resistance and breakout not confirmed, wait_for_trigger.
If stop/target absent, do not classify immediate setup as good.
If only conditional entry levels exist, classify as conditional_good/acceptable.
9. R:R and Resistance Proximity Rules

Do not allow enter_long_now just because market_direction is bullish.

For immediate enter_long_now:

current price must be in entry zone or immediate setup explicitly valid
stop exists
target exists
R:R >= configured minimum, default 1.2 or 1.5 if fee pressure high
nearest resistance is not too close
not chasing an overextended candle
no explicit “wait for confirmation” text

For wait_for_trigger:

if R:R would be good after trigger/retest, entry_expectancy=conditional_good
if R:R uncertain, entry_expectancy=acceptable or pending_good
if resistance is too close before confirmation, not good now

Fee pressure should increase required R:R and setup grade.

Example:

normal account: min RR 1.2
reduced_size_only / high fee warning: min RR 1.5
cooldown_required: min RR 1.8 or manual confirm
blocked/hard_block: no new entry regardless, but still show market setup
10. UI / API Backward Compatibility

Do not break existing UI.

Existing Decision Support card may read:

market_direction
action_verdict
entry_expectancy
execution_permission
forbidden_actions
fee_to_equity_ratio
overtrading_fee_warning

Keep these fields.

Add new fields:

setup_action_verdict
market_action_verdict
account_execution_permission
account_overlay
final_action
final_action_label
trigger_condition
trigger_price
trigger_zone
invalidation
raw_trigger_text
position_alignment
allowed_actions
forbidden_action_codes
forbidden_action_labels
fee_summary
fee_pressure_level
required_rr
immediate_entry_allowed
immediate_entry_blockers

If static/index.html is already touched:

inspect the diff
keep only minimal display/default model changes
do not perform broad UI refactor unless necessary
if adding UI fields, use minimal additions and record in report

Prefer backend compatibility first.

11. Prompt Behavior Requirements

Update prompts so agents do not all collapse into same safety phrase.

Aggressive analyst:

Must argue market-only opportunity.
Must identify what would justify conditional entry.
May propose reduced-size conditional entry if account permission allows.
Must not ignore fee/account restrictions.
Must not recommend no-stop, high-leverage, revenge, or full-size chase.
Must not be silenced by blocked permission; it can still discuss the market-only opportunity.

Conservative analyst:

Must argue account risk, fee drag, resistance proximity, RSI overextension, data limitations.
Must distinguish “market setup exists” from “not executable due to account overlay.”

Neutral/judge:

Must explicitly output:
market-only view
setup condition
account-adjusted permission
final action

Final LLM report:
Must separate:

시장만 보면
진입 품질
내 계좌 반영 시
기존 포지션 관리
금지 행동
무효화 조건

Bad output:

“상방 우위지만 신규 진입 금지” repeated everywhere
“blocked so market discussion omitted”
“enter_long_now” when body says wait for support/breakout/retest
“good” expectancy for conditional future setup

Good output:

market_direction: bullish
setup_action_verdict: wait_for_trigger
entry_expectancy: conditional_good
execution_permission: blocked or reduce_size_only based on fee
final_action: wait_for_trigger_but_no_new_entry_until_fee_cooldown
existing short: reduce/close on bullish breakout
forbidden: high leverage, full size, repeated reentry, no-stop, chase; new entry forbidden only if blocked/hard_block
12. Tests To Add Or Strengthen

Add tests. Do not rely only on smoke test.

Preferred new files:

tests/test_decision_action_semantics.py
tests/test_fee_permission_policy.py
tests/test_trigger_extraction.py
tests/test_model_defaults.py

Required test cases:

Conditional text forces wait_for_trigger:
Input report contains:
“$63,610 15m 종가 돌파 후 $63,480~$63,606 리테스트 지지 시 롱”
Expected:
action_verdict = wait_for_trigger
setup_action_verdict = wait_for_trigger
entry_expectancy != good
entry_expectancy in conditional_good / acceptable / pending_good
trigger_condition not empty
Support confirmation forces wait_for_trigger:
Input report contains:
“$63,120~$63,220 지지 확인 시 롱”
Expected:
wait_for_trigger
trigger zone extracted or raw_trigger_text preserved
Do not chase:
Input report contains:
“미완성봉 추격 금지”
Expected:
not enter_long_now
immediate_entry_allowed=false
chase_entry in forbidden actions
High fee warning cannot allow:
overtrading_fee_warning=true
fee_to_equity_ratio=0.309
Expected:
execution_permission != allow
account_execution_permission != allow
Fee above reduce threshold:
fee ratio=0.03
Expected:
reduce_size_only or cooldown_required or manual_confirm_required
not allow
Fee above block threshold:
fee ratio=0.06
Expected:
blocked or cooldown_required depending policy
not allow
Fee above hard threshold:
fee ratio=0.31
Expected:
blocked or hard_block
not allow
Missing fee data:
fee data missing
Expected:
not automatically blocked
reason indicates fee data missing
permission based on other factors
Gross vs net fee:
gross high
received rebate high
expected rebate present
Expected:
fee_summary has gross/net/conservative ratios
permission uses configured effective ratio
report shows transparent fields
Market direction independent:
market_direction bullish
fee hard block
Expected:
market_direction remains bullish
setup_action can remain wait_for_trigger
final_action reflects account block
Conflicting short:
market bullish
current position short
Expected:
position_alignment conflicted/strongly_conflicted
allowed_actions includes reduce_position or close_position
forbidden may include adding to short or short chase
blocked forbidden actions:
execution_permission blocked
Expected:
forbidden includes new entry prohibition
allowed may include close/reduce unless account data error
reduce_size_only forbidden actions:
execution_permission reduce_size_only
Expected:
forbidden includes high_leverage, full_size, repeated_reentry, no_stop
does not include generic new_entry_forbidden label
entry_expectancy good only immediate:
action_verdict wait_for_trigger
Expected:
entry_expectancy != good
enter_long_now strict:
Only allowed when:
no trigger text
current price inside entry zone
RR acceptable
no nearby resistance blocker
account permission allow/reduced/manual but not blocked
Expected:
enter_long_now possible only in this fixture
model default:
config default LLM_MODEL is gpt-5.6-sol
.env.example uses gpt-5.6-sol
README uses gpt-5.6-sol
run.sh uses env-driven or gpt-5.6-sol
gpt-5.5 not present except legacy analyzer_snapshot.txt or explicit historical report
Prompt string test:
risk prompts no longer contain exact absolute obedience phrasing like:
“execution_permission=blocked이면 시장 방향과 관계없이 신규 진입 금지를 강하게 명시”
If still present, replace or contextualize.

Run:

python scripts/smoke_test.py
python -m pytest -q
if pytest capture issue occurs, run python -m pytest -q -s and document capture issue separately
13. Browser / Runtime Validation

If browser or local web validation is available, do it.

Start app only if safe.
Do not expose secrets.
Do not print .env.

Validation goals:

Decision Support card shows actual fee/rebate values.
market_direction is visible.
action_verdict does not say enter_long_now when body says wait for trigger.
entry_expectancy is conditional_good/acceptable for conditional setups.
execution_permission is not allow when overtrading_fee_warning=true and fee ratio is high.
forbidden actions differ between reduce_size_only and blocked.
existing short conflict is explained separately from new entry permission.
gpt-5.6-sol appears as default model in UI/config if UI exposes it.

If browser unavailable:

use API endpoint if known
use smoke test fixture
use unit tests
document limitation.
14. Worktree / Commit Discipline

The worktree is dirty.
Do not make one giant commit that mixes user work and Codex work unless unavoidable and explicitly documented.

Before commit:

git status --short
git diff --stat
git diff --cached --stat
git diff --cached --check
ensure .env not staged
ensure secrets not staged
ensure static/index.html changes are intentional and minimal
ensure account_providers did not gain write/order methods

Commit if:

tests pass
staged files are coherent
change is separable
no secrets
no accidental giant UI refactor
no user unrelated changes staged

Recommended commits:

feat(config): default to gpt-5.6-sol
refactor(decision): separate setup verdict from account overlay
test(decision): cover trigger and fee permission semantics
docs(codex): update maintenance state and review packet

If cannot safely commit:

do not commit
explain exactly why in CODEX_REPORT.md and CODEX_STATE.md
provide suggested git add command for safe subset

Never:

push main directly
force push
stage .env
commit analyzer_snapshot.txt unless explicitly needed
commit secrets
commit giant unrelated static/index.html diff unless minimal and documented
15. Reports To Maintain

Always update:

CODEX_STATE.md:

current phase
completed items
current blockers
next 3 actions
test status
commit status

CODEX_REPORT.md:

summary
files changed
behavior before/after
fee calculation example
action semantics
tests
git status
diffstat
risks
human review needed

CODEX_CHANGELOG.md:

timestamped changes
commands run
tests run
commit hashes if any

CODEX_NEXT_STEPS.md:

exact next run tasks
unresolved issues
risky files
suggested /goal objective

CODEX_REVIEW_PACKET.md:

short summary for ChatGPT
commands user should paste
latest test output summary
suspicious diffs
next instruction recommendation

At end of every run, include a section:

“Resume here next time”

with exact next steps.

16. Required Review Packet Commands

At the end, tell user to paste:

git status --short
git diff --stat
sed -n '1,260p' CODEX_REPORT.md
cat CODEX_STATE.md
cat CODEX_NEXT_STEPS.md
cat CODEX_REVIEW_PACKET.md
git diff -- decision_bridge.py decision_support.py | sed -n '1,320p'
git diff -- agents/risk_prompts.py agents/prompts.py analyzer.py | sed -n '1,260p'
git diff -- config.py .env.example README.md run.sh static/index.html | sed -n '1,260p'
python scripts/smoke_test.py
python -m pytest -q

17. Desired Example Output For Current Browser Case

For the observed case:

market_direction: 상방 우위
LLM body: conditional entry after support/breakout/retest
fee_to_equity_ratio: 0.3097955
overtrading_fee_warning: true
existing position: small short

Desired structure:

market_direction: bullish / 상방 우위
setup_action_verdict: wait_for_trigger
action_verdict: wait_for_trigger
entry_expectancy: conditional_good
trigger_condition:
“$63,610 15m close breakout and retest hold OR $63,120~$63,220 support confirmation”
invalidation:
“$62,930 breakdown” if present in source; otherwise preserve raw text or null
execution_permission:
blocked or hard_block depending policy
account_execution_permission:
blocked or hard_block
fee_pressure_level:
extreme
final_action:
wait_for_trigger_but_no_new_entry_until_fee_cooldown
allowed_actions:
wait_for_trigger
reduce_position
close_position
hold_existing
forbidden_actions:
high_leverage
open_new_full_size
repeated_reentry
no_stop_entry
chase_entry
new_entry_when_blocked only if blocked/hard_block
position_alignment:
conflicted or strongly_conflicted
existing_position_guidance:
“If bullish breakout confirms, reduce/close conflicting short rather than add to it.”

Not acceptable:
action_verdict: enter_long_now
entry_expectancy: good
execution_permission: allow
market_direction changed to no_trade
all agents repeating only “신규 진입 금지”

18. If Near Limit Or Interrupted

If token/time/quota limit is near, do not keep editing blindly.

Before stopping:

Save CODEX_STATE.md
Save CODEX_NEXT_STEPS.md
Save CODEX_REVIEW_PACKET.md
Record git status
Record tests run and results
Record exact next command/objective
Do not leave half-written files
Do not commit failing code

Next run must be able to resume from state files.

19. Done Criteria

This goal is done only when:

gpt-5.6-sol is the default everywhere intended
gpt-5.5 is gone except legacy snapshots/reports
action_verdict no longer says enter_long_now for conditional trigger text
entry_expectancy=good is not used for wait_for_trigger
overtrading_fee_warning=true prevents execution_permission=allow
high fee ratio above thresholds produces reduced/cooldown/blocked/hard_block according to config
missing fee data does not automatically block
market_direction remains independent from account restrictions
final_action combines setup + account overlay transparently
reduce/close existing conflicting position is allowed even when new entries are blocked, unless data/account error requires manual review
prompts separate market-only and account-adjusted conclusions
smoke test passes
pytest passes or real failures are documented and narrowed
reports are updated
safe commits are made if possible, otherwise commit withholding is justified with exact safe staging recommendation
