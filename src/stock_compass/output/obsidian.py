"""Obsidian 볼트 직접 기록 (ObsidianExporter) — 자동 보고 dual-sink의 로컬 sink.

launchd 독립 프로세스는 claude.ai MCP에 접근 불가 → 볼트 폴더에 `.md` 파일을
직접 쓴다. 볼트 미설정/미존재(iCloud 미동기)면 graceful skip(None 반환) — 보고
실패가 배치 전체를 막지 않는다.

폴더 구조: <vault>/<reports_subdir>/<subfolder>/<filename>.md
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path

from stock_compass.config import settings
from stock_compass.utils.dates import now_kst
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ObsidianExporter:
    """보고 마크다운을 Obsidian 볼트의 Reports 하위 폴더에 기록."""

    vault_dir: Path | None = settings.obsidian_vault_dir
    reports_subdir: str = settings.obsidian_reports_subdir

    def write_report(
        self,
        content: str,
        *,
        on_date: date_cls,
        subfolder: str,
        filename: str,
        title: str,
        kind: str,
    ) -> Path | None:
        """볼트에 보고 노트 기록 후 경로 반환. 볼트 미가용 시 None (skip).

        Args:
            subfolder: Reports 하위 폴더명 (예: "01. Daily")
            filename: 확장자 없는 파일명 (예: "2026-06-04 US")
            title: frontmatter title
            kind: frontmatter kind/tag (예: "daily", "batch-us")
        """
        base = self._reports_root()
        if base is None:
            return None

        target_dir = base / subfolder
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / f"{_safe_filename(filename)}.md"
            path.write_text(
                _frontmatter(title=title, on_date=on_date, kind=kind) + content,
                encoding="utf-8",
            )
        except OSError as e:
            _logger.warning("Obsidian 보고 기록 실패 (%s/%s): %s", subfolder, filename, e)
            return None

        _logger.info("Obsidian 보고: %s", path)
        return path

    def _reports_root(self) -> Path | None:
        """<vault>/<reports_subdir> — 볼트 미설정/미존재 시 None."""
        if self.vault_dir is None:
            _logger.debug("OBSIDIAN_VAULT_DIR 미설정 — Obsidian sink skip")
            return None
        if not self.vault_dir.exists():
            _logger.warning(
                "Obsidian 볼트 경로 없음 (iCloud 미동기?): %s — skip", self.vault_dir
            )
            return None
        return self.vault_dir / self.reports_subdir


def _frontmatter(*, title: str, on_date: date_cls, kind: str) -> str:
    """YAML frontmatter — Obsidian 속성 + 태그. 본문 앞에 붙임."""
    generated = now_kst().strftime("%Y-%m-%d %H:%M")
    return (
        "---\n"
        f"title: {title}\n"
        f"date: {on_date.isoformat()}\n"
        f"kind: {kind}\n"
        "tags: [stock-compass, 자동보고]\n"
        f"generated: {generated} KST\n"
        "source: stock-compass automation\n"
        "---\n\n"
    )


def _safe_filename(name: str) -> str:
    """파일명에서 경로 구분자·금지문자 제거."""
    bad = '/\\:*?"<>|'
    return "".join("_" if c in bad else c for c in name).strip() or "report"
