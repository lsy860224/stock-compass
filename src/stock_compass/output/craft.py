"""Craft 일일 노트 (Markdown) 생성.

Phase 3: 파일 출력만 (`data/craft_export/YYYY-MM-DD.md`).
Phase 5+에서 CRAFT_API_TOKEN으로 직접 발행 예정.

면책: 모든 노트 상단·하단에 자동 삽입 (CLAUDE.md 1).
"""

from __future__ import annotations

import shutil
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path

from stock_compass.config import settings
from stock_compass.factors.base import FactorScore
from stock_compass.scoring.engine import DISCLAIMER, CompositeScore
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
