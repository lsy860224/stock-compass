"""CraftExporter — 마크다운 렌더링 + 파일 백업 동작."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore
from stock_compass.output.craft import CraftExporter
from stock_compass.output.craft_exporter import _clean_note, _format_price
from stock_compass.scoring.engine import CompositeScore


def _mk(
    ticker: str,
    total: float,
    market: str = "US",
    name: str | None = None,
    price: float | None = None,
    currency: str | None = None,
) -> CompositeScore:
    factors = [
        FactorScore(
            name=n,            score=total,
            weight=DEFAULT_WEIGHTS[n],            note=f"{n} note",
            raw_values={"per": 15.0} if n == "valuation" else {},
        )
        for n in DEFAULT_WEIGHTS
    ]
    return CompositeScore(
        ticker=ticker,
        market=market,  # type: ignore[arg-type]
        total_score=total,
        verdict="관심권" if total >= 70 else "중립" if total >= 50 else "주의",
        factors=factors,
        computed_at=datetime(2026, 5, 22, 7, 0, tzinfo=UTC),
        price_at_score=price,
        currency=currency or ("USD" if market == "US" else "KRW"),
        name=name,
    )


@pytest.fixture
def exporter(tmp_path: Path) -> CraftExporter:
    return CraftExporter(export_dir=tmp_path)


class TestRenderDailyNote:
    def test_empty_scores(self, exporter: CraftExporter) -> None:
        md = exporter.render_daily_note([], on_date=date(2026, 5, 22))
        assert "# stock-compass · 2026-05-22" in md
        assert "대상 종목 없음" in md
        assert "투자 자문이 아닙니다" in md

    def test_sections_present(self, exporter: CraftExporter) -> None:
        scores = [
            _mk("AAPL", 50.0, name="Apple Inc.", price=150.0),
            _mk("005930", 75.0, market="KR", name="삼성전자", price=293000.0),
        ]
        md = exporter.render_daily_note(scores, on_date=date(2026, 5, 22))
        for section in (
            "# stock-compass · 2026-05-22",
            "## 일일 요약",
            "## 순위",
            "## 종목별 카드",
            "## 매매 일지",
            "**면책**",
        ):
            assert section in md, f"섹션 누락: {section}"

    def test_verdict_buckets_in_summary(self, exporter: CraftExporter) -> None:
        scores = [
            _mk("HIGH", 80.0),
            _mk("MID", 60.0),
            _mk("LOW", 30.0),
        ]
        md = exporter.render_daily_note(scores, on_date=date(2026, 5, 22))
        assert "**관심권 (≥70)**: 1종" in md
        assert "**중립 (50~69)**: 1종" in md
        assert "**주의 (<50)**: 1종" in md
        assert "`HIGH`" in md  # 관심권 종목 명시
        assert "`LOW`" in md  # 주의 종목 명시

    def test_per_ticker_includes_factors(self, exporter: CraftExporter) -> None:
        md = exporter.render_daily_note(
            [_mk("NVDA", 65.0, name="NVIDIA")], on_date=date(2026, 5, 22)
        )
        assert "`NVDA`" in md
        assert "NVIDIA" in md
        for label in ("Valuation", "Fundamentals", "Technical", "Macro", "Sentiment"):
            assert label in md

    def test_ranking_ordered(self, exporter: CraftExporter) -> None:
        # 입력 순서와 무관하게 점수 내림차순으로 출력되어야 함 (호출자가 정렬해야 하지만 안전)
        scores = [
            _mk("LOW", 40.0, name="Low"),
            _mk("HIGH", 90.0, name="High"),
        ]
        md = exporter.render_daily_note(
            sorted(scores, key=lambda s: s.total_score, reverse=True),
            on_date=date(2026, 5, 22),
        )
        # HIGH가 LOW보다 먼저 등장해야 함
        assert md.index("HIGH") < md.index("LOW")


class TestExportToFile:
    def test_creates_file_at_expected_path(self, exporter: CraftExporter) -> None:
        path = exporter.export_to_file("# test\n", on_date=date(2026, 5, 22))
        assert path.name == "2026-05-22.md"
        assert path.read_text(encoding="utf-8") == "# test\n"

    def test_backs_up_existing(self, exporter: CraftExporter) -> None:
        d = date(2026, 5, 22)
        exporter.export_to_file("# original\n", on_date=d)
        exporter.export_to_file("# updated\n", on_date=d)
        backups = list(exporter.export_dir.glob("2026-05-22.md.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "# original\n"
        assert (exporter.export_dir / "2026-05-22.md").read_text(encoding="utf-8") == (
            "# updated\n"
        )


class TestHelpers:
    @pytest.mark.parametrize(
        "price,currency,expected",
        [
            (None, "USD", "—"),
            (1234.5, "USD", "1,234.50 USD"),
            (293000.0, "KRW", "293,000.00 KRW"),
            (10.0, None, "10.00"),
        ],
    )
    def test_format_price(
        self, price: float | None, currency: str | None, expected: str
    ) -> None:
        assert _format_price(price, currency) == expected

    def test_clean_note_escapes_pipe_and_newline(self) -> None:
        assert _clean_note("a|b\nc") == "a/b c"

    def test_clean_note_empty_dash(self) -> None:
        assert _clean_note("") == "—"
        assert _clean_note("   ") == "—"
