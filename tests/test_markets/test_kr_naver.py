"""Naver News API 통합 — KR sentiment 부활 회귀 (AT2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest

from stock_compass.markets.kr import _fetch_news_naver


@pytest.fixture
def naver_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    from stock_compass.config import settings

    monkeypatch.setattr(settings, "naver_client_id", "test_id")
    monkeypatch.setattr(settings, "naver_client_secret", SecretStr("test_secret"))


def _mk_response(items: list[dict[str, str]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"items": items, "total": len(items)},
        request=httpx.Request("GET", "https://openapi.naver.com/v1/search/news.json"),
    )


class TestSettingsGate:
    def test_no_credentials_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stock_compass.config import settings

        monkeypatch.setattr(settings, "naver_client_id", None)
        monkeypatch.setattr(settings, "naver_client_secret", None)
        assert _fetch_news_naver("005930", days=7) == []

    def test_no_ticker_name_returns_empty(
        self, naver_configured: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # _kr_name None 반환 → Naver 경로 생략
        monkeypatch.setattr("stock_compass.markets.kr._kr_name", lambda c: None)
        assert _fetch_news_naver("005930", days=7) == []


class TestParsing:
    def test_parses_response(
        self, naver_configured: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "stock_compass.markets.kr._kr_name", lambda c: "삼성전자"
        )
        today = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S %z").strip() or (
            datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0900")
        )
        mock_items = [
            {
                "title": "<b>삼성전자</b>, 신제품 출시",
                "originallink": "https://example.com/article1",
                "link": "https://news.naver.com/article1",
                "description": "삼성전자가 신제품을 출시했다고 발표.",
                "pubDate": today,
            }
        ]
        with patch(
            "httpx.get", return_value=_mk_response(mock_items)
        ):
            out = _fetch_news_naver("005930", days=7)
        assert len(out) == 1
        # HTML 태그 제거 + entity 디코드
        assert out[0].title == "삼성전자, 신제품 출시"
        assert out[0].url == "https://example.com/article1"
        assert out[0].source_name == "naver"

    def test_filters_old_articles(
        self, naver_configured: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "stock_compass.markets.kr._kr_name", lambda c: "삼성전자"
        )
        long_ago = (datetime.now(UTC) - timedelta(days=30)).strftime(
            "%a, %d %b %Y %H:%M:%S %z"
        )
        with patch(
            "httpx.get",
            return_value=_mk_response(
                [
                    {
                        "title": "old article",
                        "originallink": "https://example.com/old",
                        "link": "x",
                        "description": "",
                        "pubDate": long_ago,
                    }
                ]
            ),
        ):
            out = _fetch_news_naver("005930", days=7)
        assert out == []

    def test_handles_http_error_gracefully(
        self, naver_configured: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "stock_compass.markets.kr._kr_name", lambda c: "삼성전자"
        )
        with patch(
            "httpx.get",
            side_effect=httpx.HTTPError("network down"),
        ):
            assert _fetch_news_naver("005930", days=7) == []

    def test_skips_malformed_items(
        self, naver_configured: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "stock_compass.markets.kr._kr_name", lambda c: "삼성전자"
        )
        today = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S %z")
        with patch(
            "httpx.get",
            return_value=_mk_response(
                [
                    {"title": "", "originallink": "x", "pubDate": today},  # 빈 제목
                    {"title": "ok", "pubDate": "BAD_DATE"},  # 잘못된 날짜
                    {
                        "title": "valid",
                        "originallink": "https://example.com/valid",
                        "link": "y",
                        "description": "",
                        "pubDate": today,
                    },
                ]
            ),
        ):
            out = _fetch_news_naver("005930", days=7)
        assert len(out) == 1
        assert out[0].title == "valid"
