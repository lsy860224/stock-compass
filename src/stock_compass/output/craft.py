"""Craft 일일 노트 (Markdown) 생성 + Craft Pro API 직접 발행.

CraftExporter: 파일 출력 (`data/craft_export/YYYY-MM-DD.md`)
CraftPublisher: Craft Pro API로 노트 자동 발행 (Phase D)

면책: 모든 노트 상단·하단에 자동 삽입 (CLAUDE.md 1).
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_compass.config import settings
from stock_compass.factors.base import FactorScore
from stock_compass.output._craft_client import (
    CraftAPIError,
    CraftAuthError,
    CraftClient,
)
from stock_compass.scoring.engine import DISCLAIMER, CompositeScore
from stock_compass.utils.dates import now_utc, to_iso_utc
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)
_FACTOR_LABEL = {
    "valuation": "Valuation",
    "fundamentals": "Fundamentals",
    "technical": "Technical",
    "macro": "Macro",
    "sentiment": "Sentiment",
}
_FACTOR_ORDER = ("valuation", "fundamentals", "technical", "macro", "sentiment")


@dataclass(frozen=True, slots=True)
class CraftExporter:
    """워치리스트 일일 점수를 Craft 호환 Markdown으로 변환·저장."""

    export_dir: Path = settings.craft_export_dir

    def render_daily_note(
        self, scores: list[CompositeScore], on_date: date_cls
    ) -> str:
        """5팩터 점수 + 일일 요약 + 빈 매매 일지 섹션을 마크다운으로 직렬화."""
        sections = [
            _header(on_date),
            _summary_section(scores),
            _ranking_table(scores),
            _per_ticker_section(scores),
            _trade_journal_section(),
            _footer(),
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

    def export(self, scores: list[CompositeScore], on_date: date_cls) -> Path:
        """render + write 일괄 처리 (CLI에서 주로 사용)."""
        return self.export_to_file(self.render_daily_note(scores, on_date), on_date)


# ──────────────────────── 섹션 빌더 ────────────────────────


def _header(on_date: date_cls) -> str:
    return (
        f"# stock-compass · {on_date.isoformat()} (KST)\n\n"
        f"> 자동 생성 노트 — **투자 자문 아님.** 본인 판단의 보조 자료."
    )


def _summary_section(scores: list[CompositeScore]) -> str:
    if not scores:
        return "## 일일 요약\n\n- 대상 종목 없음 (워치리스트 비어 있거나 batch 미실행)."

    verdicts = Counter(s.verdict for s in scores)
    interest = [s for s in scores if s.verdict == "관심권"]
    caution = [s for s in scores if s.verdict == "주의"]
    avg = sum(s.total_score for s in scores) / len(scores)
    top = max(scores, key=lambda s: s.total_score)
    bottom = min(scores, key=lambda s: s.total_score)

    lines = [
        "## 일일 요약",
        "",
        f"- **종목 수**: {len(scores)}",
        f"- **관심권 (≥70)**: {verdicts['관심권']}종"
        + (f" — {', '.join(f'`{s.ticker}`' for s in interest)}" if interest else ""),
        f"- **중립 (50~69)**: {verdicts['중립']}종",
        f"- **주의 (<50)**: {verdicts['주의']}종"
        + (f" — {', '.join(f'`{s.ticker}`' for s in caution)}" if caution else ""),
        f"- **점수 분포**: 평균 {avg:.1f} / 최고 {top.total_score:.1f}"
        f" ({top.ticker}) / 최저 {bottom.total_score:.1f} ({bottom.ticker})",
    ]
    return "\n".join(lines)


def _ranking_table(scores: list[CompositeScore]) -> str:
    if not scores:
        return ""
    rows = [
        "## 순위",
        "",
        "| 순 | 종목 | 시장 | 종합 | 판단 | 가격 |",
        "|---:|---|:---:|---:|:---:|---:|",
    ]
    for i, s in enumerate(scores, start=1):
        name = f" {s.name}" if s.name else ""
        price = _format_price(s.price_at_score, s.currency)
        rows.append(
            f"| {i} | `{s.ticker}`{name} | {s.market} |"
            f" {s.total_score:.1f} | {s.verdict} | {price} |"
        )
    return "\n".join(rows)


def _per_ticker_section(scores: list[CompositeScore]) -> str:
    if not scores:
        return ""
    blocks = ["## 종목별 카드"]
    for i, s in enumerate(scores, start=1):
        blocks.append(_ticker_card(i, s))
    return "\n\n".join(blocks)


def _ticker_card(rank: int, s: CompositeScore) -> str:
    name = f" · {s.name}" if s.name else ""
    sector = f"  ·  {s.sector}" if s.sector else ""
    price = _format_price(s.price_at_score, s.currency)
    by_name: dict[str, FactorScore] = {f.name: f for f in s.factors}

    lines = [
        f"### {rank}. `{s.ticker}`{name} [{s.market}] — **{s.verdict}**",
        "",
        f"**종합**: {s.total_score:.1f} / 100  ·  **가격**: {price}{sector}",
        "",
        "| 팩터 | 가중 | 점수 | 메모 |",
        "|---|---:|---:|---|",
    ]
    for fname in _FACTOR_ORDER:
        f = by_name.get(fname)
        if f is None:
            lines.append(f"| {_FACTOR_LABEL[fname]} | — | — | (없음) |")
            continue
        lines.append(
            f"| {_FACTOR_LABEL[fname]} | {f.weight:.0%} |"
            f" {f.score:.1f} | {_clean_note(f.note)} |"
        )

    lines.extend(
        [
            "",
            "**메모**:",
            "",
            "- ",
        ]
    )
    return "\n".join(lines)


def _trade_journal_section() -> str:
    return (
        "## 매매 일지\n\n"
        "> 오늘 본인 매매를 직접 기록. (Phase 6에서 `stock-compass trade add` "
        "명령으로 DB 자동 적재)\n\n"
        "- **매수**:\n"
        "- **매도**:\n"
        "- **관찰**:"
    )


def _footer() -> str:
    return f"---\n\n> **면책**: {DISCLAIMER}"


def _format_price(price: float | None, currency: str | None) -> str:
    if price is None:
        return "—"
    suffix = f" {currency}" if currency else ""
    return f"{price:,.2f}{suffix}"


def _clean_note(note: str) -> str:
    """마크다운 테이블 안전 — 파이프·줄바꿈 제거."""
    return note.replace("|", "/").replace("\n", " ").strip() or "—"


# ──────────────────────── Craft Pro API publisher ────────────────────────


@dataclass(frozen=True, slots=True)
class CraftPublishResult:
    note_id: str
    url: str
    folder_id: str
    published_at: datetime
    is_update: bool


class CraftPublisher:
    """Craft Pro API 자동 발행 + DB 중복 추적 (craft_publications).

    Token/folder_id는 settings에서 lazy 로드. 토큰 없으면 publish 호출 시
    명확한 CraftAuthError raise (호출자가 파일 fallback 안내).
    """

    def __init__(
        self,
        *,
        client: CraftClient | None = None,
        default_folder_id: str | None = None,
    ) -> None:
        self._client = client
        self.default_folder_id = default_folder_id or settings.craft_daily_folder_id

    def publish_daily_note(
        self,
        content: str,
        on_date: date_cls,
        *,
        folder_id: str | None = None,
        note_kind: str = "daily",
    ) -> CraftPublishResult:
        """일일 노트 발행. 같은 (note_kind, on_date)가 이미 있으면 update."""
        target_folder = folder_id or self.default_folder_id
        if not target_folder:
            raise CraftAuthError(
                "CRAFT_DAILY_FOLDER_ID 미설정 — .env.local 확인"
            )

        client = self._get_client()
        title = f"stock-compass · {on_date.isoformat()}"

        from stock_compass.db import get_db_connection

        with get_db_connection() as conn:
            existing = _find_publication(conn, note_kind, on_date)
            if existing:
                response = client.update_note(
                    existing["note_id"],
                    title=title,
                    content_markdown=content,
                )
                is_update = True
            else:
                response = client.post_note(
                    target_folder,
                    title=title,
                    content_markdown=content,
                    idempotency_key=f"sc-{note_kind}-{on_date.isoformat()}",
                )
                is_update = False
            note_id = str(response.get("id", ""))
            url = str(response.get("url", ""))
            _record_publication(
                conn,
                note_kind=note_kind,
                on_date=on_date,
                note_id=note_id,
                folder_id=target_folder,
                url=url,
            )

        _logger.info(
            "Craft %s: %s (%s)", "update" if is_update else "publish", note_id, url
        )
        return CraftPublishResult(
            note_id=note_id,
            url=url,
            folder_id=target_folder,
            published_at=now_utc(),
            is_update=is_update,
        )

    def _get_client(self) -> CraftClient:
        if self._client is not None:
            return self._client
        token = settings.craft_api_token
        if token is None:
            raise CraftAuthError(
                "CRAFT_API_TOKEN 미설정 — .env.local에 추가 후 재시도"
            )
        self._client = CraftClient(
            token=token.get_secret_value(),
            base_url=settings.craft_api_base_url,
        )
        return self._client


def _find_publication(
    conn: sqlite3.Connection, note_kind: str, on_date: date_cls
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, note_id, folder_id, url
        FROM craft_publications
        WHERE note_kind = ? AND on_date = ?
        """,
        (note_kind, on_date.isoformat()),
    ).fetchone()
    return dict(row) if row else None


def _record_publication(
    conn: sqlite3.Connection,
    *,
    note_kind: str,
    on_date: date_cls,
    note_id: str,
    folder_id: str,
    url: str,
) -> None:
    conn.execute(
        """
        INSERT INTO craft_publications
          (note_kind, on_date, note_id, folder_id, url, published_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(note_kind, on_date) DO UPDATE SET
          note_id = excluded.note_id,
          folder_id = excluded.folder_id,
          url = excluded.url,
          published_at = excluded.published_at
        """,
        (
            note_kind,
            on_date.isoformat(),
            note_id,
            folder_id,
            url,
            to_iso_utc(now_utc()),
        ),
    )


# Phase B의 discover에서 import한 ImportError 회피용 — CraftPublisher 노출
__all__ = [
    "CraftAPIError",
    "CraftAuthError",
    "CraftExporter",
    "CraftPublishResult",
    "CraftPublisher",
]
