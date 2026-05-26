"""Craft 일일 노트 마크다운 빌더 + 파일 저장 (CraftExporter).

순수 file output — Craft Pro API 호출 없음. Publisher는 craft_publisher.py.
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
from stock_compass.markets.base import Market
from stock_compass.scoring.engine import DISCLAIMER, CompositeScore, Verdict
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

# Δ 점수 변화의 신호도 임계 — 이만큼 변하면 highlight 섹션에 포함
_DELTA_HIGHLIGHT_THRESHOLD = 5.0

# 호출자가 주입하는 보조 데이터 타입
PreviousScores = dict[tuple[str, Market], tuple[float, Verdict]]
SectorRanks = dict[tuple[str, Market], tuple[int, int]]
TokenUsage = dict[str, int | float]


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


def _change_highlight_section(
    scores: list[CompositeScore], prev: PreviousScores
) -> str:
    """어제 대비 큰 변화·verdict 변경·신규 진입 highlight (BT1)."""
    if not scores or not prev:
        return ""
    risers: list[tuple[CompositeScore, float, Verdict]] = []
    fallers: list[tuple[CompositeScore, float, Verdict]] = []
    verdict_changes: list[tuple[CompositeScore, Verdict]] = []
    for s in scores:
        key = (s.ticker, s.market)
        if key not in prev:
            continue
        prev_score, prev_verdict = prev[key]
        delta = s.total_score - prev_score
        if prev_verdict != s.verdict:
            verdict_changes.append((s, prev_verdict))
        if delta >= _DELTA_HIGHLIGHT_THRESHOLD:
            risers.append((s, delta, prev_verdict))
        elif delta <= -_DELTA_HIGHLIGHT_THRESHOLD:
            fallers.append((s, delta, prev_verdict))
    if not risers and not fallers and not verdict_changes:
        return ""
    lines = ["## 변화 highlight"]
    if verdict_changes:
        lines.append("\n**판단 변경**:")
        for s, prev_v in verdict_changes:
            lines.append(
                f"- `{s.ticker}`: {prev_v} → **{s.verdict}** ({s.total_score:.1f})"
            )
    if risers:
        lines.append("\n**상승 ▲**:")
        for s, d, _ in sorted(risers, key=lambda x: -x[1]):
            lines.append(
                f"- `{s.ticker}`: {s.total_score - d:.1f} → {s.total_score:.1f} "
                f"(Δ+{d:.1f})"
            )
    if fallers:
        lines.append("\n**하락 ▼**:")
        for s, d, _ in sorted(fallers, key=lambda x: x[1]):
            lines.append(
                f"- `{s.ticker}`: {s.total_score - d:.1f} → {s.total_score:.1f} "
                f"(Δ{d:.1f})"
            )
    return "\n".join(lines)


def _ranking_table(scores: list[CompositeScore], prev: PreviousScores) -> str:
    if not scores:
        return ""
    has_delta = bool(prev)
    header_cols = (
        ["순", "종목", "시장", "종합", "Δ", "판단", "가격"]
        if has_delta
        else ["순", "종목", "시장", "종합", "판단", "가격"]
    )
    align = (
        "|---:|---|:---:|---:|---:|:---:|---:|"
        if has_delta
        else "|---:|---|:---:|---:|:---:|---:|"
    )
    rows = [
        "## 순위",
        "",
        "| " + " | ".join(header_cols) + " |",
        align,
    ]
    for i, s in enumerate(scores, start=1):
        name = f" {s.name}" if s.name else ""
        price = _format_price(s.price_at_score, s.currency)
        if has_delta:
            delta_str = _delta_cell(s, prev)
            rows.append(
                f"| {i} | `{s.ticker}`{name} | {s.market} |"
                f" {s.total_score:.1f} | {delta_str} | {s.verdict} | {price} |"
            )
        else:
            rows.append(
                f"| {i} | `{s.ticker}`{name} | {s.market} |"
                f" {s.total_score:.1f} | {s.verdict} | {price} |"
            )
    return "\n".join(rows)


def _delta_cell(s: CompositeScore, prev: PreviousScores) -> str:
    info = prev.get((s.ticker, s.market))
    if info is None:
        return "—"
    delta = s.total_score - info[0]
    if abs(delta) < 0.05:
        return "0"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}"


def _per_ticker_section(
    scores: list[CompositeScore], prev: PreviousScores, ranks: SectorRanks
) -> str:
    if not scores:
        return ""
    blocks = ["## 종목별 카드"]
    for i, s in enumerate(scores, start=1):
        blocks.append(_ticker_card(i, s, prev, ranks))
    return "\n\n".join(blocks)


def _ticker_card(
    rank: int,
    s: CompositeScore,
    prev: PreviousScores,
    ranks: SectorRanks,
) -> str:
    name = f" · {s.name}" if s.name else ""
    sector = f"  ·  {s.sector}" if s.sector else ""
    price = _format_price(s.price_at_score, s.currency)
    by_name: dict[str, FactorScore] = {f.name: f for f in s.factors}

    # 어제 대비 + sector rank 메타 (BT1 + BT3)
    meta_lines: list[str] = []
    info = prev.get((s.ticker, s.market))
    if info is not None:
        prev_score, prev_verdict = info
        delta = s.total_score - prev_score
        arrow = "▲" if delta >= 0 else "▼"
        change = (
            f" ({prev_verdict} → **{s.verdict}**)"
            if prev_verdict != s.verdict
            else ""
        )
        meta_lines.append(
            f"_어제 {prev_score:.1f} → 오늘 {s.total_score:.1f} "
            f"({arrow}{delta:+.1f}){change}_"
        )
    rank_info = ranks.get((s.ticker, s.market))
    if rank_info is not None and s.sector:
        r, total = rank_info
        meta_lines.append(
            f"_{s.sector} sector {total}종 중 **{r}위**_"
        )

    lines = [
        f"### {rank}. `{s.ticker}`{name} [{s.market}] — **{s.verdict}**",
        "",
        f"**종합**: {s.total_score:.1f} / 100  ·  **가격**: {price}{sector}",
    ]
    if meta_lines:
        lines.append("")
        lines.extend(meta_lines)
    lines.extend(
        [
            "",
            "| 팩터 | 가중 | 점수 | 메모 |",
            "|---|---:|---:|---|",
        ]
    )
    for fname in _FACTOR_ORDER:
        f = by_name.get(fname)
        if f is None:
            lines.append(f"| {_FACTOR_LABEL[fname]} | — | — | (없음) |")
            continue
        lines.append(
            f"| {_FACTOR_LABEL[fname]} | {f.weight:.0%} |"
            f" {f.score:.1f} | {_clean_note(f.note)} |"
        )

    vrange_md = _valuation_range_md(s)
    if vrange_md:
        lines.extend(["", vrange_md])

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


def _footer(token_usage: TokenUsage | None = None) -> str:
    usage_line = ""
    if token_usage:
        from stock_compass.config import settings

        in_used = int(token_usage.get("input_tokens", 0))
        in_limit = settings.anthropic_daily_input_limit
        pct = (in_used / in_limit * 100) if in_limit > 0 else 0.0
        cost = float(token_usage.get("cost_usd", 0.0))
        usage_line = (
            f"\n\n> _Anthropic API 사용량: input {in_used:,} / {in_limit:,} tokens "
            f"({pct:.1f}%) · ${cost:.4f}_"
        )
    return f"---\n\n> **면책**: {DISCLAIMER}{usage_line}"


def _format_price(price: float | None, currency: str | None) -> str:
    if price is None:
        return "—"
    suffix = f" {currency}" if currency else ""
    return f"{price:,.2f}{suffix}"


def _clean_note(note: str) -> str:
    """마크다운 테이블 안전 — 파이프·줄바꿈 제거."""
    return note.replace("|", "/").replace("\n", " ").strip() or "—"


def _valuation_range_md(s: CompositeScore) -> str:
    """종목별 valuation range를 markdown 표로. fundamentals 호출 실패 시 빈 문자열."""
    try:
        from stock_compass.markets import get_adapter
        from stock_compass.output.valuation_range import (
            DISCLAIMER as VR_DISCLAIMER,
        )
        from stock_compass.output.valuation_range import compute_valuation_range

        adapter = get_adapter(s.ticker, s.market)
        fund = adapter.get_fundamentals(s.ticker)
        vr = compute_valuation_range(fund, current_price=s.price_at_score)
    except Exception:
        return ""

    if vr.is_empty():
        return ""

    lines = [
        "**Valuation Range** (현재 펀더멘털 x 시나리오)",
        "",
        "| 시나리오 | 방법 | 배수 | 적정가 | vs 현재 |",
        "|---|---|---:|---:|---:|",
    ]
    for p in vr.points:
        mul_str = (
            f"{p.multiple * 100:.1f}%" if p.method == "DIVIDEND" else f"{p.multiple:.1f}x"
        )
        price_str = f"{p.fair_price:,.2f} {vr.currency}"
        vs_str = (
            f"{'+' if p.vs_current_pct >= 0 else ''}{p.vs_current_pct:.1f}%"
            if p.vs_current_pct is not None
            else "—"
        )
        lines.append(
            f"| {p.scenario} | {p.method} | {mul_str} | {price_str} | {vs_str} |"
        )
    lines.append(f"\n> _{VR_DISCLAIMER}_")
    return "\n".join(lines)
