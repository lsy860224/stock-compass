"""프리셋 로더 — 6개 .sql 파일 인식 + 미존재 처리."""

from __future__ import annotations

import pytest

from stock_compass.screener.presets import (
    PresetNotFoundError,
    list_presets,
    load_preset,
    presets_dir,
)


class TestList:
    def test_finds_all_six_phase7_presets(self) -> None:
        names = {p.name for p in list_presets()}
        expected = {
            "deep_value_kr",
            "value_growth_kr",
            "momentum_us",
            "oversold_quality_global",
            "turnaround",
            "high_dividend_kr",
        }
        assert expected.issubset(names), f"누락: {expected - names}"

    def test_descriptions_extracted(self) -> None:
        presets = {p.name: p for p in list_presets()}
        # 첫 비-preset 주석이 설명으로 잡혀야 함
        deep_value = presets["deep_value_kr"]
        assert len(deep_value.description) > 0
        assert "preset:" not in deep_value.description.lower()


class TestLoad:
    def test_load_returns_sql_text(self) -> None:
        sql = load_preset("deep_value_kr")
        assert "SELECT" in sql.upper()
        assert "v_latest_scores" in sql.lower()

    def test_unknown_preset_raises(self) -> None:
        with pytest.raises(PresetNotFoundError, match="없음"):
            load_preset("nonexistent")


class TestPath:
    def test_presets_dir_exists(self) -> None:
        # 프로젝트 루트 / screeners / presets
        d = presets_dir()
        assert d.exists() and d.is_dir()
        assert d.name == "presets"
