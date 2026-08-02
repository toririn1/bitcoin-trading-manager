#!/bin/bash
# =============================================
# Crypto Signal Analyzer - 설치 & 실행 스크립트
# FastAPI + 단일 HTML 버전
# =============================================
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🚀 BTC Signal Analyzer  (FastAPI 버전)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cd "$(dirname "$0")"

if ! command -v python3 &>/dev/null; then
  echo "❌ python3가 필요합니다. https://www.python.org 에서 설치해주세요."
  exit 1
fi

echo "✅ Python: $(python3 --version)"
echo ""
echo "📦 패키지 설치 중..."
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
echo "✅ 패키지 설치 완료"
echo ""
echo "🌐 브라우저에서 http://localhost:8000 으로 접속하세요."
echo "   (종료하려면 Ctrl+C)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# .env 파일 존재 확인
if [ ! -f ".env" ]; then
  echo "⚠️  .env 파일이 없습니다. 로컬 기본값을 사용합니다:"
  echo "   LLM_PROVIDER=openai_oauth"
  echo "   LLM_BASE_URL=http://127.0.0.1:10532/v1"
  echo "   LLM_MODEL=gpt-5.6-sol"
  echo "   openai_oauth에서는 API key가 필요 없습니다."
  echo "   Anthropic/OpenAI API-key provider를 쓸 때만 API key를 설정하세요."
  echo ""
fi

.venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
