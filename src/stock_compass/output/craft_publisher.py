"""Craft Pro API 자동 발행 (CraftPublisher) + DB 중복 추적.

CraftExporter는 craft_exporter.py — file output 전용. 이 모듈은 순수 API 호출.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from typing import Any

from stock_compass.config import settings
from stock_compass.output._craft_client import (
    CraftAPIError,
    CraftAuthError,
    CraftClient,
)
from stock_compass.utils.dates import now_utc, to_iso_utc
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CraftPublishResult:
    note_id: str
    url: str
    folder_id: str
    published_at: datetime
    is_update: bool


class CraftPublisher:
    """Craft Pro API 자동 발행 + DB 중복 추적 (craft_publications).

    Token/folder_id는 settings에서 lazy 로드. 토큰 없으면 publish 호출 시
    명확한 CraftAuthError raise (호출자가 파일 fallback 안내).
    """

    def __init__(
        self,
        *,
        client: CraftClient | None = None,
        default_folder_id: str | None = None,
    ) -> None:
        self._client = client
        self.default_folder_id = default_folder_id or settings.craft_daily_folder_id

    def publish_daily_note(
        self,
        content: str,
        on_date: date_cls,
        *,
        folder_id: str | None = None,
        note_kind: str = "daily",
        charts: list[tuple[str, bytes]] | None = None,
        title: str | None = None,
    ) -> CraftPublishResult:
        """일일 노트 발행 (2단계: POST /documents → POST /blocks → POST /upload).

        같은 (note_kind, on_date) 발행 기록이 있으면 기존 문서 delete 후 신규 생성
        (URL은 매번 새로 발급되지만 노트 누적되지 않아 깔끔).

        Args:
            content: 본문 markdown
            on_date: 일자 (제목·dedup key)
            folder_id: 발행 폴더 (None이면 default_folder_id)
            note_kind: 'daily' | 'discover:<preset>' | 'weekly-discover' 등
            charts: 본문 뒤에 첨부할 (caption, png_bytes) 리스트 — 각 차트마다
                caption 텍스트 블록 + 이미지 업로드. 차트 실패는 본문 성공 막지 않음
            title: 노트 제목 override (기본: "stock-compass · YYYY-MM-DD")
        """
        target_folder = folder_id or self.default_folder_id
        if not target_folder:
            raise CraftAuthError(
                "CRAFT_DAILY_FOLDER_ID 미설정 — .env.local 확인"
            )

        client = self._get_client()
        resolved_title = title or f"stock-compass · {on_date.isoformat()}"

        from stock_compass.db import get_db_connection

        with get_db_connection() as conn:
            existing = _find_publication(conn, note_kind, on_date)
            is_update = existing is not None

            # 갱신: 기존 문서 soft-delete (휴지통). 실패해도 신규 생성은 계속.
            if existing:
                try:
                    client.delete_documents([existing["note_id"]])
                except CraftAPIError as e:
                    _logger.warning(
                        "이전 노트 삭제 실패 (계속 진행): %s — %s",
                        existing["note_id"],
                        e,
                    )

            created = client.create_document(
                folder_id=target_folder, title=resolved_title
            )
            new_id = str(created.get("id", ""))
            new_url = str(created.get("clickableLink", ""))
            if not new_id:
                raise CraftAPIError(
                    f"문서 생성 응답에 id 없음: {created}"
                )
            client.append_markdown_blocks(document_id=new_id, markdown=content)

            if charts:
                _append_charts(client, new_id, charts)

            _record_publication(
                conn,
                note_kind=note_kind,
                on_date=on_date,
                note_id=new_id,
                folder_id=target_folder,
                url=new_url,
            )

        _logger.info(
            "Craft %s: %s (%s)", "update" if is_update else "publish", new_id, new_url
        )
        return CraftPublishResult(
            note_id=new_id,
            url=new_url,
            folder_id=target_folder,
            published_at=now_utc(),
            is_update=is_update,
        )

    def _get_client(self) -> CraftClient:
        if self._client is not None:
            return self._client
        token = settings.craft_api_token
        if token is None:
            raise CraftAuthError(
                "CRAFT_API_TOKEN 미설정 — .env.local에 추가 후 재시도"
            )
        # CRAFT_API_TOKEN은 실제로는 base URL (secret 포함).
        # 예: https://connect.craft.do/links/<secret>/api/v1
        self._client = CraftClient(base_url=token.get_secret_value())
        return self._client


def _append_charts(
    client: CraftClient,
    document_id: str,
    charts: list[tuple[str, bytes]],
) -> None:
    """차트 첨부 — 각 항목마다 caption + image 업로드. 개별 실패는 격리."""
    if not charts:
        return
    try:
        client.append_markdown_blocks(
            document_id=document_id,
            markdown="## 📈 30일 점수 추이",
        )
    except CraftAPIError as e:
        _logger.warning("차트 섹션 헤더 추가 실패: %s", e)
        return

    for caption, png_bytes in charts:
        try:
            client.append_markdown_blocks(
                document_id=document_id, markdown=f"**{caption}**"
            )
            client.upload_image(
                document_id=document_id,
                image_bytes=png_bytes,
                content_type="image/png",
            )
        except CraftAPIError as e:
            _logger.warning("차트 첨부 실패 (%s): %s", caption, e)


def _find_publication(
    conn: sqlite3.Connection, note_kind: str, on_date: date_cls
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, note_id, folder_id, url
        FROM craft_publications
        WHERE note_kind = ? AND on_date = ?
        """,
        (note_kind, on_date.isoformat()),
    ).fetchone()
    return dict(row) if row else None


def _record_publication(
    conn: sqlite3.Connection,
    *,
    note_kind: str,
    on_date: date_cls,
    note_id: str,
    folder_id: str,
    url: str,
) -> None:
    conn.execute(
        """
        INSERT INTO craft_publications
          (note_kind, on_date, note_id, folder_id, url, published_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(note_kind, on_date) DO UPDATE SET
          note_id = excluded.note_id,
          folder_id = excluded.folder_id,
          url = excluded.url,
          published_at = excluded.published_at
        """,
        (
            note_kind,
            on_date.isoformat(),
            note_id,
            folder_id,
            url,
            to_iso_utc(now_utc()),
        ),
    )
