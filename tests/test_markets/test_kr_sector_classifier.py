"""KSIC → GICS 매핑 + DART company API → sector lookup."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from stock_compass.markets._kr_sector_classifier import (
    _KSIC_TO_GICS,
    ALL_GICS_SECTORS,
    classify_kr_sector_by_ksic,
    get_kr_sector,
)


class TestClassifyKsic:
    @pytest.mark.parametrize(
        "ksic,expected",
        [
            # 삼성전자/SK하이닉스 (반도체)
            ("264", "Information Technology"),
            ("2612", "Information Technology"),
            ("26110", "Information Technology"),
            # 카카오/NAVER (포털)
            ("63120", "Communication Services"),
            # 현대차 (자동차)
            ("30121", "Consumer Discretionary"),
            # 화학/철강 (Materials)
            ("20121", "Materials"),
            ("241", "Materials"),
            # 의약품
            ("21210", "Health Care"),
            # 은행
            ("64120", "Financials"),
            # 통신
            ("61210", "Communication Services"),
            # 식료품
            ("10130", "Consumer Staples"),
            # 자동차 판매
            ("45200", "Consumer Discretionary"),
            # 1자리 padding (zfill로 "01" 처리 → 농업 매핑)
            ("1", None),
        ],
    )
    def test_mapping(self, ksic: str, expected: str | None) -> None:
        result = classify_kr_sector_by_ksic(ksic)
        if expected is None and ksic == "1":
            # "1"은 zfill로 "01" 처리 → Consumer Staples
            assert result == "Consumer Staples"
        else:
            assert result == expected

    def test_none_input(self) -> None:
        assert classify_kr_sector_by_ksic(None) is None
        assert classify_kr_sector_by_ksic("") is None

    def test_all_mapped_to_valid_gics(self) -> None:
        # 모든 매핑값이 GICS 11 sector 중 하나
        for sector in _KSIC_TO_GICS.values():
            assert sector in ALL_GICS_SECTORS

    def test_unknown_ksic_returns_none(self) -> None:
        # KSIC 매핑에 없는 prefix
        assert classify_kr_sector_by_ksic("99999") is None


class TestGetKrSector:
    def test_no_dart_key_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stock_compass.config import settings

        monkeypatch.setattr(settings, "dart_api_key", None)
        # cache 도 비어야 함 — tmp 캐시 디렉토리 사용 안 함, settings.cache_dir 직접
        # → DART 호출 없이 None 반환 검증만 (실제 캐시 hit 시 다른 결과)
        result = get_kr_sector("NONEXIST_TEST_CODE_999")
        assert result is None

    def test_dart_success_mocked(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        from pydantic import SecretStr

        from stock_compass.config import settings

        # 임시 캐시 디렉토리 → 다른 테스트와 격리
        monkeypatch.setattr(settings, "cache_dir", tmp_path)
        monkeypatch.setattr(
            settings, "dart_api_key", SecretStr("dummy_key")
        )

        # OpenDartReader.company 가 induty_code 반환하도록 mock
        class _FakeDart:
            def __init__(self, key):
                pass

            def company(self, code):
                return {"induty_code": "264", "corp_name": "Mock Corp"}

        with patch(
            "OpenDartReader.__init__", _FakeDart.__init__
        ), patch("OpenDartReader.company", _FakeDart.company):
            result = get_kr_sector("MOCK_TEST")
        assert result == "Information Technology"

    def test_dart_failure_returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        from pydantic import SecretStr

        from stock_compass.config import settings

        monkeypatch.setattr(settings, "cache_dir", tmp_path)
        monkeypatch.setattr(settings, "dart_api_key", SecretStr("dummy"))

        # DART 호출이 예외 → None
        with patch(
            "OpenDartReader.__init__",
            side_effect=Exception("dart fail"),
        ):
            assert get_kr_sector("FAIL_TEST") is None
