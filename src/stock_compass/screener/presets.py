"""프리셋 SQL 로더 — `screeners/presets/*.sql`을 자동 발견."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

# 프로젝트 루트 / screeners/presets — config.PROJECT_ROOT 기준
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PRESETS_DIR = _PROJECT_ROOT / "screeners" / "presets"


@dataclass(frozen=True, slots=True)
class PresetInfo:
    name: str  # 파일명에서 .sql 제거
    description: str  # 첫 비공백 주석 라인
    path: Path


class PresetNotFoundError(KeyError):
    """존재하지 않는 프리셋."""


def presets_dir() -> Path:
    return _PRESETS_DIR


def list_presets() -> list[PresetInfo]:
    """presets 디렉토리 스캔. 정렬: 알파벳."""
    if not _PRESETS_DIR.exists():
        _logger.warning("presets 디렉토리 없음: %s", _PRESETS_DIR)
        return []
    items = []
    for path in sorted(_PRESETS_DIR.glob("*.sql")):
        items.append(
            PresetInfo(
                name=path.stem,
                description=_first_comment(path),
                path=path,
            )
        )
    return items


def load_preset(name: str) -> str:
    """프리셋 SQL 읽기. `name`은 확장자 제외."""
    path = _PRESETS_DIR / f"{name}.sql"
    if not path.exists():
        raise PresetNotFoundError(
            f"프리셋 없음: {name!r} (찾은 위치: {path})"
        )
    return path.read_text(encoding="utf-8")


def _first_comment(path: Path) -> str:
    """파일의 첫 비-shebang 주석 — 설명용. 없으면 빈 문자열."""
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("--"):
            # `-- preset: <name>` 형식이면 두 번째 주석을 본문으로
            content = s.lstrip("-").strip()
            if content.startswith("preset:"):
                continue
            return content
        # SQL 코드 시작 — 주석 없음
        break
    return ""
