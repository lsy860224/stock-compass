#!/usr/bin/env bash
# launchd 등록 해제

set -euo pipefail

LABEL="com.user.stockcompass"
PLIST="$HOME/Library/LaunchAgents/com.user.stockcompass.plist"

echo "🛑 launchd 해제 중..."

if launchctl print "gui/$UID/$LABEL" &> /dev/null; then
  launchctl bootout "gui/$UID/$LABEL"
  echo "✅ launchd bootout 완료"
fi

if [[ -f "$PLIST" ]]; then
  rm "$PLIST"
  echo "✅ plist 삭제: $PLIST"
fi

echo ""
echo "✅ 해제 완료"
