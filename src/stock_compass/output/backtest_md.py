"""백테스트 결과 → Craft-friendly Markdown 렌더.

`commands/screener.backtest` 가 --publish-craft 시 호출. 통계 표 +
라운드별 요약 + 면책 패널. CLAUDE.md 1) 절대 원칙 (매수 권유 X) 준수.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stock_compass.screener.backtest import BacktestResult


def render_backtest_note(result: BacktestResult) -> str:
    """BacktestResult → Markdown 본문. Craft 노트 publish 용도."""
    from stock_compass.utils.dates import now_kst

    title = result.preset_name or "inline"
    when = now_kst().strftime("%Y-%m-%d %H:%M KST")

    lines: list[str] = [
        f"# 백테스트 결과 · {title} · {when}",
        "",
        f"> **기간**: {result.start.isoformat()} ~ {result.end.isoformat()}  ·  "
        f"**rebalance**: {result.rebalance}  ·  "
        f"**forward**: {', '.join(result.forward_periods)}",
        ">",
        "> ⚠️ **매수 권유 아님.** 거래비용·세금·슬리피지·배당 미반영. "
        "survivorship bias: 상장폐지 종목 제외 → 실제 시점 선택 가능 종목 누락 가능.",
        "",
    ]

    if not result.rounds:
        lines.append("## 결과")
        lines.append("\n라운드 0개 — 데이터 또는 기간 부족.")
        return "\n".join(lines)

    lines.append("## 전체 통계")
    lines.append("")
    lines.append("| Forward | 평균 | 중앙값 | 적중률 | 최저 |")
    lines.append("|---|---:|---:|---:|---:|")
    for p in result.forward_periods:
        avg = result.stats.avg_return.get(p)
        med = result.stats.median_return.get(p)
        hit = result.stats.hit_rate.get(p)
        worst = result.stats.worst_return.get(p)
        lines.append(
            "| {p} | {avg} | {med} | {hit} | {worst} |".format(
                p=p,
                avg=_fmt_pct(avg),
                med=_fmt_pct(med),
                hit=f"{hit * 100:.1f}%" if hit is not None else "—",
                worst=_fmt_pct(worst),
            )
        )
    lines.append("")
    lines.append(
        f"_총 선정 {result.stats.total_picks}건 x {len(result.forward_periods)} 기간 "
        f"({result.stats.rounds_count} 라운드)_"
    )

    # 라운드별 요약 (라운드 많으면 상위 10개 + 하위 10개)
    rounds = result.rounds
    lines.append("")
    lines.append("## 라운드별 요약")
    lines.append("")
    headers = ["as_of", "종목"] + [f"평균 {p}" for p in result.forward_periods]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for r in rounds:
        cells = [r.as_of.isoformat(), str(len(r.selected))]
        for p in result.forward_periods:
            vals = [
                v
                for tr in r.forward_returns.values()
                if (v := tr.get(p)) is not None
            ]
            cells.append(
                _fmt_pct(sum(vals) / len(vals)) if vals else "—"
            )
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"_{result.disclaimer}_")
    return "\n".join(lines)


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v * 100:.1f}%"
