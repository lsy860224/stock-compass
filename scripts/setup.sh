#!/usr/bin/env bash
# ====================================
# stock-compass 프로젝트 초기 셋업 (macOS)
# ====================================
# 사용법: chmod +x scripts/setup.sh && ./scripts/setup.sh

set -euo pipefail

echo "🚀 stock-compass 초기 셋업 (macOS)"
echo "═══════════════════════════════════"
echo ""

# ─────── 1. macOS 확인 ───────
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "❌ 이 스크립트는 macOS 전용입니다. (현재: $(uname -s))"
  exit 1
fi
echo "✅ macOS 환경 확인: $(sw_vers -productVersion)"

# ─────── 2. Homebrew ───────
echo ""
echo "1️⃣  Homebrew 확인..."
if ! command -v brew &> /dev/null; then
  echo "❌ Homebrew 미설치"
  echo "   설치: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
  exit 1
fi
echo "✅ Homebrew $(brew --version | head -1)"

# ─────── 3. uv (Python 패키지 매니저) ───────
echo ""
echo "2️⃣  uv 확인..."
if ! command -v uv &> /dev/null; then
  echo "⚠️  uv 미설치 — 자동 설치 시도..."
  brew install uv
fi
echo "✅ uv $(uv --version)"

# ─────── 4. Python 3.12 ───────
echo ""
echo "3️⃣  Python 3.12 준비..."
uv python install 3.12
echo "✅ Python 3.12 준비 완료"

# ─────── 5. 가상환경 + 의존성 ───────
echo ""
echo "4️⃣  의존성 설치 (uv sync)..."
if [[ ! -f pyproject.toml ]]; then
  echo "❌ pyproject.toml 없음. Phase 0 (Claude Code)를 먼저 실행하세요."
  exit 1
fi
uv sync
echo "✅ 의존성 설치 완료"

# ─────── 6. .env.local ───────
echo ""
echo "5️⃣  환경변수 파일..."
if [[ ! -f .env.local ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env.local
    echo "⚙️  .env.local 생성됨"
    echo "   👉 다음 키를 발급받아 입력하세요:"
    echo "      - ANTHROPIC_API_KEY: https://console.anthropic.com"
    echo "      - FRED_API_KEY: https://fred.stlouisfed.org/docs/api/api_key.html"
    echo "      - DART_API_KEY: https://opendart.fss.or.kr"
  else
    echo "❌ .env.example 없음"
    exit 1
  fi
else
  echo "✅ .env.local 이미 존재 (건너뜀)"
fi

# ─────── 7. 데이터 디렉토리 ───────
echo ""
echo "6️⃣  데이터 디렉토리 준비..."
mkdir -p data/cache data/craft_export data/backups logs
echo "✅ data/, logs/ 디렉토리 준비 완료"

# ─────── 8. DB 초기화 ───────
echo ""
echo "7️⃣  DB 마이그레이션..."
if uv run python -c "from stock_compass.db.migrations import migrate; migrate()" 2>/dev/null; then
  echo "✅ DB 마이그레이션 완료 (data/stock_compass.db)"
else
  echo "⚠️  DB 마이그레이션 함수 미구현 (Phase 2에서 구현)"
fi

# ─────── 9. .gitignore 확인 ───────
echo ""
echo "8️⃣  .gitignore 검증..."
if [[ -f .gitignore ]]; then
  for pattern in ".env.local" "data/*.db" "logs/" "__pycache__"; do
    if ! grep -q "$pattern" .gitignore; then
      echo "⚠️  .gitignore에 '$pattern' 추가 권장"
    fi
  done
  echo "✅ .gitignore 점검 완료"
else
  echo "⚠️  .gitignore 없음 — Phase 0에서 생성됩니다"
fi

# ─────── 10. Git 초기화 ───────
if [[ ! -d .git ]]; then
  echo ""
  echo "9️⃣  Git 초기화..."
  git init -q
  git add .
  git commit -m "feat: stock-compass scaffolding" -q || true
  echo "✅ Git 초기화 완료"
fi

# ─────── 11. CLI 동작 확인 ───────
echo ""
echo "🔟 CLI 동작 확인..."
if uv run python -m stock_compass --help &> /dev/null; then
  echo "✅ stock-compass CLI 실행 가능"
else
  echo "⚠️  CLI 미구현 (Phase 0에서 구현)"
fi

# ─────── 완료 ───────
echo ""
echo "═══════════════════════════════════"
echo "✅ 셋업 완료!"
echo "═══════════════════════════════════"
echo ""
echo "다음 단계:"
echo "  1. .env.local에 API 키 입력 (ANTHROPIC·FRED·DART)"
echo "  2. .env.local의 WATCHLIST_KR / WATCHLIST_US 본인 종목으로 수정"
echo "  3. Claude Code 열기:"
echo "     \"CLAUDE.md를 읽고 docs/IMPLEMENTATION_GUIDE.md의 Phase 0부터 시작해줘\""
echo "  4. Phase 0 → 1 → 2 순서로 진행 (각 Phase 끝나면 git commit)"
echo "  5. Phase 6 완료 후: ./scripts/install_launchd.sh (자동 스케줄)"
echo ""
echo "⚠️  법적 주의:"
echo "  - 이 도구는 본인 사용 한정"
echo "  - 결과를 타인에게 제공·판매 시 유사투자자문업 위반 가능"
echo ""
