"""Craft 일일 노트 마크다운 빌더 + 파일 저장 (CraftExporter).

순수 file output — Craft Pro API 호출 없음. Publisher는 craft_publisher.py.
섹션 빌더 함수와 보조 타입은 `_craft_sections`에 있다 (여기서 re-export).
면책: 모든 노트 상단·하단에 자동 삽입 (CLAUDE.md 1).
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path

from stock_compass.config import settings
from stock_compass.output._craft_sections import (
    PreviousScores,
    SectorRanks,
    TokenUsage,
    _change_highlight_section,
    _clean_note,
    _footer,
    _format_price,
    _header,
    _per_ticker_section,
    _ranking_table,
    _summary_section,
    _trade_journal_section,
)
from stock_compass.scoring.engine import CompositeScore
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

# 하위 호환 re-export — 외부(scheduler·reporting 등)가 craft_exporter에서 import.
# _clean_note·_format_price 는 테스트가 직접 import (test_craft.py).
__all__ = [
    "CraftExporter",
    "PreviousScores",
    "SectorRanks",
    "TokenUsage",
    "_clean_note",
    "_format_price",
]


@dataclass(frozen=True, slots=True)
class CraftExporter:
    """워치리스트 일일 점수를 Craft 호환 Markdown으로 변환·저장."""

    export_dir: Path = settings.craft_export_dir

    def render_daily_note(
        self,
        scores: list[CompositeScore],
        on_date: date_cls,
        *,
        previous_scores: PreviousScores | None = None,
        sector_ranks: SectorRanks | None = None,
        token_usage: TokenUsage | None = None,
    ) -> str:
        """5팩터 점수 + 일일 요약 + 빈 매매 일지 섹션을 마크다운으로 직렬화."""
        prev = previous_scores or {}
        ranks = sector_ranks or {}
        sections = [
            _header(on_date),
            _summary_section(scores),
            _change_highlight_section(scores, prev),
            _ranking_table(scores, prev),
            _per_ticker_section(scores, prev, ranks),
            _trade_journal_section(),
            _footer(token_usage),
        ]
        return "\n\n".join(s for s in sections if s).rstrip() + "\n"

    def export_to_file(self, content: str, on_date: date_cls) -> Path:
        """`data/craft_export/YYYY-MM-DD.md` 작성. 기존 파일 있으면 timestamped .bak 백업."""
        self.export_dir.mkdir(parents=True, exist_ok=True)
        path = self.export_dir / f"{on_date.isoformat()}.md"
        if path.exists():
            backup = path.with_suffix(f".md.{time.strftime('%Y%m%d%H%M%S')}.bak")
            shutil.copy2(path, backup)
            _logger.info("기존 노트 백업: %s", backup.name)
        path.write_text(content, encoding="utf-8")
        return path

    def export(
        self,
        scores: list[CompositeScore],
        on_date: date_cls,
        *,
        previous_scores: PreviousScores | None = None,
        sector_ranks: SectorRanks | None = None,
        token_usage: TokenUsage | None = None,
    ) -> Path:
        """render + write 일괄 처리 (CLI에서 주로 사용)."""
        body = self.render_daily_note(
            scores,
            on_date,
            previous_scores=previous_scores,
            sector_ranks=sector_ranks,
            token_usage=token_usage,
        )
        return self.export_to_file(body, on_date)
