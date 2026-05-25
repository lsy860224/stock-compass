"""CraftPublisher — 신규 발행 + 갱신 + 인증 실패 + 중복 추적."""

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
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {
            "id": "note_001",
            "url": "https://craft.do/notes/001",
            "folder_id": "test_folder_xyz",
        }
        self.post_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

    def post_note(self, folder_id: str, **kwargs: Any) -> dict[str, Any]:
        self.post_calls.append({"folder_id": folder_id, **kwargs})
        return self.response

    def update_note(self, note_id: str, **kwargs: Any) -> dict[str, Any]:
        self.update_calls.append({"note_id": note_id, **kwargs})
        return self.response


class TestPublishDailyNote:
    def test_first_publish_inserts_record(self, db_path: Path) -> None:
        stub = _StubClient()
        publisher = CraftPublisher(client=stub)  # type: ignore[arg-type]
        result = publisher.publish_daily_note(
            "# Hello\n\n내용", on_date=date(2026, 5, 25)
        )

        assert result.note_id == "note_001"
        assert result.is_update is False
        assert len(stub.post_calls) == 1
        assert stub.post_calls[0]["title"] == "stock-compass · 2026-05-25"
        # DB 기록
        with sqlite3.connect(db_path) as c:
            row = c.execute(
                "SELECT note_kind, on_date, note_id FROM craft_publications"
            ).fetchone()
            assert row == ("daily", "2026-05-25", "note_001")

    def test_second_publish_calls_update(self, db_path: Path) -> None:
        stub = _StubClient()
        publisher = CraftPublisher(client=stub)  # type: ignore[arg-type]
        publisher.publish_daily_note("v1", on_date=date(2026, 5, 25))
        r2 = publisher.publish_daily_note("v2", on_date=date(2026, 5, 25))

        assert r2.is_update is True
        assert len(stub.update_calls) == 1
        assert stub.update_calls[0]["note_id"] == "note_001"

    def test_different_kind_independent(self, db_path: Path) -> None:
        # daily + discover:value_growth_kr는 각각 한 번씩 publish 가능
        stub = _StubClient()
        publisher = CraftPublisher(client=stub)  # type: ignore[arg-type]
        publisher.publish_daily_note("a", on_date=date(2026, 5, 25))
        publisher.publish_daily_note(
            "b", on_date=date(2026, 5, 25), note_kind="discover:vg_kr"
        )
        assert len(stub.post_calls) == 2

    def test_no_folder_id_raises(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stock_compass.config import settings

        monkeypatch.setattr(settings, "craft_daily_folder_id", None)
        publisher = CraftPublisher(client=_StubClient())  # type: ignore[arg-type]
        with pytest.raises(CraftAuthError, match="CRAFT_DAILY_FOLDER_ID"):
            publisher.publish_daily_note("x", on_date=date(2026, 5, 25))


class TestLazyClient:
    def test_no_token_raises_on_publish(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stock_compass.config import settings

        monkeypatch.setattr(settings, "craft_api_token", None)
        publisher = CraftPublisher()  # 토큰 없는 lazy 모드
        with pytest.raises(CraftAuthError, match="CRAFT_API_TOKEN"):
            publisher.publish_daily_note("x", on_date=date(2026, 5, 25))


class TestIdempotencyKey:
    def test_post_includes_idempotency_key(self, db_path: Path) -> None:
        stub = _StubClient()
        publisher = CraftPublisher(client=stub)  # type: ignore[arg-type]
        publisher.publish_daily_note(
            "x", on_date=date(2026, 5, 25), note_kind="daily"
        )
        assert (
            stub.post_calls[0]["idempotency_key"] == "sc-daily-2026-05-25"
        )


class TestCraftClient:
    def test_401_raises_auth_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        from stock_compass.output._craft_client import CraftClient

        client = CraftClient(token="bad_token", base_url="https://test.example")

        def _fake_request(*args: Any, **kwargs: Any) -> Any:
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = "Unauthorized"
            return mock_response

        with monkeypatch.context() as m:
            m.setattr("httpx.Client.request", _fake_request)
            with pytest.raises(CraftAuthError, match="인증 실패"):
                client.post_note(
                    "folder_a", title="t", content_markdown="x"
                )
        _ = httpx  # silence unused import
