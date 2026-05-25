"""Craft REST API HTTP 클라이언트 — 격리 레이어.

Craft Pro API의 정확한 endpoint/payload는 사용자 환경에 따라 다를 수 있어
여기에 격리. 변경 시 이 모듈만 수정.

가정 (사용자가 Craft Settings → API에서 확인 필요):
- Endpoint: POST {base_url}/folders/{folder_id}/notes
- Auth: Bearer token (Authorization 헤더)
- Payload: {"title": "...", "content_markdown": "..."}
- 응답: {"id": "...", "url": "...", "folder_id": "..."}
"""

from __future__ import annotations

from typing import Any

import httpx

from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)


class CraftAPIError(RuntimeError):
    """Craft API 호출 실패 (인증·rate limit·서버 오류 포함)."""


class CraftAuthError(CraftAPIError):
    """401/403 — 토큰 누락 또는 무효."""


class CraftRateLimitError(CraftAPIError):
    """429 — Retry-After 헤더 honour."""


class CraftClient:
    """얇은 httpx 래퍼. tenacity 재시도는 호출자가 적용."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = "https://www.craft.do/api/v1",
        timeout_sec: float = 10.0,
    ) -> None:
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    def post_note(
        self,
        folder_id: str,
        *,
        title: str,
        content_markdown: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """폴더에 새 노트 생성. 응답 JSON 반환."""
        payload = {
            "title": title,
            "content_markdown": content_markdown,
        }
        headers = self._headers(idempotency_key=idempotency_key)
        return self._request(
            "POST", f"/folders/{folder_id}/notes", json=payload, headers=headers
        )

    def update_note(
        self,
        note_id: str,
        *,
        title: str,
        content_markdown: str,
    ) -> dict[str, Any]:
        """기존 노트 본문 갱신. 응답 JSON 반환."""
        payload = {"title": title, "content_markdown": content_markdown}
        return self._request(
            "PATCH", f"/notes/{note_id}", json=payload, headers=self._headers()
        )

    # ─── 내부 ───

    def _headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "User-Agent": "stock-compass/0.1 (Personal use)",
        }
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout_sec) as client:
                response = client.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            raise CraftAPIError(f"네트워크 오류: {e}") from e

        if response.status_code in (401, 403):
            raise CraftAuthError(
                f"인증 실패 ({response.status_code}) — CRAFT_API_TOKEN 확인"
            )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "?")
            raise CraftRateLimitError(
                f"rate limit 초과 — Retry-After={retry_after}s"
            )
        if response.status_code >= 400:
            raise CraftAPIError(
                f"{response.status_code} — {response.text[:200]}"
            )
        try:
            data = response.json()
        except ValueError as e:
            raise CraftAPIError(f"응답 JSON 파싱 실패: {e}") from e
        if not isinstance(data, dict):
            raise CraftAPIError(f"응답 형식 오류 (dict 아님): {type(data).__name__}")
        return data
