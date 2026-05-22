"""pytest 공용 픽스처. Phase 0에서는 placeholder."""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
