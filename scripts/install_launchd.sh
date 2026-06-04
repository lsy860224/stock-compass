#!/usr/bin/env bash
# ====================================
# launchd 자동 스케줄 등록 (macOS)
# ====================================
# Phase 6 완료 후 실행
# 사용법: chmod +x scripts/install_launchd.sh && ./scripts/install_launchd.sh

set -euo pipefail

PROJECT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_TEMPLATE="$PROJECT_PATH/launchd/com.user.stockcompass.plist"
PLIST_INSTALL="$HOME/Library/LaunchAgents/com.user.stockcompass.plist"
LABEL="com.user.stockcompass"

echo "🚀 launchd 자동 스케줄 등록"
echo "═══════════════════════════════════"
echo "프로젝트 경로: $PROJECT_PATH"
echo ""

# ─────── 1. 기존 등록 해제 ───────
if launchctl print "gui/$UID/$LABEL" &> /dev/null; then
  echo "⚙️  기존 등록 감지 — 해제 중..."
  launchctl bootout "gui/$UID/$LABEL" || true
  echo "✅ 기존 등록 해제 완료"
fi

# ─────── 2. plist 템플릿 확인 ───────
if [[ ! -f "$PLIST_TEMPLATE" ]]; then
  echo "❌ 템플릿 없음: $PLIST_TEMPLATE"
  exit 1
fi

# ─────── 3. uv 절대 경로 ───────
UV_PATH="$(command -v uv)"
if [[ -z "$UV_PATH" ]]; then
  echo "❌ uv 미설치. brew install uv 먼저 실행"
  exit 1
fi
echo "✅ uv 경로: $UV_PATH"

# ─────── 4. plist 변수 치환 ───────
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|{{PROJECT_PATH}}|$PROJECT_PATH|g" \
    -e "s|{{UV_PATH}}|$UV_PATH|g" \
    "$PLIST_TEMPLATE" > "$PLIST_INSTALL"
echo "✅ plist 설치: $PLIST_INSTALL"

# ─────── 5. launchd 등록 ───────
launchctl bootstrap "gui/$UID" "$PLIST_INSTALL"
launchctl enable "gui/$UID/$LABEL"
echo "✅ launchd 등록 완료"

# ─────── 6. 확인 ───────
echo ""
echo "현재 등록 상태:"
launchctl print "gui/$UID/$LABEL" | grep -E "(state|program|next run)" | head -10 || true

echo ""
echo "═══════════════════════════════════"
echo "✅ 설치 완료!"
echo "═══════════════════════════════════"
echo ""
echo "예약 시각:"
echo "  - 평일 06:30 KST: 미국 시장 배치"
echo "  - 평일 16:30 KST: 한국 시장 배치"
echo "  - 평일 07:00 KST: 일일 종합 리포트"
echo "  - 토요일 05:00 KST: 유니버스 주간 재채점 (853종목)"
echo "  - 토요일 08:00 KST: 주간 종목 발굴 (weekly-discover)"
echo ""
echo "수동 즉시 실행 (테스트):"
echo "  launchctl kickstart gui/$UID/$LABEL"
echo ""
echo "로그 확인:"
echo "  tail -f $PROJECT_PATH/logs/launchd-stdout.log"
echo "  tail -f $PROJECT_PATH/logs/launchd-stderr.log"
echo ""
echo "해제:"
echo "  ./scripts/uninstall_launchd.sh"
echo ""
echo "⚠️  Mac 절전 모드에서는 동작 X — 'pmset -g' 확인 권장"
