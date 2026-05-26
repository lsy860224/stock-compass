"""Craft Space API HTTP 클라이언트 — 격리 레이어.

실제 Craft API 스펙 (2026-05 검증):
- Base URL = CRAFT_API_TOKEN 값 자체 (`https://connect.craft.do/links/<secret>/api/v1`).
  URL에 인증 secret이 포함되어 있어 별도 Authorization 헤더 불필요.
- 발급: Craft 앱 → Imagine 탭 → Add API Connection (폴더 scope 권장)
- OpenAPI spec: `<base_url>/openapi.json`

발행 흐름 (2단계):
1) POST /documents — 문서 생성 (title만 받음)
2) POST /blocks --pageId=<documentId> --markdown=<content>
   markdown의 `\n\n` paragraph break를 craft가 자동으로 여러 블록으로 분리.

갱신 전략: DELETE /documents + 새 POST /documents (URL은 매번 새로 발급되지만 노트가
누적되지 않아 깔끔. craft_publications에 새 ID 갱신).
"""

from __future__ import annotations

from typing import Any

import httpx

from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)


class CraftAPIError(RuntimeError):
    """Craft API 호출 실패 (네트워크·400~599 응답 포함)."""


class CraftAuthError(CraftAPIError):
    """URL secret 잘못됐거나 만료 (401/403) — 사용자가 새 connection 발급 필요."""


class CraftRateLimitError(CraftAPIError):
    """429 — Retry-After 헤더 honour."""


class CraftClient:
    """얇은 httpx 래퍼. URL 인증 패턴 — 헤더 없음."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: float = 15.0,
    ) -> None:
        # base_url 자체가 secret 포함이라 그대로 보관 (로깅 시 마스킹 필요)
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    # ─── public — 문서 ───

    def create_document(self, *, folder_id: str, title: str) -> dict[str, Any]:
        """POST /documents — 폴더에 새 문서 1개 생성. 응답에서 id + clickableLink 반환.

        Response shape: {"items": [{"id", "title", "clickableLink"}]}
        """
        payload = {
            "documents": [{"title": title}],
            "destination": {"folderId": folder_id},
        }
        data = self._request("POST", "/documents", json=payload)
        items = data.get("items") or []
        if not items:
            raise CraftAPIError(f"문서 생성 응답에 items 없음: {data}")
        return dict(items[0])

    def delete_documents(self, document_ids: list[str]) -> list[str]:
        """DELETE /documents — soft delete (휴지통으로). 응답: 삭제된 ID 배열."""
        if not document_ids:
            return []
        data = self._request(
            "DELETE", "/documents", json={"documentIds": document_ids}
        )
        return list(data.get("items") or [])

    # ─── public — 블록 ───

    def append_markdown_blocks(
        self, *, document_id: str, markdown: str
    ) -> list[dict[str, Any]]:
        """POST /blocks — 문서 끝에 markdown을 텍스트 블록(들)로 추가.

        Craft는 markdown 안의 `\\n\\n` paragraph break를 자동으로 여러 블록으로 분리.
        헤딩(`## `), 리스트(`- `), 코드펜스(``` ` ```)도 자동 인식.
        """
        if not markdown.strip():
            return []
        payload = {
            "blocks": [{"type": "text", "markdown": markdown}],
            "position": {"position": "end", "pageId": document_id},
        }
        data = self._request("POST", "/blocks", json=payload)
        return list(data.get("items") or [])

    # ─── public — 이미지 업로드 ───

    def upload_image(
        self,
        *,
        document_id: str,
        image_bytes: bytes,
        content_type: str = "image/png",
    ) -> dict[str, Any]:
        """POST /upload?position=end&pageId=... — 문서 끝에 이미지 블록 추가.

        body는 raw octet-stream. Response: {"blockId", "assetUrl"}.
        """
        if not image_bytes:
            raise CraftAPIError("upload_image: 빈 image_bytes")
        return self._request(
            "POST",
            "/upload",
            params={"position": "end", "pageId": document_id},
            content=image_bytes,
            headers={"Content-Type": content_type},
        )

    # ─── 내부 ───

    def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {}) or {}
        headers.setdefault("User-Agent", "stock-compass/0.1 (Personal use)")
        headers.setdefault("Content-Type", "application/json")

        try:
            with httpx.Client(timeout=self.timeout_sec) as client:
                response = client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise CraftAPIError(f"네트워크 오류: {e}") from e

        if response.status_code in (401, 403):
            raise CraftAuthError(
                f"인증 실패 ({response.status_code}) — "
                "CRAFT_API_TOKEN URL이 만료됐거나 잘못됨. "
                "Craft 앱 → Imagine 탭에서 새 connection 발급."
            )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "?")
            raise CraftRateLimitError(
                f"rate limit 초과 — Retry-After={retry_after}s"
            )
        if response.status_code >= 400:
            raise CraftAPIError(
                f"{method} {path} — HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        try:
            data = response.json()
        except ValueError as e:
            raise CraftAPIError(f"응답 JSON 파싱 실패: {e}") from e
        if not isinstance(data, dict):
            raise CraftAPIError(
                f"응답 형식 오류 (dict 아님): {type(data).__name__}"
            )
        return data
