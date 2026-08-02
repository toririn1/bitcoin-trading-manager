# =============================================
# Investment Judge — Bull/Bear 토론 중재 및 방향성 결론
# =============================================
# TradingAgents 의 invest_judge 패턴을 BTC 선물 맥락으로 구현.
#
# 역할:
#   - Bull/Bear 의 최종 발언을 받아 논리 강도를 평가
#   - 명확한 방향성 결론(상방/하방/중립)과 이유를 한 문단으로 정리
#   - 결과는 Risk Triad 에 "심판 결론" 으로 함께 주입됨
#   - judge 전용 메모리를 가져 과거 중재 패턴을 학습
# =============================================
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, asdict, field
from typing import Callable, Optional

import config
from llm_client import call_text_llm

try:
    from .memory import AgentMemories
except Exception:
    AgentMemories = None  # type: ignore


JUDGE_MODEL = os.getenv("JUDGE_MODEL", "claude-sonnet-4-6")
JUDGE_ENABLED = os.getenv("JUDGE_ENABLED", "1") not in ("0", "false", "False", "")

# 출력 포맷: 판정/점수/이유(2~3줄)/Bull핵심/Bear핵심 = 5블록 ≈ 350~500자.
# 한국어 ≈ 600~900 토큰. 500 은 Bear 핵심이 마지막에 절단되는 사례 발생
# (관측: '스테이블코인 7d -0.85B' 직후에서 끊김 — 마지막 줄 미완성).
# 1500 으로 상향: reasoning 이 길어지거나 양측 핵심줄이 풍부해지는 경우에도 안전.
# (900 → 1500 — 판정 reasoning 이 길어지는 사례에서 마지막 줄 잘림 방지)
JUDGE_MAX_OUTPUT_TOKENS = int(os.getenv("JUDGE_MAX_OUTPUT_TOKENS", "1500"))

JUDGE_SYSTEM = """당신은 BTC 선물 시장의 'Investment Judge(투자 심판)'입니다.
시장 판단·setup 품질·계좌 실행 허가·최종 행동을 서로 다른 계층으로 판정하세요.
역할: Bull Researcher 와 Bear Researcher 의 토론을 공정하게 듣고,
어느 쪽의 논리가 현재 데이터에 더 잘 부합하는지 판정한 뒤 명확한 방향성 결론을 내립니다.

판정 원칙:
1. 편향 없이 두 주장의 근거 강도를 비교하세요. 주장의 분량이 아니라 데이터와의 정합성으로 평가.
2. 한쪽이 명백히 우세하면 그 방향을 선택. 데이터 근거가 비슷하면 '중립'을 선택.
3. '중립'은 진짜 불확실한 경우에만 — 애매모호함 회피용으로 쓰지 마세요.
4. 판정 이유를 구체적 데이터 근거(가격 구조, 파생심리, 거시)로 2~3줄 서술.
5. 다음 단계(Risk Triad)가 이 결론을 토대로 리스크 규모를 논의할 수 있도록
   'Bull 의 핵심 근거 한 줄 / Bear 의 핵심 근거 한 줄' 을 마지막에 요약.
6. 점수는 -2~+2 정수로만 평가하세요. +2는 해당 축이 판정 방향을 강하게 지지,
   0은 중립/불충분, -2는 판정 방향에 강하게 반대한다는 뜻입니다.
7. 계좌 제한 때문에 시장 방향이나 setup을 중립·no_edge·no_trade로 덮어쓰지 마세요.
8. blocked/hard_block은 계좌 기준 신규 실행만 제한합니다. 데이터 무결성 오류가 아니면 기존 포지션 축소·청산은 허용 가능한 행동으로 남기세요.
9. 금융 조언 회피성 문구 없이 단호하게 판정하되, 수치·트리거·무효화 조건은 데이터에서만 가져오세요.

출력 형식 (반드시 준수 — 라벨 표기 그대로):
판정: [상방 우위 / 하방 우위 / 중립]
시장 확신: [0~100 정수]
시장 레짐: [한 줄]
무효화: [가격 또는 조건 한 줄]
Setup action: [enter_long_now / enter_short_now / wait_for_trigger / no_edge 등]
진입 기대값: [poor / acceptable / conditional_good / good / excellent]
계좌 실행 허가: [allow / reduce_size_only / manual_confirm_required / cooldown_required / blocked / hard_block]
계좌 제한 이유: [한 줄]
금지 행동: [쉼표 구분]
Final action: [setup과 계좌 오버레이를 합성한 한 줄]
점수: price_structure=0, momentum=0, derivatives=0, macro=0, account_risk_fit=0, counter_scenario=0
이유: [2~3줄 구체적 근거 — 가격 구조·파생·거시 중 최소 2축 인용]
Bull 핵심: [한 줄, 60~120자]
Bear 핵심: [한 줄, 60~120자]

분량 규칙:
- 전체 600~1000자. 모든 라벨이 빠지거나 문장이 중간에서 끊기지 않도록 분량을 사전에 조절하세요.
- HTML 금지. 마크다운(**굵게**)은 핵심 강조에 한해 허용 (### 헤더·--- 가로줄은 출력 형식이 깨지므로 금지)."""

JUDGE_USER_TEMPLATE = """{pair_label} 현재 데이터 및 Bull/Bear 토론 결과입니다.

{context_blob}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Bull Researcher 최종 발언]
{bull_final}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Bear Researcher 최종 발언]
{bear_final}
{past_memories_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
위 데이터와 토론을 바탕으로 공정하게 판정하세요."""


ProgressCallback = Callable[[str, str], None]


@dataclass
class JudgeResult:
    """투자 심판 결과."""
    enabled: bool
    verdict: str        # "상방 우위" | "하방 우위" | "중립"
    reasoning: str      # 판정 이유 (2~3줄)
    bull_key: str       # Bull 핵심 근거 한 줄
    bear_key: str       # Bear 핵심 근거 한 줄
    raw_text: str       # LLM 원본 출력
    rubric_scores: dict[str, int] = field(default_factory=dict)
    market_view: dict = field(default_factory=lambda: {"direction": "중립", "confidence": None, "regime": None, "invalidation": None})
    setup_view: dict = field(default_factory=lambda: {"action_verdict": "wait_for_trigger", "entry_expectancy": "acceptable", "trigger_condition": None, "risk_reward": None})
    account_permission: dict = field(default_factory=lambda: {"execution_permission": "manual_confirm_required", "entry_expectancy": "acceptable", "forbidden_actions": [], "reason": "decision support unavailable"})
    final_action: str = "wait_for_trigger_with_manual_confirm"
    decision_support: dict = field(default_factory=dict)
    model: str = ""
    elapsed_s: float = 0.0
    error: Optional[str] = None

    def to_payload(self) -> dict:
        return asdict(self)


def _parse_judge_output(text: str) -> dict:
    """Judge LLM 출력에서 구조화된 필드 추출."""
    lines = text.strip().splitlines()
    result = {
        "verdict": "",
        "reasoning": "",
        "bull_key": "",
        "bear_key": "",
        "rubric_scores": {},
        "market_confidence": None,
        "market_regime": "",
        "invalidation": "",
        "setup_action": "",
        "entry_expectancy": "",
        "execution_permission": "",
        "account_reason": "",
        "forbidden_actions": [],
        "final_action": "",
    }
    reasoning_lines = []
    in_reasoning = False

    for line in lines:
        stripped = _strip_markdown_line(line)
        if m := re.match(r"판정\s*[:：]\s*(.+)$", stripped):
            result["verdict"] = _normalize_verdict(m.group(1))
            in_reasoning = False
        elif m := re.match(r"점수\s*[:：]\s*(.+)$", stripped):
            score_text = m.group(1).strip()
            result["rubric_scores"] = {
                key: int(val)
                for key, val in re.findall(r'([a-z_]+)\s*=\s*(-?\d+)', score_text)
            }
            in_reasoning = False
        elif m := re.match(r"이유\s*[:：]\s*(.*)$", stripped):
            val = m.group(1).strip()
            if val:
                reasoning_lines.append(val)
            in_reasoning = True
        elif m := re.match(r"시장\s*확신\s*[:：]\s*(\d+(?:\.\d+)?)", stripped):
            result["market_confidence"] = float(m.group(1))
            in_reasoning = False
        elif m := re.match(r"시장\s*레짐\s*[:：]\s*(.+)$", stripped):
            result["market_regime"] = m.group(1).strip()
            in_reasoning = False
        elif m := re.match(r"무효화\s*[:：]\s*(.+)$", stripped):
            result["invalidation"] = m.group(1).strip()
            in_reasoning = False
        elif m := re.match(r"Setup\s*action\s*[:：]\s*(.+)$", stripped, re.IGNORECASE):
            result["setup_action"] = m.group(1).strip()
            in_reasoning = False
        elif m := re.match(r"진입\s*기대값\s*[:：]\s*(.+)$", stripped):
            result["entry_expectancy"] = m.group(1).strip()
            in_reasoning = False
        elif m := re.match(r"계좌\s*실행\s*허가\s*[:：]\s*(.+)$", stripped):
            result["execution_permission"] = m.group(1).strip()
            in_reasoning = False
        elif m := re.match(r"계좌\s*제한\s*이유\s*[:：]\s*(.+)$", stripped):
            result["account_reason"] = m.group(1).strip()
            in_reasoning = False
        elif m := re.match(r"금지\s*행동\s*[:：]\s*(.+)$", stripped):
            result["forbidden_actions"] = [item.strip() for item in re.split(r"[,|]", m.group(1)) if item.strip()]
            in_reasoning = False
        elif m := re.match(r"Final\s*action\s*[:：]\s*(.+)$", stripped, re.IGNORECASE):
            result["final_action"] = m.group(1).strip()
            in_reasoning = False
        elif m := re.match(r"Bull\s*핵심\s*[:：]\s*(.+)$", stripped, re.IGNORECASE):
            result["bull_key"] = m.group(1).strip()
            in_reasoning = False
        elif m := re.match(r"Bear\s*핵심\s*[:：]\s*(.+)$", stripped, re.IGNORECASE):
            result["bear_key"] = m.group(1).strip()
            in_reasoning = False
        elif in_reasoning and stripped:
            reasoning_lines.append(stripped)

    result["reasoning"] = " ".join(reasoning_lines)
    return result


def _strip_markdown_line(line: str) -> str:
    """판정 라벨 주변의 가벼운 마크다운 문법을 제거한다."""
    stripped = str(line or "").strip()
    stripped = re.sub(r"^\s*(?:[-+]\s+|>\s*)+", "", stripped)
    stripped = re.sub(r"^\s*#{1,6}\s*", "", stripped)
    stripped = re.sub(r"<[^>]+>", "", stripped)
    stripped = stripped.replace("`", "").replace("*", "")
    return stripped.strip()


def _normalize_verdict(value: str) -> str:
    """마크다운/수식어가 섞인 판정을 표준 3값으로 정규화."""
    cleaned = _strip_markdown_line(value)
    candidates: list[tuple[int, str]] = []
    for keyword, verdict in (
        ("상방", "상방 우위"),
        ("매수", "상방 우위"),
        ("하방", "하방 우위"),
        ("매도", "하방 우위"),
        ("중립", "중립"),
        ("홀드", "중립"),
    ):
        pos = cleaned.find(keyword)
        if pos >= 0:
            candidates.append((pos, verdict))
    if candidates:
        return min(candidates, key=lambda item: item[0])[1]
    return cleaned


def _model_label() -> str:
    return config.ANTHROPIC_MODEL if config.LLM_PROVIDER == "anthropic" else config.LLM_MODEL


def _call_llm(system: str, user: str) -> str:
    return call_text_llm(
        system_prompt=system,
        user_prompt=user,
        max_tokens=JUDGE_MAX_OUTPUT_TOKENS,
    )


def run_judge(
    context_blob: str,
    pair_label: str,
    bull_final: str,
    bear_final: str,
    agent_memories: Optional["AgentMemories"] = None,
    memory_query: str = "",
    progress_cb: Optional[ProgressCallback] = None,
    decision_support: Optional[dict] = None,
) -> JudgeResult:
    """
    Bull/Bear 토론 결과를 받아 방향성을 판정한다.

    Parameters
    ----------
    context_blob : str
        공통 시장 데이터 블록.
    pair_label : str
        "BTC/USDC" 등.
    bull_final, bear_final : str
        Bull/Bear 최종 발언.
    agent_memories : AgentMemories, optional
        judge 전용 메모리 — 과거 중재 패턴 회상.
    memory_query : str
        BM25 쿼리용 상황 요약 문자열.
    progress_cb : callable, optional
        SSE 진행률 콜백.
    """
    if not JUDGE_ENABLED:
        return JudgeResult(enabled=False, verdict="", reasoning="", bull_key="", bear_key="", raw_text="")
    if not config.llm_api_key_configured():
        return JudgeResult(enabled=False, verdict="", reasoning="", bull_key="", bear_key="", raw_text="",
                           error="LLM 설정 미완료")
    if not bull_final and not bear_final:
        return JudgeResult(enabled=False, verdict="", reasoning="", bull_key="", bear_key="", raw_text="",
                           error="Bull/Bear 발언 없음 — 토론 미수행")

    if progress_cb:
        progress_cb("judge", "투자 심판 중재 중")

    # judge 메모리 회상
    past = ""
    if agent_memories is not None:
        _query = memory_query or context_blob[:200]
        past = agent_memories.recall("judge", _query, top_k=2)

    user_prompt = JUDGE_USER_TEMPLATE.format(
        pair_label=pair_label,
        context_blob=context_blob,
        bull_final=bull_final or "(Bull 발언 없음)",
        bear_final=bear_final or "(Bear 발언 없음)",
        past_memories_block=(
            f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{past}\n"
            if past else ""
        ),
    )

    t0 = time.time()
    try:
        raw = _call_llm(JUDGE_SYSTEM, user_prompt)
    except Exception as exc:
        return JudgeResult(
            enabled=True, verdict="중립", reasoning="", bull_key="", bear_key="",
            raw_text="", error=f"{type(exc).__name__}: {exc}",
        )
    elapsed = time.time() - t0

    parsed = _parse_judge_output(raw)
    decision = decision_support if isinstance(decision_support, dict) else {}
    execution_permission = (
        decision.get("account_execution_permission")
        or decision.get("execution_permission")
        or parsed.get("execution_permission")
        or "manual_confirm_required"
    )
    entry_expectancy = (
        decision.get("setup_quality")
        or decision.get("entry_expectancy")
        or parsed.get("entry_expectancy")
        or "acceptable"
    )
    forbidden_actions = (
        decision.get("forbidden_action_codes")
        or decision.get("forbidden_actions")
        or parsed.get("forbidden_actions")
        or []
    )
    execution_reasons = decision.get("execution_reasons") or []
    account_reason = (
        "; ".join(str(item) for item in execution_reasons)
        or parsed.get("account_reason")
        or "decision support unavailable"
    )
    market_view = {
        "direction": parsed["verdict"] or decision.get("market_direction") or "중립",
        "confidence": parsed.get("market_confidence"),
        "regime": parsed.get("market_regime") or decision.get("market_regime"),
        "invalidation": parsed.get("invalidation") or decision.get("invalidation"),
    }
    setup_view = {
        "action_verdict": decision.get("setup_action_verdict") or parsed.get("setup_action") or "wait_for_trigger",
        "entry_expectancy": entry_expectancy,
        "trigger_condition": decision.get("trigger_condition"),
        "risk_reward": decision.get("risk_reward"),
    }
    return JudgeResult(
        enabled=True,
        verdict=parsed["verdict"] or "중립",
        reasoning=parsed["reasoning"],
        bull_key=parsed["bull_key"],
        bear_key=parsed["bear_key"],
        raw_text=raw,
        rubric_scores=parsed.get("rubric_scores") or {},
        market_view=market_view,
        setup_view=setup_view,
        account_permission={
            "execution_permission": execution_permission,
            "entry_expectancy": entry_expectancy,
            "forbidden_actions": list(forbidden_actions),
            "reason": account_reason,
        },
        final_action=decision.get("final_action") or parsed.get("final_action") or "wait_for_trigger_with_manual_confirm",
        model=_model_label(),
        elapsed_s=round(elapsed, 2),
        decision_support=decision,
    )


def format_judge_block(judge: Optional[JudgeResult]) -> str:
    """
    Judge 결과를 최종 프롬프트 주입용 블록으로 변환.
    비활성/실패면 빈 문자열.
    """
    if judge is None or not judge.enabled:
        return ""
    if judge.error and not judge.verdict:
        return f"[투자 심판]\n  수행 실패 — {judge.error}"

    lines = ["[투자 심판 결론]"]
    lines.append(f"  판정: {judge.verdict}")
    if judge.market_view:
        lines.append(f"  market_view: {judge.market_view}")
    if judge.setup_view:
        lines.append(f"  setup_view: {judge.setup_view}")
    if judge.account_permission:
        lines.append(f"  account_permission: {judge.account_permission}")
    if judge.final_action:
        lines.append(f"  final_action: {judge.final_action}")
    if judge.rubric_scores:
        score_line = ", ".join(f"{k}={v}" for k, v in judge.rubric_scores.items())
        lines.append(f"  점수: {score_line}")
    if judge.reasoning:
        lines.append(f"  이유: {judge.reasoning}")
    if judge.bull_key:
        lines.append(f"  Bull 핵심: {judge.bull_key}")
    if judge.bear_key:
        lines.append(f"  Bear 핵심: {judge.bear_key}")
    if judge.error:
        lines.append(f"  ⚠️ 일부 실패 — {judge.error}")
    return "\n".join(lines)
