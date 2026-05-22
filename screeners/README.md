# 스크리너 디렉토리

> 본인의 SQL 스크리너 저장 위치. `presets/`는 패키지 기본 제공.
> 본인 작성 SQL은 이 폴더 루트에 저장 (gitignore 대상 — 전략 보호).

## 사용

### 프리셋 실행

```bash
uv run stock-compass screen --preset deep_value_kr
uv run stock-compass screen --preset value_growth_kr --limit 10
uv run stock-compass screen --list-presets
```

### 본인 SQL 저장 + 실행

```bash
# 본인 전략을 SQL 파일로 저장
cat > screeners/my_strategy.sql << 'EOF'
SELECT code, name, composite_score, per, roe
FROM v_latest_scores
WHERE market = 'KR'
  AND composite_score >= 75
  AND per BETWEEN 5 AND 20
  AND roe >= 0.15
ORDER BY composite_score DESC
LIMIT 10;
EOF

uv run stock-compass screen --file screeners/my_strategy.sql
```

### 인라인 SQL

```bash
uv run stock-compass screen --sql "
  SELECT code, name FROM v_latest_scores
  WHERE composite_score >= 80 ORDER BY composite_score DESC LIMIT 5
"
```

### 인터랙티브 REPL

```bash
uv run stock-compass screen --interactive

> .views                 # 뷰 목록
> .fields v_latest_scores  # 칼럼 목록
> .preset deep_value_kr  # 프리셋 SQL 출력 (편집 가능)
> SELECT * FROM v_latest_scores WHERE rsi_14 < 30 LIMIT 5;
> .save my_oversold     # 마지막 SQL을 screeners/my_oversold.sql 저장
> .quit
```

## 출력 옵션

```bash
# 터미널 (기본)
uv run stock-compass screen --preset momentum_us

# CSV
uv run stock-compass screen --preset deep_value_kr --format csv > value.csv

# JSON
uv run stock-compass screen --preset momentum_us --format json

# Craft 노트
uv run stock-compass screen --preset value_growth_kr --to-craft

# 워치리스트 자동 추가 (다음 배치부터 추적)
uv run stock-compass screen --preset turnaround --add-to-watchlist --group screening

# 하이브리드 sentiment 심층 분석 프롬프트 생성
uv run stock-compass screen --preset value_growth_kr --prompt-deepdive
# → data/prompts/YYYYMMDD-deepdive-value_growth_kr.md 생성
# → Claude.ai에 복붙 → 응답을 stock-compass sentiment import 로 다시 입력
```

## 백테스트

```bash
uv run stock-compass screen --preset value_growth_kr \
  --backtest --start 2025-01-01 --end 2026-05-22 \
  --rebalance monthly --forward-period 3m
```

## 칼럼 치트시트

```bash
uv run stock-compass screen --list-fields
```

또는 `docs/SCREENER_SPEC.md` 4) 참조.

## 안전장치 (자동 적용)

- ✅ 읽기 전용 모드 (DROP/DELETE/UPDATE 불가)
- ✅ 자동 LIMIT (없으면 50, 최대 5000)
- ✅ 쿼리 타임아웃 5초
- ✅ 모든 결과 하단에 한계 명시 자동 삽입

## 본인 SQL 작성 팁

```sql
-- 1. v_latest_scores 부터 시작 (95% 케이스 충분)
SELECT * FROM v_latest_scores WHERE market = 'KR' LIMIT 5;

-- 2. 시계열 분석은 v_score_history
SELECT date, composite_score FROM v_score_history
WHERE code = '005930' ORDER BY date DESC LIMIT 30;

-- 3. JSON1 함수로 raw_values에서 임의 지표 추출
SELECT code, json_extract(raw_values, '$.beta_1y') AS beta
FROM factor_scores
WHERE factor_name = 'technical' AND date = (SELECT MAX(date) FROM factor_scores);

-- 4. 윈도우 함수 (SQLite 3.25+)
SELECT code, composite_score,
  LAG(composite_score, 30) OVER (PARTITION BY ticker_id ORDER BY date) AS score_30d_ago
FROM v_score_history;
```
