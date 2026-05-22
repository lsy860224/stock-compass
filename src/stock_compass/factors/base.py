"""팩터 공통 — FactorScore 모델 + 기본 가중치."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FactorName = Literal["valuation", "fundamentals", "technical", "macro", "sentiment"]

# CLAUDE.md 8) 기본 가중치
DEFAULT_WEIGHTS: dict[FactorName, float] = {
    "valuation": 0.30,
    "fundamentals": 0.25,
    "technical": 0.20,
    "macro": 0.15,
    "sentiment": 0.10,
}
assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9, "가중치 합이 1이 아닙니다"


class FactorScore(BaseModel):
    """단일 팩터 산출 결과."""

    model_config = ConfigDict(frozen=True)

    name: FactorName
    score: float = Field(ge=0.0, le=100.0)
    weight: float = Field(ge=0.0, le=1.0)
    raw_values: dict[str, Any] = Field(default_factory=dict)
    note: str = ""
    source: str = Field(default="computed", description="api / cache / fallback / pykrx 등")


def neutral(name: FactorName, note: str, raw: dict[str, Any] | None = None) -> FactorScore:
    """데이터 부족·실패 시 50점 중립값."""
    return FactorScore(
        name=name,
        score=50.0,
        weight=DEFAULT_WEIGHTS[name],
        raw_values=raw or {},
        note=note,
        source="fallback",
    )
