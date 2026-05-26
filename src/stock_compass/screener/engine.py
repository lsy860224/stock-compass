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
from stock_compass.markets.base import Market
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
        budget: float | None = None,
        budget_market: Market | None = None,
    ) -> ScreenerResult:
        """budget 지정 시 원본 SQL을 CTE로 감싸 `WHERE price <= :budget` 추가.

        budget_market 지정 시 시장 통화 분리 (KRW/USD 환산 회피).
        """
        self._validate(sql)
        rewritten = self._expand_v_at_date(sql)

        merged_params: dict[str, Any] = dict(params or {})
        if budget is not None:
            rewritten = self._wrap_with_budget(rewritten, budget, budget_market)
            merged_params["__budget"] = budget
            if budget_market is not None:
                merged_params["__bm"] = budget_market

        target_limit = self._target_limit(limit)
        final_sql = self._enforce_limit(rewritten, target_limit)

        start = time.perf_counter()
        with self._ro_connection() as conn:
            self._install_timeout(conn, start)
            try:
                cur = conn.execute(final_sql, merged_params)
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

    def _wrap_with_budget(
        self, sql: str, budget: float, market: Market | None
    ) -> str:
        """원본 SQL을 CTE로 감싸 budget 필터 적용.

        price 컬럼 자동 탐지: `price`, `price_krw`, `price_usd` 중 첫 매치 사용.
        market 컬럼이 base에 없으면 `--budget-market` 무시 (안전 fallback).
        base SQL이 ORDER BY 를 가져도 outer SELECT는 정보를 잃으므로 outer에
        `ORDER BY composite_score DESC NULLS LAST` 부착 (있을 때만).
        """
        _ = budget  # SQL 바인딩으로만 전달
        clean = sql.rstrip().rstrip(";").rstrip()
        m = _LIMIT_TAIL.search(clean)
        if m is not None:
            clean = clean[: m.start()].rstrip()

        base_columns = self._probe_columns(clean)
        price_col = next(
            (c for c in ("price", "price_krw", "price_usd") if c in base_columns),
            None,
        )
        if price_col is None:
            raise ScreenerError(
                "이 SQL은 price 컬럼을 노출하지 않습니다 — "
                "budget 사용 불가 (SELECT에 price/price_krw/price_usd 중 하나 추가)"
            )

        has_market = "market" in base_columns
        market_clause = (
            " AND market = :__bm" if market is not None and has_market else ""
        )
        order_clause = (
            "\nORDER BY composite_score DESC NULLS LAST"
            if "composite_score" in base_columns
            else ""
        )
        return (
            f"WITH __base AS (\n{clean}\n)\n"
            f"SELECT * FROM __base "
            f"WHERE {price_col} IS NOT NULL AND {price_col} <= :__budget"
            f"{market_clause}"
            f"{order_clause}"
        )

    def _probe_columns(self, base_sql: str) -> set[str]:
        """base SQL을 LIMIT 0으로 한 번 실행해 컬럼 이름 추출."""
        probe = f"WITH __probe AS (\n{base_sql}\n) SELECT * FROM __probe LIMIT 0"
        try:
            with self._ro_connection() as conn:
                cur = conn.execute(probe)
                return {d[0] for d in (cur.description or [])}
        except sqlite3.OperationalError:
            return set()

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
