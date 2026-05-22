"""ScreenerEngine — RO 모드 SQL 실행 + 안전 검증 + LIMIT 강제.

CLAUDE.md 14) 원칙: SQLite 연결은 `mode=ro` URI — DDL/DML 자동 차단.
추가로 위험 키워드 사전 검사로 사용자에게 명확한 에러 메시지 제공.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date as date_cls
from pathlib import Path
from typing import Any

from stock_compass.config import settings
from stock_compass.utils.dates import today_kst
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

# 쓰기성 SQL 키워드 (대소문자 무관, word boundary로 식별)
_FORBIDDEN = re.compile(
    r"\b("
    r"DROP|DELETE|UPDATE|INSERT|ALTER|ATTACH|DETACH|CREATE|REPLACE|"
    r"TRUNCATE|VACUUM|REINDEX|PRAGMA"
    r")\b",
    re.IGNORECASE,
)

# v_at_date('YYYY-MM-DD') 패턴 (Phase 7-5 backtest 진입점)
_AT_DATE = re.compile(r"v_at_date\(\s*'(\d{4}-\d{2}-\d{2})'\s*\)", re.IGNORECASE)

# LIMIT N 또는 LIMIT N OFFSET M 검출 (쿼리 말단)
_LIMIT_TAIL = re.compile(
    r"\bLIMIT\s+(\d+)(\s+OFFSET\s+\d+)?\s*;?\s*\Z", re.IGNORECASE
)


class ScreenerError(RuntimeError):
    """스크리너 실행 거부 (안전 검증 실패·LIMIT 위반 등)."""


@dataclass(frozen=True, slots=True)
class ScreenerResult:
    """단일 실행 결과 — 출력 어댑터(table/csv/json/craft)가 공통 소비."""

    columns: list[str]
    rows: list[dict[str, Any]] = field(default_factory=list)
    sql: str = ""
    preset_name: str | None = None
    elapsed_ms: int = 0
    limit_applied: int | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)


class ScreenerEngine:
    """읽기 전용 SQL 실행기. 모든 사용자 입력 SQL은 이 게이트만 통과."""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        default_limit: int | None = None,
        max_limit: int = 5_000,
        timeout_sec: int = 5,
    ) -> None:
        self.db_path = db_path or settings.db_path
        self.default_limit = default_limit or settings.screener_default_limit
        self.max_limit = max_limit
        self.timeout_sec = timeout_sec

    # ─── public ───

    def run_sql(
        self,
        sql: str,
        *,
        params: Mapping[str, Any] | None = None,
        limit: int | None = None,
        preset_name: str | None = None,
    ) -> ScreenerResult:
        self._validate(sql)
        rewritten = self._expand_v_at_date(sql)
        target_limit = self._target_limit(limit)
        final_sql = self._enforce_limit(rewritten, target_limit)

        start = time.perf_counter()
        with self._ro_connection() as conn:
            self._install_timeout(conn, start)
            try:
                cur = conn.execute(final_sql, dict(params or {}))
            except sqlite3.OperationalError as e:
                raise ScreenerError(f"SQL 실행 오류: {e}") from e
            columns = [d[0] for d in (cur.description or [])]
            rows = [dict(zip(columns, r, strict=False)) for r in cur.fetchall()]
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        result = ScreenerResult(
            columns=columns,
            rows=rows,
            sql=final_sql,
            preset_name=preset_name,
            elapsed_ms=elapsed_ms,
            limit_applied=target_limit,
        )
        self._record_run(result, original_sql=sql)
        return result

    # ─── 안전·정규화 ───

    def _validate(self, sql: str) -> None:
        stripped = sql.strip()
        if not stripped:
            raise ScreenerError("빈 SQL")
        if ";" in stripped[:-1].rstrip(";"):
            # 마지막 세미콜론은 OK, 중간 ; 는 multi-statement 위험
            raise ScreenerError("multi-statement 차단 (한 번에 한 쿼리만)")
        m = _FORBIDDEN.search(stripped)
        if m:
            raise ScreenerError(
                f"읽기 전용 모드 — 위험 키워드 차단: {m.group(0).upper()}"
            )

    def _expand_v_at_date(self, sql: str) -> str:
        m = _AT_DATE.search(sql)
        if not m:
            return sql
        target = m.group(1)
        try:
            target_date = date_cls.fromisoformat(target)
        except ValueError as e:
            raise ScreenerError(f"v_at_date 일자 형식 오류: {target}") from e
        if target_date > today_kst():
            raise ScreenerError(f"v_at_date — 미래 날짜 차단: {target}")
        # Phase 7-5 백테스트 진입점 — 현재는 미구현
        raise ScreenerError(
            "v_at_date()는 Phase 7-5 백테스트에서 구현 예정 "
            "(현재는 v_latest_scores만 사용 가능)"
        )

    def _target_limit(self, requested: int | None) -> int:
        if requested is None:
            return self.default_limit
        if requested <= 0:
            raise ScreenerError(f"LIMIT는 양수: {requested}")
        if requested > self.max_limit:
            _logger.warning(
                "요청 LIMIT %d > max %d — 강제 클램프", requested, self.max_limit
            )
            return self.max_limit
        return requested

    def _enforce_limit(self, sql: str, limit: int) -> str:
        clean = sql.rstrip().rstrip(";").rstrip()
        m = _LIMIT_TAIL.search(clean)
        if m is None:
            return f"{clean}\nLIMIT {limit}"
        existing = int(m.group(1))
        if existing > self.max_limit:
            # 기존 LIMIT을 max로 클램프
            return clean[: m.start()].rstrip() + f"\nLIMIT {self.max_limit}"
        return clean

    # ─── 연결·타임아웃 ───

    def _ro_connection(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path.absolute()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _install_timeout(self, conn: sqlite3.Connection, start: float) -> None:
        deadline = start + self.timeout_sec

        def _check() -> int:
            return 1 if time.perf_counter() > deadline else 0

        conn.set_progress_handler(_check, 1_000)

    # ─── 감사 로그 (별도 RW 연결) ───

    def _record_run(self, result: ScreenerResult, *, original_sql: str) -> None:
        from stock_compass.db import get_db_connection

        tickers = [r.get("ticker_id") or r.get("code") for r in result.rows]
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO screener_runs
                  (preset_name, sql_text, result_count, result_tickers, elapsed_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    result.preset_name,
                    original_sql,
                    result.row_count,
                    json.dumps(tickers, ensure_ascii=False, default=str),
                    result.elapsed_ms,
                ),
            )
