# ₿ BTC Signal Analyzer

개인 로컬 전용 비트코인 시장 분석 대시보드.
Binance/Bybit 시장 데이터 + provider-agnostic LLM 분석 + 거시경제 지표를 하나의 웹 인터페이스로 제공합니다.

![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green?logo=fastapi)
![LLM](https://img.shields.io/badge/LLM-OpenAI%20compatible-blue)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## 주요 기능

### 📈 실시간 시장 데이터
- Binance 선물 WebSocket 연결 (aggTrade + kline 스트림)
- 다중 타임프레임 지원: 15m · 1h · 4h · 1d
- ECharts 기반 캔들스틱 차트 (볼린저 밴드, EMA, SMA 오버레이)
- RSI · MACD · 거래량 서브차트 실시간 업데이트

### 🤖 LLM 분석
- Anthropic Claude, OpenAI, OpenAI-compatible Chat Completions 엔드포인트 지원
- 멀티 타임프레임 기술적 지표를 종합한 분석 리포트
- 롱/숏/관망 신호 + 신뢰도 점수 출력
- 진입가 · 목표가 · 손절가 자동 산출
- 자동매매나 주문 실행 기능은 제공하지 않습니다.

### 🌍 거시경제 지표
매크로 환경이 BTC에 미치는 영향을 실시간 카드로 표시합니다.

| 지표 | 소스 | 설명 |
|------|------|------|
| 10Y 국채금리 (^TNX) | yfinance | 실시간 — 금리 상승 → BTC 부정적 |
| 5Y 국채금리 (^FVX)  | yfinance | 실시간 — 단기 금리 환경 |
| 달러 인덱스 (DX-Y.NYB) | yfinance | 실시간 — 달러 강세 → BTC 부정적 |
| HYG/LQD 비율 | yfinance | 신용 스프레드 프록시 — 확대 시 리스크오프 선행 신호 |
| IBIT (현물 BTC ETF) | yfinance | 기관 수급 프록시 — 거래량/20MA 추적 |
| 스테이블코인 시총 | DefiLlama | 유동성 공급 지표 |
| USDT 도미넌스 | DefiLlama | 리스크 온/오프 심리 |
| BTC 도미넌스 | CoinGecko | 알트코인 자금 유입 여부 |

각 지표는 **5일 변화량**, **20일 Z-스코어**, **레짐(상승/하락/횡보)** 을 함께 표시합니다.
서버가 켜져 있는 동안 시간축 히스토리를 누적해 24h · 72h · 7d 변화도 함께 추적합니다.

### 💼 바이낸스 계좌 연동 (선택)
- 선물 계좌 잔고 · 포지션 실시간 조회
- 진입가 대비 현재 손익 표시
- 최근 계좌 히스토리를 바탕으로 12h · 72h · 7d 운영 맥락 요약 제공

### 🔑 웹 기반 API 키 설정
- 최초 실행 시 키 입력 모달 자동 표시
- LLM API 키 (분석 실행 시 필수) / Binance API (계좌 조회 시 선택)
- 입력값은 로컬 `.env` 파일에만 저장 — 서버 재시작 없이 반영

---

## 시작하기

### 1. 저장소 클론

```bash
git clone https://github.com/toririn1/bitcoin-trading-manager.git
cd bitcoin-trading-manager
```

### 2. 로컬 openai-oauth 프록시 실행

OpenAI 종량제 API key 없이 로컬 프록시를 기본 provider로 사용합니다.

```bash
node packages/openai-oauth/dist/cli.js --port 10532 --models gpt-5.6-sol --reasoning-effort medium
```

### 3. `.env` 설정

```env
LLM_PROVIDER=openai_oauth
LLM_BASE_URL=http://127.0.0.1:10532/v1
LLM_MODEL=gpt-5.6-sol
LLM_API_KEY=
ANALYSIS_COOLDOWN_SECS=0
ANALYSIS_DEBOUNCE_SECS=5
PREVENT_CONCURRENT_ANALYSIS=true
```

### 4. 분석기 실행

```bash
./run.sh
```

브라우저에서 **http://localhost:8000** 접속.

### 선택: API 키 준비

| 키 | 필수 여부 | 발급 주소 |
|----|----------|----------|
| LLM API Key | openai_oauth 사용 시 불필요 | OpenAI, Anthropic 또는 호환 provider |
| Binance API Key + Secret | 선택 | [binance.com → API 관리](https://www.binance.com/ko/my/settings/api-management) |

> FRED API 키는 더 이상 필요하지 않습니다. 금리·달러 데이터는 yfinance 실시간 시세로 대체되었습니다.

> **Binance API 권한 설정:** 분석 전용이면 read-only 권한만 권장합니다. 출금(Withdrawal) 권한은 절대 부여하지 말고, 주문/선물 거래 권한도 켜지 마세요.

### 웹 설정 모달

`openai_oauth` 기본값에서는 LLM API key 없이 configured 상태로 동작합니다.
직접 입력하거나 `.env.example`을 복사해 사용할 수 있습니다.

```bash
cp .env.example .env
# 열어서 값 입력
```

OpenAI 예시:

```env
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your_key
LLM_MODEL=gpt-4.1-mini
LLM_MAX_TOKENS=8000
LLM_TEMPERATURE=0.2
LLM_TIMEOUT_SECS=120
```

OpenAI-compatible 예시:

```env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=your_key
LLM_MODEL=your-chat-model
```

Anthropic 예시:

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your_key
ANTHROPIC_MODEL=claude-sonnet-4-6
```

---

## 프로젝트 구조

```
bitcoin-trading-manager/
├── server.py            # FastAPI 메인 서버 (SSE 스트리밍, API 라우트)
├── analyzer.py          # LLM 프롬프트 빌드 & 응답 파싱
├── llm_client.py        # Anthropic/OpenAI-compatible provider 클라이언트
├── account_history.py   # 계좌 히스토리 저장 & 운영 맥락 요약
├── macro_history.py     # 거시 히스토리 저장 & 24h·72h·7d 요약
├── macro_fetcher.py     # 거시경제 지표 수집 (yfinance / DefiLlama / CoinGecko)
├── market_context.py    # 시장 컨텍스트 문자열 생성
├── indicators.py        # 기술적 지표 계산 (RSI, MACD, BB, 피보나치 등)
├── data_fetcher.py      # Binance REST API OHLCV 수집
├── account_context.py   # Binance 계좌 WebSocket 스트림
├── config.py            # 환경 변수 로드
├── static/
│   └── index.html       # 단일 파일 프론트엔드 (ECharts + Vanilla JS)
├── run.sh               # 설치 & 실행 스크립트
├── requirements.txt     # Python 의존성
├── .env.example         # API 키 템플릿
└── .gitignore
```

---

## 기술 스택

| 분류 | 기술 |
|------|------|
| 백엔드 | FastAPI · Uvicorn · Python 3.9+ |
| 실시간 통신 | WebSocket (Binance) · SSE (Server-Sent Events) |
| AI | Anthropic Claude · OpenAI Chat Completions · OpenAI-compatible endpoints |
| 데이터 | Binance Futures REST/WS · yfinance · DefiLlama · CoinGecko |
| 차트 | Apache ECharts 5 |
| 프론트엔드 | Vanilla JS · HTML/CSS (단일 파일, 빌드 도구 없음) |

---

## 지표 설명

### 기술적 지표
- **RSI(14)**: 과매수(>70) / 과매도(<30) 구분
- **MACD(12,26,9)**: 모멘텀 방향 및 히스토그램 추이
- **볼린저 밴드(20,2)**: 밴드 %B로 가격 위치 정규화
- **SMA 50 / 200**: 중장기 추세 판단
- **EMA 9**: 단기 모멘텀
- **ATR(14)**: 변동성 측정, 손절 거리 산정
- **피보나치 스윙**: 스윙 고저 기반 되돌림 레벨 자동 계산

### 거시경제 지표 해석
- **금리 상승 + 달러 강세** → 위험자산 매도 압력
- **스테이블코인 시총 증가 + USDT 도미넌스 하락** → 크립토 매수세 유입
- **BTC 도미넌스 상승** → 알트코인보다 BTC 선호 (리스크 오프 내 상대 강세)

---

## 주의사항

- **투자 조언이 아닙니다.** 본 프로젝트는 데이터 분석 도구이며, 매매 결과에 대한 책임은 사용자에게 있습니다.
- API 키는 절대 외부에 공유하지 마세요. `.env` 파일은 `.gitignore`에 포함되어 있습니다.
- Binance API 키 생성 시 분석 전용이면 **읽기 전용 권한만** 부여하세요. 출금(Withdrawal) 권한은 절대 추가하지 말고, 주문/선물 거래 권한도 켜지 마세요.
- 이 프로젝트는 분석 리포트와 대시보드만 제공합니다. 자동매매/주문 실행 엔드포인트는 없습니다.

---

## License

MIT

## V2 market engine

The engine_v2 package is the new multi-asset, point-in-time decision-support path. It preserves the existing FastAPI/UI and read-only account integration while separating:

- source event time, publish time, collection time, and backtest availability time;
- closed candles from forming candles;
- actual liquidations, partial public liquidation pulses, and estimated clusters;
- trade-stream CVD from hourly taker buckets;
- actual Greek-delta option risk reversals from estimated proxies;
- deterministic scores, costs, quality gates, portfolio guards, and execution permission from LLM explanations.

V2 endpoints are additive under /api/v2/:

- /api/v2/status
- /api/v2/universe
- /api/v2/products
- /api/v2/provider-health
- /api/v2/data-health
- /api/v2/snapshot
- /api/v2/cross-asset
- /api/v2/factors
- /api/v2/events
- /api/v2/events/manual-intake
- /api/v2/opportunities
- /api/v2/decision
- /api/v2/evaluation/summary
- /api/v2/evaluation/calibration

V2 defaults to explicit live mode. Use V2_MODE=fixture only for the demo endpoint, or V2_MODE=replay for point-in-time storage replay; live never falls back to fixture. V2_LIVE_ENABLED=true is required for live collection. Fixture data is marked synthetic and cannot produce an actionable trade. Product symbols for SOXL and SK Hynix are registered only from discovery; they are not guessed. V2 never creates, changes, cancels, or executes an order.

See CODEX_V2_SOURCE_AUDIT.md, CODEX_V2_SCHEMA.md, CODEX_V2_STATE.md, and CODEX_V2_REPORT.md.
