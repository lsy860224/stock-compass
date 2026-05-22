"""Sentiment Prompt 파일 생성 (수동 경로 — Claude.ai 복붙).

스크리너·심층 분석 대상 종목 N개를 한 마크다운 파일로 묶어 사용자에게 전달.
사용자는 Claude.ai에 복붙 → JSON 응답을 `sentiment import`로 DB에 회수.

batch_id: `YYYYMMDD-HHMMSS-<tag>-<shorthash>` — 응답 파일과 1:1 매칭.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from stock_compass.config import settings
from stock_compass.markets import get_adapter
from stock_compass.markets.base import Disclosure, Market, MarketAdapter, News
from stock_compass.utils.dates import now_kst
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GeneratedPrompt:
    batch_id: str
    path: Path
    ticker_count: int
    file_count: int  # 한 batch가 여러 파일로 분할될 수 있음


class PromptGenerator:
    """뉴스/공시 → Claude.ai용 마크다운 + JSON 응답 스키마 정의."""

    def __init__(
        self,
        *,
        prompt_dir: Path | None = None,
        tickers_per_file: int = 5,
    ) -> None:
        self.prompt_dir = prompt_dir or settings.prompt_dir
        self.tickers_per_file = tickers_per_file

    def generate_sentiment_prompt(
        self,
        tickers: list[tuple[str, Market]],
        *,
        days: int = 7,
        tag: str = "sentiment",
    ) -> GeneratedPrompt:
        """tickers (code, market) → 마크다운 파일(들). 첫 파일 경로를 반환한다."""
        if not tickers:
            raise ValueError("tickers 비어 있음 — 최소 1종목 필요")

        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        batch_id = _build_batch_id(tag)
        paths: list[Path] = []

        chunks = [
            tickers[i : i + self.tickers_per_file]
            for i in range(0, len(tickers), self.tickers_per_file)
        ]
        for i, chunk in enumerate(chunks, start=1):
            suffix = f"-part{i}of{len(chunks)}" if len(chunks) > 1 else ""
            path = self.prompt_dir / f"{batch_id}{suffix}.md"
            content = self._render(batch_id, chunk, days=days, part=i, total=len(chunks))
            path.write_text(content, encoding="utf-8")
            paths.append(path)
            _logger.info(
                "Prompt 파일 생성: %s (%d종목)", path.name, len(chunk)
            )

        return GeneratedPrompt(
            batch_id=batch_id,
            path=paths[0],
            ticker_count=len(tickers),
            file_count=len(paths),
        )

    # ─── 내부 렌더링 ───

    def _render(
        self,
        batch_id: str,
        tickers: list[tuple[str, Market]],
        *,
        days: int,
        part: int,
        total: int,
    ) -> str:
        sections = [
            _header(batch_id, len(tickers), days, part, total),
            _instructions(batch_id),
            _ticker_sections(tickers, days=days),
            _footer_reminder(),
        ]
        return "\n\n".join(s for s in sections if s).rstrip() + "\n"


# ──────────────────────── 섹션 빌더 ────────────────────────


def _header(batch_id: str, n: int, days: int, part: int, total: int) -> str:
    part_note = f" · Part {part}/{total}" if total > 1 else ""
    when = now_kst().strftime("%Y-%m-%d %H:%M KST")
    return (
        f"# stock-compass — Sentiment 분석 요청{part_note}\n\n"
        f"> Batch ID: `{batch_id}`\n"
        f"> 종목 수: {n}  ·  기간: 최근 {days}일\n"
        f"> 생성: {when}"
    )


def _instructions(batch_id: str) -> str:
    return f"""## 작업 지시

다음 종목별로 최근 뉴스·공시를 분석하여 JSON 형식으로 응답해줘.

**원칙**:
1. 객관적 사실만. 추측·예측 금지.
2. 원문 30단어 이상 그대로 인용 금지 (저작권).
3. 자체 표현으로 3줄 요약 (한국어).
4. 톤 점수 `-10`(매우 부정) ~ `+10`(매우 긍정).
5. 핵심 키워드 5개 추출.

**응답 형식** (반드시 JSON 코드블록 1개, 다른 텍스트 X):

```json
{{
  "batch_id": "{batch_id}",
  "results": [
    {{
      "ticker": "<종목코드>",
      "summary": "3줄 요약",
      "tone_score": 0.0,
      "keywords": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5"],
      "concerns": ["주의사항 있으면 / 없으면 빈 배열"]
    }}
  ]
}}
```"""


def _ticker_sections(
    tickers: list[tuple[str, Market]], *, days: int
) -> str:
    blocks: list[str] = ["## 종목별 데이터"]
    for i, (code, market) in enumerate(tickers, start=1):
        adapter = get_adapter(code, market)
        blocks.append(_ticker_block(i, code, market, adapter, days=days))
    return "\n\n".join(blocks)


def _ticker_block(
    index: int,
    code: str,
    market: Market,
    adapter: MarketAdapter,
    *,
    days: int,
) -> str:
    name = _safe_name(adapter, code)
    name_str = f" — {name}" if name else ""
    news = _safe_news(adapter, code, days=days)
    disclosures = (
        _safe_disclosures(adapter, code, days=days) if market == "KR" else []
    )

    lines = [
        f"### {index}. {code}{name_str} [{market}]",
        "",
        f"#### 최근 {days}일 뉴스 ({len(news)}건)",
    ]
    if news:
        for j, n in enumerate(news[:5], start=1):  # 최대 5건
            published = n.published_at.strftime("%Y-%m-%d")
            src = f" / 출처: {n.source_name}" if n.source_name else ""
            lines.extend(
                [
                    "",
                    f"##### {j}. [{published}] {n.title}",
                    f"URL: {n.url}{src}",
                    f"요약 원문: {(n.summary or '[본문 미수집 — 제목만]')[:500]}",
                ]
            )
    else:
        lines.append("\n_뉴스 없음 — `summary`에 \"뉴스 부족\"으로 응답_")

    if market == "KR":
        lines.extend(["", f"#### 최근 {days * 2}일 공시 ({len(disclosures)}건)"])
        if disclosures:
            for j, d in enumerate(disclosures[:3], start=1):
                published = d.published_at.strftime("%Y-%m-%d")
                lines.extend(
                    [
                        "",
                        f"##### {j}. [{published}] {d.title}",
                        f"공시번호: {d.rcept_no}",
                        f"DART URL: {d.url or '—'}",
                    ]
                )
        else:
            lines.append("\n_공시 없음._")

    return "\n".join(lines)


def _footer_reminder() -> str:
    return """## 응답 가이드 (한 번 더)

- **JSON 코드블록 1개만** 응답 (```json ... ```)
- `batch_id` 정확히 위와 동일하게
- `results` 배열 순서는 위 종목 순서 유지
- 누락 종목 있으면 `"summary": "데이터 부족"`, `"tone_score": 0`, `"keywords": []`
- 30단어 이상 원문 그대로 인용 금지 (자체 표현 사용)

---

> **면책**: AI 생성 요약. 원문 확인 필수. 투자 자문 아님."""


# ──────────────────────── 안전 호출 헬퍼 ────────────────────────


def _safe_name(adapter: MarketAdapter, code: str) -> str | None:
    try:
        return adapter.get_fundamentals(code).name
    except Exception as e:
        _logger.debug("이름 조회 실패: %s — %s", code, e)
        return None


def _safe_news(adapter: MarketAdapter, code: str, *, days: int) -> list[News]:
    try:
        return adapter.get_news(code, days=days)
    except Exception as e:
        _logger.warning("뉴스 조회 실패: %s — %s", code, e)
        return []


def _safe_disclosures(adapter: MarketAdapter, code: str, *, days: int) -> list[Disclosure]:
    try:
        return adapter.get_disclosures(code, days=days * 2)
    except Exception as e:
        _logger.warning("공시 조회 실패: %s — %s", code, e)
        return []


def _build_batch_id(tag: str) -> str:
    """`YYYYMMDD-HHMMSS-<tag>-<6hex>` — 시간 + 해시로 충돌 회피."""
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    h = hashlib.sha256(f"{ts}-{tag}".encode()).hexdigest()[:6]
    return f"{ts}-{tag}-{h}"
