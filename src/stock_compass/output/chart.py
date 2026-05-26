"""점수 추이 차트 PNG 생성 — matplotlib + 한글 폰트.

CraftPublisher가 일일 노트에 첨부하는 종목별 미니 차트.

디자인 원칙:
- 모바일 가독성 우선 (600x300 px)
- 임계선 70(관심권)/50(중립) 표시
- 한글 폰트 (macOS AppleGothic)
- 1~2일치 데이터면 차트 의미 X → None 반환

면책: 차트는 과거 기록만 — 예측 아님.
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stock_compass.db.repository import HistoryRow

_logger = logging.getLogger(__name__)

# 임계선 (CLAUDE.md 8 — 점수 등급)
_THRESH_INTEREST = 70
_THRESH_NEUTRAL = 50

# 최소 데이터 포인트 — 미만이면 차트 의미 없음 (단일 점은 line 못 그림)
_MIN_POINTS = 2

# 한글 폰트 후보 (macOS 표준 → 다른 환경 fallback)
_KO_FONTS = ("AppleGothic", "Apple SD Gothic Neo", "Noto Sans CJK KR", "DejaVu Sans")


def _configure_matplotlib() -> None:
    """matplotlib 한글 폰트 + 마이너스 깨짐 방지. 멱등."""
    import matplotlib

    matplotlib.use("Agg")  # headless 환경
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt

    available = {f.name for f in fm.fontManager.ttflist}
    for font in _KO_FONTS:
        if font in available:
            plt.rcParams["font.family"] = font
            break
    plt.rcParams["axes.unicode_minus"] = False


def render_score_history_chart(
    history: list[HistoryRow],
    *,
    ticker: str,
    name: str | None = None,
) -> bytes | None:
    """30일 점수 추이 → PNG bytes. 데이터 부족 시 None.

    Args:
        history: get_score_history 결과 (오래된 → 최신 순)
        ticker: 헤더 표시용
        name: 종목명 (있으면 헤더에 포함)

    Returns:
        PNG bytes 또는 None (데이터 부족 시)
    """
    if len(history) < _MIN_POINTS:
        return None

    _configure_matplotlib()
    import matplotlib.pyplot as plt
    from matplotlib.dates import DateFormatter

    dates = [_parse_date(h.date) for h in history]
    scores = [h.total_score for h in history]
    latest = scores[-1]
    color = _verdict_color(latest)

    fig = plt.figure(figsize=(6, 3), dpi=120)
    ax = fig.add_subplot(111)

    # 임계선 (배경)
    ax.axhspan(_THRESH_INTEREST, 100, alpha=0.08, color="green")
    ax.axhspan(_THRESH_NEUTRAL, _THRESH_INTEREST, alpha=0.05, color="orange")
    ax.axhspan(0, _THRESH_NEUTRAL, alpha=0.08, color="red")
    ax.axhline(_THRESH_INTEREST, color="green", linestyle=":", linewidth=0.8, alpha=0.5)
    ax.axhline(_THRESH_NEUTRAL, color="red", linestyle=":", linewidth=0.8, alpha=0.5)

    # 점수 라인
    ax.plot(dates, scores, marker="o", markersize=4, linewidth=1.8, color=color)

    # 헤더 — 종목명 + 현재 점수
    title_label = f"{name} ({ticker})" if name else ticker
    title = f"{title_label}  ·  현재 {latest:.1f}"
    ax.set_title(title, fontsize=11, fontweight="bold", loc="left")

    # 축 포맷
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 30, 50, 70, 100])
    ax.set_ylabel("점수", fontsize=9)
    ax.tick_params(axis="both", labelsize=8)
    if dates:
        ax.xaxis.set_major_formatter(DateFormatter("%m-%d"))  # type: ignore[no-untyped-call]
        fig.autofmt_xdate(rotation=30, ha="right")
    ax.grid(True, alpha=0.15)

    # 면책 footer
    ax.text(
        0.99,
        -0.18,
        "참고용 — 투자 자문 아님",
        transform=ax.transAxes,
        fontsize=7,
        color="gray",
        ha="right",
        va="top",
    )

    fig.tight_layout()
    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", bbox_inches="tight")
    finally:
        plt.close(fig)
    return buf.getvalue()


# ──────────────────────── helpers ────────────────────────


def _parse_date(s: str):  # type: ignore[no-untyped-def]
    """ISO date string → datetime.date (matplotlib에서 datetime axis로 처리)."""
    from datetime import date

    return date.fromisoformat(s)


def _verdict_color(score: float) -> str:
    if score >= _THRESH_INTEREST:
        return "#1f9d55"  # green
    if score >= _THRESH_NEUTRAL:
        return "#d97706"  # amber
    return "#dc2626"  # red
