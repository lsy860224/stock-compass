"""Anthropic 모델별 단가 + 토큰 분할 추정.

Phase 4 시점 기준. 변동 시 _PRICING 갱신.
"""

from __future__ import annotations

# 모델별 ($/1M input, $/1M output).
_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.25, 1.25),
    "claude-haiku-4-5": (0.25, 1.25),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
}
_DEFAULT_PRICING = (1.0, 5.0)


def split_tokens(total: int) -> tuple[int, int]:
    """SDK가 usage 분리 못 주면 7:3 추정 (요약 input >> output)."""
    if total <= 0:
        return (0, 0)
    in_t = int(total * 0.7)
    return (in_t, total - in_t)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _PRICING.get(model, _DEFAULT_PRICING)
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate
