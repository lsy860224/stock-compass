"""macOS 네이티브 알림 (osascript).

`subprocess.run`으로 osascript 호출 — 외부 의존성 없음, 단 macOS 한정.
타 OS에서는 stdout 폴백 + 경고 로그.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

# AppleScript에서 escape 필요한 문자
_ESCAPE_MAP = str.maketrans({'"': '\\"', "\\": "\\\\", "\n": " "})


def macos_notify(
    *,
    title: str,
    body: str,
    subtitle: str | None = None,
) -> bool:
    """macOS 알림센터로 발화. 성공 시 True, 실패·미지원 시 False."""
    if sys.platform != "darwin":
        _logger.warning("[notify-stub] %s — %s", title, body)
        return False
    if shutil.which("osascript") is None:
        _logger.warning("osascript 미발견 (Headless 환경?) — 알림 생략")
        return False

    script_parts = [
        f'display notification "{_esc(body)}"',
        f'with title "{_esc(title)}"',
    ]
    if subtitle:
        script_parts.append(f'subtitle "{_esc(subtitle)}"')
    script = " ".join(script_parts)

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            timeout=5,
            capture_output=True,
            text=True,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        _logger.warning("osascript 호출 실패: %s", e)
        return False

    if result.returncode != 0:
        _logger.warning(
            "osascript 비정상 종료 (%d): %s", result.returncode, result.stderr.strip()
        )
        return False
    return True


def _esc(text: str) -> str:
    return text.translate(_ESCAPE_MAP)
