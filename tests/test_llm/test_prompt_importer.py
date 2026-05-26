"""PromptImporter — JSON 변종 파싱 + 30단어 경계 + DB 저장."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stock_compass.db import (
    get_recent_news_summaries,
    migrate,
    upsert_ticker,
)
from stock_compass.llm.prompt_importer import (
    PromptImportError,
    _max_words_per_sentence,
    import_response,
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "test.db"
    migrate(db)
    c = sqlite3.connect(db, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    # 워치리스트 종목 미리 등록
    upsert_ticker(
        c,
        code="005930",
        market="KR",
        name="삼성전자",
        sector="반도체",
        currency="KRW",
        yfinance_symbol="005930.KS",
    )
    return c


def _write(tmp_path: Path, content: str, name: str = "response.txt") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ──────────────────────── JSON 파싱 변종 ────────────────────────


class TestJsonExtraction:
    def test_code_fence_json(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        body = """
여기는 응답 머리말입니다.

```json
{
  "batch_id": "B1",
  "results": [
    {"ticker": "005930", "summary": "짧은 요약.", "tone_score": 2.0, "keywords": ["a"]}
  ]
}
```
"""
        path = _write(tmp_path, body)
        r = import_response(conn, path, batch_id="B1", archive=False)
        assert r.saved == 1

    def test_bare_json_no_fence(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        body = (
            '{"batch_id":"B2","results":[{"ticker":"005930",'
            '"summary":"x","tone_score":0,"keywords":[]}]}'
        )
        r = import_response(conn, _write(tmp_path, body), batch_id="B2", archive=False)
        assert r.saved == 1

    def test_no_json_block_raises(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        with pytest.raises(PromptImportError, match="JSON 코드블록"):
            import_response(
                conn,
                _write(tmp_path, "no json here"),
                batch_id="B3",
                archive=False,
            )

    def test_batch_id_mismatch_raises(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        body = '{"batch_id":"X","results":[]}'
        with pytest.raises(PromptImportError, match="batch_id 불일치"):
            import_response(
                conn, _write(tmp_path, body), batch_id="Y", archive=False
            )

    def test_invalid_schema_raises(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        body = '{"batch_id":"B","results":[{"ticker":"005930"}]}'  # 필수 필드 누락
        with pytest.raises(PromptImportError, match="검증 실패"):
            import_response(conn, _write(tmp_path, body), archive=False)

    def test_tone_out_of_range_raises(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        body = (
            '{"batch_id":"B","results":['
            '{"ticker":"005930","summary":"x","tone_score":99,"keywords":[]}]}'
        )
        with pytest.raises(PromptImportError, match="검증 실패"):
            import_response(conn, _write(tmp_path, body), archive=False)


# ──────────────────────── 인용 단어수 경계 ────────────────────────


class TestQuoteWordBoundary:
    @pytest.mark.parametrize(
        "word_count,expected_max",
        [(29, 29), (30, 30), (49, 49), (50, 50)],
    )
    def test_max_words_per_sentence(self, word_count: int, expected_max: int) -> None:
        text = " ".join(["w"] * word_count) + "."
        assert _max_words_per_sentence(text) == expected_max

    def test_29_words_no_warning(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        summary = " ".join(["w"] * 29) + "."
        body = (
            f'{{"batch_id":"B","results":[{{"ticker":"005930",'
            f'"summary":"{summary}","tone_score":0,"keywords":[]}}]}}'
        )
        r = import_response(conn, _write(tmp_path, body), archive=False)
        assert r.saved == 1
        assert r.warnings == []
        assert r.blocked == []

    def test_30_words_warns_but_saves(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        summary = " ".join(["w"] * 30) + "."
        body = (
            f'{{"batch_id":"B","results":[{{"ticker":"005930",'
            f'"summary":"{summary}","tone_score":0,"keywords":[]}}]}}'
        )
        r = import_response(conn, _write(tmp_path, body), archive=False)
        assert r.saved == 1
        assert any("30단어" in w or "단어" in w for w in r.warnings)

    def test_50_words_blocks(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        summary = " ".join(["w"] * 50) + "."
        body = (
            f'{{"batch_id":"B","results":[{{"ticker":"005930",'
            f'"summary":"{summary}","tone_score":0,"keywords":[]}}]}}'
        )
        r = import_response(conn, _write(tmp_path, body), archive=False)
        assert r.saved == 0
        assert any("차단" in b for b in r.blocked)


# ──────────────────────── DB 저장 ────────────────────────


class TestDbPersistence:
    def test_saves_to_news_summaries_with_source(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        body = (
            '{"batch_id":"BX","results":[{"ticker":"005930",'
            '"summary":"OK","tone_score":3,"keywords":["a","b"]}]}'
        )
        import_response(conn, _write(tmp_path, body), archive=False)

        rows = get_recent_news_summaries(conn, ticker_id=1, days=1)
        assert len(rows) == 1
        assert rows[0].source == "manual_prompt"
        assert rows[0].tone_score == 3.0
        assert rows[0].batch_id == "BX"
        assert rows[0].keywords == ["a", "b"]

    def test_concerns_persisted(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        # 사용자가 제공한 concerns 가 DB에 저장되어야 함 (이전엔 폐기됐던 필드)
        body = (
            '{"batch_id":"BC","results":[{"ticker":"005930",'
            '"summary":"x","tone_score":-1,"keywords":["반도체"],'
            '"concerns":["환율 불확실성","HBM 경쟁 심화"]}]}'
        )
        import_response(conn, _write(tmp_path, body), archive=False)
        rows = get_recent_news_summaries(conn, ticker_id=1, days=1)
        assert rows[0].concerns == ["환율 불확실성", "HBM 경쟁 심화"]

    def test_concerns_default_empty(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        body = (
            '{"batch_id":"BD","results":[{"ticker":"005930",'
            '"summary":"x","tone_score":0,"keywords":[]}]}'
        )
        import_response(conn, _write(tmp_path, body), archive=False)
        rows = get_recent_news_summaries(conn, ticker_id=1, days=1)
        assert rows[0].concerns == []

    def test_unknown_ticker_warns_skip(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        body = (
            '{"batch_id":"BZ","results":[{"ticker":"NOPE",'
            '"summary":"x","tone_score":0,"keywords":[]}]}'
        )
        r = import_response(conn, _write(tmp_path, body), archive=False)
        # 알파벳이지만 DB에 없음 → 미등록 경고
        assert r.saved == 0
        assert any("미등록" in w for w in r.warnings)

    def test_archive_creates_copy(
        self, tmp_path: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # archive 기본 경로를 임시 디렉토리 안으로 변경
        from stock_compass.config import settings

        archive_root = tmp_path / "prompts"
        archive_root.mkdir()
        monkeypatch.setattr(settings, "prompt_dir", archive_root)

        body = (
            '{"batch_id":"BA","results":[{"ticker":"005930",'
            '"summary":"x","tone_score":0,"keywords":[]}]}'
        )
        r = import_response(conn, _write(tmp_path, body), archive=True)
        assert r.archived_to is not None
        assert r.archived_to.exists()
        assert r.archived_to.parent.name == "archive"
