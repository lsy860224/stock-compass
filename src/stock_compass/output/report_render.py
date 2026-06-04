"""자동 보고용 마크다운 렌더러 — 배치/재채점/알림 요약.

일일 종합 노트는 craft_exporter.render_daily_note 가 담당. 여기서는 그 외
자동화 task(장 마감 배치·유니버스 재채점·알림 발화)의 결과 요약을 생성한다.
모든 노트 하단에 면책(DISCLAIMER) 자동 삽입.
"""

from __future__ import annotations

from datetime import date as date_cls

from stock_compass.markets.base import Market
from stock_compass.scoring.engine import DISCLAIMER, CompositeScore

_MARKET_FLAG = {"US": "🇺🇸", "KR": "🇰🇷"}
_VERDICT_ORDER = ("관심권", "중립", "주의")


def render_batch_note(
    scores: list[CompositeScore], market: Market, on_date: date_cls
) -> str:
    """장 마감 배치(워치리스트 시장별) 결과 — 점수 순위 요약."""
    flag = _MARKET_FLAG.get(market, "")
    lines = [
        f"# {flag} {market} 장 마감 배치 · {on_date.isoformat()}",
        "",
        f"워치리스트 {market} {len(scores)}종목 점수 갱신.",
        "",
        _verdict_line(scores),
        "",
        "| 종목 | 점수 | 등급 | V | F | Q | T | M | S |",
        "| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in sorted(scores, key=lambda x: x.total_score, reverse=True):
        lines.append(
            f"| {_label(s)} | {s.total_score:.1f} | {s.verdict} "
            f"| {_f(s, 'valuation')} | {_f(s, 'fundamentals')} | {_f(s, 'quality')} "
            f"| {_f(s, 'technical')} | {_f(s, 'macro')} | {_f(s, 'sentiment')} |"
        )
    lines += ["", _footer()]
    return "\n".join(lines)


def render_rescore_summary(
    scores: list[CompositeScore], on_date: date_cls, *, top_n: int = 20
) -> str:
    """유니버스 주간 재채점 요약 — 채점 규모·등급 분포·관심권 상위."""
    kr = sum(1 for s in scores if s.market == "KR")
    us = len(scores) - kr
    watch = sorted(scores, key=lambda x: x.total_score, reverse=True)
    interest = [s for s in watch if s.verdict == "관심권"][:top_n]

    lines = [
        f"# 🔄 유니버스 주간 재채점 · {on_date.isoformat()}",
        "",
        f"전 유니버스 **{len(scores)}종목** 재채점 (KR {kr} · US {us}).",
        "",
        _verdict_line(scores),
        "",
        f"## 관심권(≥70) 상위 {min(top_n, len(interest))}",
        "",
    ]
    if interest:
        lines += [
            "| 종목 | 시장 | 점수 | 섹터 |",
            "| --- | :---: | ---: | --- |",
        ]
        lines += [
            f"| {_label(s)} | {s.market} | {s.total_score:.1f} | {s.sector or '—'} |"
            for s in interest
        ]
    else:
        lines.append("관심권(≥70) 종목 없음.")
    lines += [
        "",
        "> 스크리너 `v_latest_scores`가 이 점수로 갱신됨 — 08:00 weekly-discover 입력.",
        "",
        _footer(),
    ]
    return "\n".join(lines)


def render_alerts_note(fired: list, on_date: date_cls) -> str:  # type: ignore[type-arg]
    """발화된 알림 요약 — 임계치·급변 트리거 이력."""
    delivered = [f for f in fired if getattr(f, "delivered", False)]
    lines = [
        f"# 🔔 알림 발화 · {on_date.isoformat()}",
        "",
        f"발화 {len(delivered)}건 (중복 제외).",
        "",
        "| 종목 | 트리거 | 이전 | → | 현재 |",
        "| --- | --- | ---: | :---: | ---: |",
    ]
    for f in delivered:
        a = f.alert
        before = f"{a.score_before:.1f}" if a.score_before is not None else "—"
        lines.append(
            f"| {a.ticker} ({a.market}) | {a.trigger_type} | {before} | → "
            f"| {a.score_after:.1f} |"
        )
    lines += ["", _footer()]
    return "\n".join(lines)


def _verdict_line(scores: list[CompositeScore]) -> str:
    counts: dict[str, int] = {}
    for s in scores:
        counts[s.verdict] = counts.get(s.verdict, 0) + 1
    parts = [f"{v} {counts.get(v, 0)}" for v in _VERDICT_ORDER]
    return "**등급 분포:** " + " · ".join(parts)


def _label(s: CompositeScore) -> str:
    name = (s.name or "").strip()
    short = name if len(name) <= 18 else name[:17] + "…"
    return f"{s.ticker} {short}".strip()


def _f(s: CompositeScore, factor: str) -> str:
    fs = s.factor(factor)  # type: ignore[arg-type]
    return f"{fs.score:.0f}" if fs is not None else "—"


def _footer() -> str:
    return f"---\n\n> {DISCLAIMER}"
