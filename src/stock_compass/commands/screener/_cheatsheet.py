"""screen --list-presets / --list-fields 카탈로그 출력."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stock_compass.commands._app import console

if TYPE_CHECKING:
    from stock_compass.screener import PresetInfo


def print_preset_catalog(presets: list[PresetInfo]) -> None:
    from rich.table import Table

    if not presets:
        console.print(
            "[yellow]프리셋 없음 — screeners/presets/*.sql 확인.[/yellow]"
        )
        return
    table = Table(title=f"사용 가능한 프리셋 ({len(presets)}개)")
    table.add_column("이름", style="cyan")
    table.add_column("설명", overflow="fold")
    for p in presets:
        table.add_row(p.name, p.description)
    console.print(table)
    console.print("[dim]사용: stock-compass screen --preset <name>[/dim]")


def print_field_cheatsheet() -> None:
    from rich.table import Table

    sections: list[tuple[str, list[tuple[str, str]]]] = [
        (
            "Valuation",
            [
                ("per", "PER (배)"),
                ("pbr", "PBR (배)"),
                ("peg", "PEG (배)"),
                ("dividend_yield", "배당수익률 (0.03 = 3%)"),
                ("market_cap", "시가총액 (현지 통화)"),
                ("market_cap_krw", "KRW 환산 시가총액 (cross-market 비교)"),
                ("size_bucket", "규모 등급 (mega/large/mid/small/micro)"),
                ("valuation_score", "Valuation 팩터 점수 0~100"),
            ],
        ),
        (
            "Fundamentals",
            [
                ("revenue_growth_yoy", "매출 성장률 YoY (0.10 = 10%)"),
                ("operating_margin", "영업이익률"),
                ("roe", "ROE (0.15 = 15%)"),
                ("fundamentals_score", "Fundamentals 점수 0~100"),
            ],
        ),
        (
            "Quality",
            [
                ("debt_to_equity", "부채비율 D/E (yfinance % 스케일, 낮을수록 양호)"),
                ("current_ratio", "유동비율 (유동자산/유동부채, 높을수록 양호)"),
                ("roa", "총자산이익률 (0.08 = 8%)"),
                ("quality_score", "Quality 점수 0~100"),
            ],
        ),
        (
            "Technical",
            [
                ("rsi_14", "RSI(14)"),
                ("ma200_distance", "200MA 이격률 (+0.05 = 5% 위)"),
                ("volume_zscore", "20일 거래량 z-score"),
                ("technical_score", "Technical 점수 0~100"),
            ],
        ),
        (
            "Macro / Sentiment",
            [
                ("macro_score", "Macro 점수 0~100 (VIX·금리·KR USD/KRW)"),
                ("sentiment_score", "Sentiment 점수 0~100 (뉴스·공시 톤)"),
                ("sentiment_source", "api/manual_prompt/fallback/cache/placeholder"),
            ],
        ),
        (
            "메타",
            [
                ("code", "종목 코드"),
                ("name", "종목명"),
                ("market", "KR / US"),
                ("sector", "섹터"),
                ("universes", "지수 멤버십 (CSV 문자열)"),
                ("composite_score", "5팩터 가중평균 0~100"),
                ("verdict", "관심권/중립/주의"),
                ("price", "최신 종가"),
                ("as_of_date", "데이터 기준일"),
            ],
        ),
    ]

    for title, items in sections:
        table = Table(title=f"[{title}]")
        table.add_column("필드", style="cyan")
        table.add_column("설명", overflow="fold")
        for name, desc in items:
            table.add_row(name, desc)
        console.print(table)
