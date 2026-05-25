"""CraftPublisher — 실제 Craft Space API 흐름 (POST /documents + /blocks).

신규 발행 / 갱신(기존 delete + 신규 create) / 인증 실패 시나리오.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from stock_compass.db import migrate
from stock_compass.output._craft_client import CraftAuthError
from stock_compass.output.craft import CraftPublisher


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from stock_compass.config import settings

    p = tmp_path / "test.db"
    migrate(p)
    monkeypatch.setattr(settings, "db_path", p)
    monkeypatch.setattr(settings, "craft_daily_folder_id", "test_folder_xyz")
    return p


class _StubClient:
    """CraftClient 인터페이스 흉내내는 stub. 호출 기록 + 가짜 응답."""

    def __init__(
        self,
        *,
        new_id: str = "doc_001",
        new_url: str = "craftdocs://open?documentId=doc_001",
        delete_raises: Exception | None = None,
    ) -> None:
        self.new_id = new_id
        self.new_url = new_url
        self.delete_raises = delete_raises
        self.create_calls: list[dict[str, Any]] = []
        self.delete_calls: list[list[str]] = []
        self.block_calls: list[dict[str, Any]] = []

    def create_document(
        self, *, folder_id: str, title: str
    ) -> dict[str, Any]:
        self.create_calls.append({"folder_id": folder_id, "title": title})
        return {
            "id": self.new_id,
            "title": title,
            "clickableLink": self.new_url,
        }

    def delete_documents(self, document_ids: list[str]) -> list[str]:
        if self.delete_raises is not None:
            raise self.delete_raises
        self.delete_calls.append(list(document_ids))
        return list(document_ids)

    def append_markdown_blocks(
        self, *, document_id: str, markdown: str
    ) -> list[dict[str, Any]]:
        self.block_calls.append(
            {"document_id": document_id, "markdown": markdown}
        )
        return [{"id": "b1", "type": "text", "markdown": markdown}]


# ──────────────────────── 신규 발행 ────────────────────────


class TestFirstPublish:
    def test_creates_document_and_appends_blocks(self, db_path: Path) -> None:
        stub = _StubClient(new_id="doc_aaa", new_url="craftdocs://aaa")
        publisher = CraftPublisher(client=stub)  # type: ignore[arg-type]
        result = publisher.publish_daily_note(
            "# Hello\n\n본문 내용", on_date=date(2026, 5, 26)
        )

        # 호출 검증
        assert len(stub.create_calls) == 1
        assert stub.create_calls[0]["title"] == "stock-compass · 2026-05-26"
        assert stub.create_calls[0]["folder_id"] == "test_folder_xyz"
        assert stub.delete_calls == []  # 신규는 delete 없음
        assert len(stub.block_calls) == 1
        assert stub.block_calls[0]["document_id"] == "doc_aaa"
        assert stub.block_calls[0]["markdown"] == "# Hello\n\n본문 내용"

        # 반환값
        assert result.note_id == "doc_aaa"
        assert result.url == "craftdocs://aaa"
        assert result.is_update is False

        # DB 기록
        with sqlite3.connect(db_path) as c:
            row = c.execute(
                "SELECT note_kind, on_date, note_id, url FROM craft_publications"
            ).fetchone()
            assert row == ("daily", "2026-05-26", "doc_aaa", "craftdocs://aaa")


# ──────────────────────── 갱신 ────────────────────────


class TestUpdate:
    def test_second_publish_deletes_then_creates(self, db_path: Path) -> None:
        stub = _StubClient(new_id="doc_v1")
        publisher = CraftPublisher(client=stub)  # type: ignore[arg-type]
        publisher.publish_daily_note("v1", on_date=date(2026, 5, 26))

        # 두 번째: 기존 doc_v1 delete + 신규 생성
        stub.new_id = "doc_v2"
        stub.new_url = "craftdocs://v2"
        r2 = publisher.publish_daily_note("v2 본문", on_date=date(2026, 5, 26))

        assert stub.delete_calls == [["doc_v1"]]
        assert len(stub.create_calls) == 2  # 1차 + 2차
        assert r2.is_update is True
        assert r2.note_id == "doc_v2"
        assert r2.url == "craftdocs://v2"

        # DB는 새 ID로 갱신됨
        with sqlite3.connect(db_path) as c:
            row = c.execute(
                "SELECT note_id, url FROM craft_publications WHERE on_date = ?",
                ("2026-05-26",),
            ).fetchone()
            assert row == ("doc_v2", "craftdocs://v2")

    def test_delete_failure_does_not_block_create(self, db_path: Path) -> None:
        """이전 노트 delete 실패해도 신규 생성은 계속 진행 (orphan은 사용자가 수동 정리)."""
        from stock_compass.output._craft_client import CraftAPIError

        stub = _StubClient(new_id="doc_v1")
        publisher = CraftPublisher(client=stub)  # type: ignore[arg-type]
        publisher.publish_daily_note("v1", on_date=date(2026, 5, 26))

        stub.delete_raises = CraftAPIError("simulated 500")
        stub.new_id = "doc_v2"
        r2 = publisher.publish_daily_note("v2", on_date=date(2026, 5, 26))

        assert r2.is_update is True
        assert r2.note_id == "doc_v2"  # 새 ID로 정상 발행
        assert stub.delete_calls == []  # delete 호출은 raise로 막힘


# ──────────────────────── 노트 종류 ────────────────────────


class TestNoteKind:
    def test_different_kind_independent(self, db_path: Path) -> None:
        stub = _StubClient()
        publisher = CraftPublisher(client=stub)  # type: ignore[arg-type]
        publisher.publish_daily_note("daily", on_date=date(2026, 5, 26))
        publisher.publish_daily_note(
            "discover", on_date=date(2026, 5, 26), note_kind="discover:vg_kr"
        )
        # 둘 다 신규 (서로 다른 kind)
        assert len(stub.create_calls) == 2
        assert stub.delete_calls == []


# ──────────────────────── 설정 누락 ────────────────────────


class TestMissingConfig:
    def test_no_folder_id_raises(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stock_compass.config import settings

        monkeypatch.setattr(settings, "craft_daily_folder_id", None)
        publisher = CraftPublisher(client=_StubClient())  # type: ignore[arg-type]
        with pytest.raises(CraftAuthError, match="CRAFT_DAILY_FOLDER_ID"):
            publisher.publish_daily_note("x", on_date=date(2026, 5, 26))

    def test_no_token_raises_on_publish(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stock_compass.config import settings

        monkeypatch.setattr(settings, "craft_api_token", None)
        publisher = CraftPublisher()  # 토큰 없는 lazy 모드
        with pytest.raises(CraftAuthError, match="CRAFT_API_TOKEN"):
            publisher.publish_daily_note("x", on_date=date(2026, 5, 26))


# ──────────────────────── CraftClient HTTP 동작 ────────────────────────


class TestCraftClient:
    """httpx mock으로 인증·에러 코드 처리 검증."""

    def test_401_raises_auth_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from stock_compass.output._craft_client import CraftClient

        client = CraftClient(base_url="https://test.example/api/v1")

        def _fake(*args: Any, **kwargs: Any) -> Any:
            m = MagicMock()
            m.status_code = 401
            m.text = "Unauthorized"
            return m

        with monkeypatch.context() as m:
            m.setattr("httpx.Client.request", _fake)
            with pytest.raises(CraftAuthError, match="인증 실패"):
                client.create_document(folder_id="f", title="t")

    def test_429_raises_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from stock_compass.output._craft_client import (
            CraftClient,
            CraftRateLimitError,
        )

        client = CraftClient(base_url="https://test.example/api/v1")

        def _fake(*args: Any, **kwargs: Any) -> Any:
            m = MagicMock()
            m.status_code = 429
            m.headers = {"Retry-After": "60"}
            m.text = "Too many"
            return m

        with monkeypatch.context() as m:
            m.setattr("httpx.Client.request", _fake)
            with pytest.raises(CraftRateLimitError, match="60"):
                client.create_document(folder_id="f", title="t")

    def test_create_document_returns_first_item(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stock_compass.output._craft_client import CraftClient

        client = CraftClient(base_url="https://test.example/api/v1")

        def _fake(*args: Any, **kwargs: Any) -> Any:
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {
                "items": [
                    {"id": "doc_123", "title": "t", "clickableLink": "craftdocs://x"}
                ]
            }
            return m

        with monkeypatch.context() as m:
            m.setattr("httpx.Client.request", _fake)
            result = client.create_document(folder_id="f", title="t")
            assert result["id"] == "doc_123"
            assert result["clickableLink"] == "craftdocs://x"

    def test_append_markdown_blocks_empty_skips(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stock_compass.output._craft_client import CraftClient

        client = CraftClient(base_url="https://test.example/api/v1")
        called: list[Any] = []

        def _fake(*args: Any, **kwargs: Any) -> Any:
            called.append(kwargs)
            return MagicMock(status_code=200, json=lambda: {"items": []})

        with monkeypatch.context() as m:
            m.setattr("httpx.Client.request", _fake)
            result = client.append_markdown_blocks(document_id="d", markdown="   ")
            assert result == []
            assert called == []  # 빈 markdown은 API 호출 없음

    def test_delete_documents_empty_no_op(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stock_compass.output._craft_client import CraftClient

        client = CraftClient(base_url="https://test.example/api/v1")
        called: list[Any] = []

        def _fake(*args: Any, **kwargs: Any) -> Any:
            called.append(kwargs)
            return MagicMock(status_code=200, json=lambda: {"items": []})

        with monkeypatch.context() as m:
            m.setattr("httpx.Client.request", _fake)
            result = client.delete_documents([])
            assert result == []
            assert called == []
