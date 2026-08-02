# =============================================
# Risk Triad Debate Runner (Aggressive / Conservative / Neutral)
# =============================================
# 원본: TradingAgents/tradingagents/graph/conditional_logic.py + risk_debators
# 적용:
#   - Bull/Bear 토론 결과 + 공통 데이터를 입력으로
#   - 3자(공격/보수/중립)가 리스크 관점에서 추가 토론
#   - 결과는 최종 analyze_with_llm() 의 [사전 토론] 블록에 추가 주입
# =============================================
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

import config
from llm_client import call_text_llm
from .risk_prompts import (
    AGGRESSIVE_SYSTEM,
    CONSERVATIVE_SYSTEM,
    NEUTRAL_SYSTEM,
    RISK_USER_TEMPLATE,
    risk_opponent_block,
)

# AgentMemories 는 선택적 의존
try:
    from .memory import AgentMemories
except Exception:
    AgentMemories = None  # type: ignore


# ── 설정 ──────────────────────────────────────────
# Risk Triad 모델. 필요 시 env 로 오버라이드.
RISK_MODEL = os.getenv("RISK_MODEL", "claude-haiku-4-5-20251001")

# 한 라운드 = Aggressive → Conservative → Neutral 순서로 1발언씩.
# 기본 1라운드 (총 3회 호출). 2라운드면 6회 — 토론이 길어진다.
RISK_MAX_ROUNDS = int(os.getenv("RISK_MAX_ROUNDS", "1"))

# Risk Triad 자체를 끄고 싶을 때: RISK_ENABLED=0
RISK_ENABLED = os.getenv("RISK_ENABLED", "1") not in ("0", "false", "False", "")

# 출력 목표: 각 900~1100자 이내 + 마지막 권고 블록.
# 5500 토큰: 프롬프트 위반으로 길게 써도 권고 블록이 잘릴 가능성이 낮다.
# (4000 → 5500 상향 — 3개 에이전트가 길게 발언할 때 마지막 권고가 잘리는 사례 관측)
RISK_MAX_OUTPUT_TOKENS = int(os.getenv("RISK_MAX_OUTPUT_TOKENS", "5500"))


# 발언 순서 — Aggressive 가 먼저 치고 나가면 Conservative/Neutral 이 반박/중재하는 구조.
SPEAKING_ORDER = ("aggressive", "conservative", "neutral")
SIDE_META = {
    "aggressive":    ("Aggressive", "⚔️", AGGRESSIVE_SYSTEM),
    "conservative":  ("Conservative", "🛡️", CONSERVATIVE_SYSTEM),
    "neutral":       ("Neutral", "⚖️", NEUTRAL_SYSTEM),
}


@dataclass
class RiskTurn:
    """한 에이전트의 한 발언."""
    side: str          # "aggressive" | "conservative" | "neutral"
    round_index: int   # 0부터 시작
    content: str
    model: str
    elapsed_s: float


@dataclass
class RiskTriadResult:
    """Risk Triad 토론 전체 결과."""
    enabled: bool
    rounds: int
    turns: list[RiskTurn] = field(default_factory=list)
    final_aggressive: str = ""
    final_conservative: str = ""
    final_neutral: str = ""
    error: Optional[str] = None
    decision_support: dict = field(default_factory=dict)
    market_view: dict = field(default_factory=lambda: {"direction": "중립", "regime": None, "invalidation": None})
    setup_view: dict = field(default_factory=lambda: {"action_verdict": "wait_for_trigger", "entry_expectancy": "acceptable", "trigger_condition": None, "risk_reward": None})
    account_permission: dict = field(default_factory=lambda: {"execution_permission": "manual_confirm_required", "entry_expectancy": "acceptable", "forbidden_actions": [], "reason": "decision support unavailable"})
    final_action: str = "wait_for_trigger_with_manual_confirm"

    def to_payload(self) -> dict:
        return {
            "enabled": self.enabled,
            "rounds": self.rounds,
            "turns": [asdict(t) for t in self.turns],
            "final_aggressive": self.final_aggressive,
            "final_conservative": self.final_conservative,
            "final_neutral": self.final_neutral,
            "error": self.error,
            "decision_support": self.decision_support,
            "market_view": self.market_view,
            "setup_view": self.setup_view,
            "account_permission": self.account_permission,
            "final_action": self.final_action,
        }


ProgressCallback = Callable[[str, str], None]


def _model_label() -> str:
    return config.ANTHROPIC_MODEL if config.LLM_PROVIDER == "anthropic" else config.LLM_MODEL


def _call_llm(system: str, user: str) -> str:
    return call_text_llm(
        system_prompt=system,
        user_prompt=user,
        max_tokens=RISK_MAX_OUTPUT_TOKENS,
    )


def run_risk_triad(
    context_blob: str,
    pair_label: str,
    bull_final: str,
    bear_final: str,
    max_rounds: Optional[int] = None,
    progress_cb: Optional[ProgressCallback] = None,
    agent_memories: Optional["AgentMemories"] = None,
    memory_query: str = "",
    judge_block: str = "",
    decision_support: Optional[dict] = None,
) -> RiskTriadResult:
    """
    Aggressive/Conservative/Neutral 3자 토론을 실행한다.

    Parameters
    ----------
    context_blob : str
        공통 데이터 블록 (analyzer._build_context_blob 결과).
    pair_label : str
        "BTC/USDC" 등.
    bull_final, bear_final : str
        직전 Bull/Bear 토론 최종 발언. 비어 있어도 동작.
    max_rounds : int, optional
        None 이면 env RISK_MAX_ROUNDS 사용.
    progress_cb : callable, optional
        (phase, detail) — phase 는 "risk_aggressive"/"risk_conservative"/"risk_neutral".
    agent_memories : AgentMemories, optional
        역할별 과거 메모리 — aggressive/conservative/neutral 각자의 회상.
    memory_query : str
        BM25 쿼리용 상황 요약 문자열.
    judge_block : str
        투자 심판 결론 블록. 빈 문자열이면 생략.

    Returns
    -------
    RiskTriadResult
    """
    rounds = max_rounds if max_rounds is not None else RISK_MAX_ROUNDS

    if not RISK_ENABLED:
        return RiskTriadResult(enabled=False, rounds=0)
    if not config.llm_api_key_configured():
        return RiskTriadResult(enabled=False, rounds=0, error="LLM 설정 미완료")

    result = RiskTriadResult(enabled=True, rounds=rounds)

    _query = memory_query or context_blob[:200]
    decision = decision_support if isinstance(decision_support, dict) else {}
    result.decision_support = decision
    result.market_view = {
        "direction": decision.get("market_direction", "중립"),
        "regime": decision.get("market_regime"),
        "invalidation": decision.get("invalidation"),
    }
    result.setup_view = {
        "action_verdict": decision.get("setup_action_verdict", "wait_for_trigger"),
        "entry_expectancy": decision.get("setup_quality") or decision.get("entry_expectancy", "acceptable"),
        "trigger_condition": decision.get("trigger_condition"),
        "risk_reward": decision.get("risk_reward"),
    }
    result.account_permission = {
        "execution_permission": decision.get("account_execution_permission") or decision.get("execution_permission", "manual_confirm_required"),
        "entry_expectancy": decision.get("setup_quality") or decision.get("entry_expectancy", "acceptable"),
        "forbidden_actions": list(decision.get("forbidden_action_codes") or decision.get("forbidden_actions") or []),
        "reason": "; ".join(str(item) for item in (decision.get("execution_reasons") or [])) or "decision support unavailable",
    }
    result.final_action = decision.get("final_action", "wait_for_trigger_with_manual_confirm")
    last = {"aggressive": "", "conservative": "", "neutral": ""}

    try:
        for r in range(rounds):
            for side in SPEAKING_ORDER:
                label, icon, system_prompt = SIDE_META[side]

                if progress_cb:
                    progress_cb(
                        f"risk_{side}",
                        f"{label} 라운드 {r + 1}/{rounds} 분석 중",
                    )

                opponent_block = risk_opponent_block(
                    aggressive_last=last["aggressive"],
                    conservative_last=last["conservative"],
                    neutral_last=last["neutral"],
                    speaking_side=side,
                )

                # 역할별 메모리 회상 (첫 라운드에만)
                past = ""
                if r == 0 and agent_memories is not None:
                    past = agent_memories.recall(side, _query, top_k=2)

                # 이번 라운드에서 본인 외 누군가 발언이 있었는지 → 반박 지시
                has_opponent = any(v for k, v in last.items() if k != side)
                rebuttal_instruction = (
                    "다른 두 분석관의 논리에서 약한 부분을 구체적으로 지적하고 "
                    "당신의 리스크 관점을 관철하세요."
                    if has_opponent
                    else "당신의 관점을 선제적으로 펼치세요."
                )

                user_prompt = RISK_USER_TEMPLATE.format(
                    pair_label=pair_label,
                    context_blob=context_blob,
                    bull_final=bull_final or "(직전 Bull 의견 없음)",
                    bear_final=bear_final or "(직전 Bear 의견 없음)",
                    judge_block=(
                        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{judge_block}\n"
                        if judge_block else ""
                    ),
                    opponent_block=opponent_block,
                    past_memories_block=(
                        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{past}\n"
                        if past else ""
                    ),
                    rebuttal_instruction=rebuttal_instruction,
                )

                t0 = time.time()
                reply = _call_llm(system_prompt, user_prompt)
                elapsed = time.time() - t0

                result.turns.append(RiskTurn(
                    side=side,
                    round_index=r,
                    content=reply,
                    model=_model_label(),
                    elapsed_s=round(elapsed, 2),
                ))
                last[side] = reply

        result.final_aggressive = last["aggressive"]
        result.final_conservative = last["conservative"]
        result.final_neutral = last["neutral"]
        return result

    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        result.final_aggressive = last["aggressive"]
        result.final_conservative = last["conservative"]
        result.final_neutral = last["neutral"]
        return result


def format_risk_block(result: RiskTriadResult) -> str:
    """
    Risk Triad 토론 결과를 최종 프롬프트 주입용 텍스트 블록으로 변환.
    비활성/실패면 빈 문자열.
    """
    if not result.enabled:
        return ""
    if result.error and not result.turns:
        return f"[사전 리스크 토론]\n  수행 실패 — {result.error}"
    if not result.turns:
        return ""

    lines = ["[사전 리스크 토론 — Aggressive vs Conservative vs Neutral]"]
    lines.append(
        "  ⚠️ 세 관점 모두 같은 데이터를 보지만 리스크 성향이 서로 다릅니다. "
        "최종 애널리스트는 공격/보수 양 극단을 비교하고, 중도의 균형안을 참고해 "
        "'📝 대응' 섹션의 공격적·보수적 라인을 결정하세요."
    )

    for t in result.turns:
        label, icon, _ = SIDE_META[t.side]
        header = f"\n[라운드 {t.round_index + 1} · {icon} {label}]"
        lines.append(header)
        for raw in t.content.splitlines():
            lines.append(f"  {raw}" if raw else "")

    if result.error:
        lines.append(f"\n  ⚠️ 토론 일부 실패 — {result.error}")

    return "\n".join(lines)
