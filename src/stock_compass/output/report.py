"""보고 dual-sink fan-out (publish_report) — Obsidian + Craft API 동시 발행.

각 sink 실패는 격리: Obsidian 실패가 Craft를, Craft 미설정이 Obsidian을 막지 않음.
kind → Obsidian 하위 폴더 매핑을 한 곳에서 관리.

자동화 task(scoring._run_scheduled_task, screener.weekly_discover)의 단일 진입점.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path

from stock_compass.output._craft_client import CraftAPIError, CraftAuthError
from stock_compass.output.craft_publisher import CraftPublisher
from stock_compass.output.obsidian import ObsidianExporter
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

# kind → Obsidian Reports 하위 폴더
_SUBFOLDER: dict[str, str] = {
    "daily": "01. Daily",
    "batch-us": "02. Batch",
    "batch-kr": "02. Batch",
    "weekly-rescore": "03. Weekly",
    "weekly-discover": "03. Weekly",
    "alerts": "04. Alerts",
}
_DEFAULT_SUBFOLDER = "00. Misc"


@dataclass(frozen=True, slots=True)
class ReportResult:
    kind: str
    obsidian_path: Path | None
    craft_url: str | None
    craft_skipped: str | None  # Craft 미발행 사유 (None이면 발행 성공)

    @property
    def any_delivered(self) -> bool:
        return self.obsidian_path is not None or self.craft_url is not None


def publish_report(
    content: str,
    *,
    on_date: date_cls,
    kind: str,
    title: str,
    filename: str,
    charts: list[tuple[str, bytes]] | None = None,
    craft_folder_id: str | None = None,
) -> ReportResult:
    """보고 마크다운을 Obsidian 볼트 + Craft API에 동시 발행.

    Args:
        kind: 보고 종류 (daily/batch-us/batch-kr/weekly-rescore/weekly-discover/alerts).
            Obsidian 하위 폴더 + Craft dedup key.
        title: 노트 제목 (양쪽 공통)
        filename: Obsidian 파일명 (확장자 없이)
        charts: Craft에만 첨부할 (caption, png) 리스트
    """
    subfolder = _SUBFOLDER.get(kind, _DEFAULT_SUBFOLDER)
    obsidian_path = ObsidianExporter().write_report(
        content,
        on_date=on_date,
        subfolder=subfolder,
        filename=filename,
        title=title,
        kind=kind,
    )

    craft_url: str | None = None
    craft_skipped: str | None = None
    try:
        result = CraftPublisher().publish_daily_note(
            content,
            on_date,
            note_kind=kind,
            title=title,
            charts=charts,
            folder_id=craft_folder_id,
        )
        craft_url = result.url
    except CraftAuthError as e:
        craft_skipped = f"미설정: {e}"
        _logger.debug("Craft 발행 skip (%s): %s", kind, e)
    except CraftAPIError as e:
        craft_skipped = f"API 오류: {e}"
        _logger.warning("Craft 발행 실패 (%s): %s", kind, e)

    return ReportResult(
        kind=kind,
        obsidian_path=obsidian_path,
        craft_url=craft_url,
        craft_skipped=craft_skipped,
    )
