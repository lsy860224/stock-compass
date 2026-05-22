# 구현 가이드: stock-compass

> Claude Code에서 아래 Phase 프롬프트를 순서대로 실행.
> 예상 소요: 풀타임 5~7일 / 야간·주말 3~4주
> 각 Phase 끝나면 git commit 권장.

---

## 사전 준비 (Day -1)

1. **macOS Homebrew 설치 확인**: `brew --version`
2. **uv 설치**: `brew install uv` (Python 패키지 매니저)
3. **Python 3.12+ 확인**: `uv python install 3.12`
4. **API 키 발급**:
   - Anthropic: https://console.anthropic.com → Keys
   - FRED: https://fred.stlouisfed.org/docs/api/api_key.html
   - DART: https://opendart.fss.or.kr (한국 공시, 무료)
5. **본인 워치리스트 정리** (KR 6자리 코드 + US 티커)

---

## Phase 0: 프로젝트 초기화 (Day 1, 2시간)

### Claude Code 프롬프트

```
이 프로젝트는 CLAUDE.md를 따른다. 먼저 CLAUDE.md를 읽고 다음을 수행해줘:

1. pyproject.toml 생성:
   - 프로젝트명: stock-compass
   - Python >=3.12
   - dependencies:
     - yfinance>=0.2.40
     - pykrx>=1.0.45
     - fredapi>=0.5.2
     - anthropic>=0.40.0
     - pandas>=2.2
     - numpy>=2.0
     - typer>=0.12
     - rich>=13.7
     - pydantic>=2.8
     - python-dotenv>=1.0
     - tenacity>=9.0
     - httpx>=0.27
     - opendartreader>=0.2.2
   - dev-dependencies:
     - pytest>=8.3
     - pytest-cov>=5.0
     - pytest-mock>=3.14
     - ruff>=0.6
     - mypy>=1.11
   - ruff 설정: line-length=100, target-version=py312, select=[E,F,I,N,UP,B,A,C4,SIM,PL]
   - mypy 설정: strict=true, python_version=3.12

2. 디렉토리 구조 생성 (CLAUDE.md 4)에 명시):
   - src/stock_compass/ 하위 전체 (markets, factors, scoring, alerts, output, llm, db, utils)
   - 각 디렉토리에 __init__.py 빈 파일
   - tests/conftest.py
   - data/, logs/ 폴더 (gitkeep만)

3. .gitignore 생성:
   - Python 표준 + .env*.local + data/*.db + data/cache/ + logs/ + __pycache__ + .venv + .ruff_cache + .mypy_cache + .pytest_cache

4. src/stock_compass/config.py 생성:
   - python-dotenv로 .env.local 로드
   - 환경변수를 Pydantic Settings로 검증
   - 필수 키 누락 시 명확한 에러 메시지

5. src/stock_compass/utils/logging.py 생성:
   - rich.logging.RichHandler 사용
   - logs/stock-compass.log 파일 로테이션 (10MB)

6. src/stock_compass/cli.py 생성:
   - typer 진입점
   - 명령어 뼈대: score, batch, alert, report (모두 NotImplementedError 우선)

완료 후 'uv sync && uv run python -m stock_compass --help' 실행 가능해야 함.
```

### 검증

```bash
cd ~/Projects/stock-compass
uv sync
uv run python -m stock_compass --help
# Usage: stock-compass [OPTIONS] COMMAND [ARGS]...
# Commands: score, batch, alert, report
```

✅ Commit: `feat: initial project scaffolding`

---

## Phase 1: 단일 종목 점수 엔진 (Day 2, 4시간)

### Claude Code 프롬프트

```
CLAUDE.md를 참고해서 단일 종목 점수 계산 기능 구현:

1. src/stock_compass/markets/base.py:
   - MarketAdapter ABC
   - 추상 메서드: get_price_history, get_fundamentals, get_news, get_disclosures, get_trading_hours, get_currency
   - 공통 데이터 모델 (Pydantic): PriceHistory, Fundamentals, News, Disclosure

2. src/stock_compass/markets/us.py:
   - UsAdapter(MarketAdapter)
   - yfinance 사용
   - tenacity로 재시도 (3회, exponential backoff)
   - 24시간 디스크 캐시 (data/cache/{ticker}-{date}.parquet)

3. src/stock_compass/markets/kr.py:
   - KrAdapter(MarketAdapter)
   - yfinance(.KS/.KQ 접미사 자동) + pykrx 보조
   - 6자리 숫자 코드를 yfinance 형식으로 변환 (005930 → 005930.KS, KOSDAQ는 .KQ)
   - DART 공시는 OpenDartReader 사용 (lazy import)

4. src/stock_compass/markets/__init__.py:
   - get_market(ticker: str) -> MarketAdapter
   - 자동 감지: 6자리 숫자 → KR, 알파벳 → US
   - 명시 옵션도 지원

5. src/stock_compass/factors/ 5개 파일 (valuation, fundamentals, technical, macro, sentiment):
   - 각 파일에 calculate(adapter: MarketAdapter, ticker: str) -> FactorScore 함수
   - FactorScore = pydantic 모델 (name, score 0~100, weight, raw_values dict, note str)
   - sentiment.py는 Phase 4까지 placeholder (점수 50 반환)
   - macro.py는 fredapi로 10Y, VIX, DXY 가져와서 risk-on/off 환산

6. src/stock_compass/scoring/engine.py:
   - ScoringEngine 클래스
   - analyze(ticker: str, market: str | None = None) -> CompositeScore
   - 5개 팩터 병렬 호출 (asyncio 또는 concurrent.futures)
   - 가중 평균 → CompositeScore(ticker, total, verdict, factors, computed_at)
   - verdict: 관심권/중립/주의 (CLAUDE.md 8) 규칙)
   - 면책 문구 자동 포함

7. cli.py에 score 명령 구현:
   - stock-compass score AAPL
   - stock-compass score 005930
   - stock-compass score 005930 --market kr (강제)
   - rich.table로 결과 출력

테스트:
- tests/test_factors/test_technical.py: RSI 계산 검증 (mock 데이터)
- tests/test_scoring/test_engine.py: 가중치 합산 검증
- 외부 API는 vcrpy 또는 monkeypatch로 mock

완료 후 'uv run stock-compass score AAPL' 실행 시 점수 + 5개 팩터 + 면책 표 출력.
```

### 검증

```bash
uv run stock-compass score AAPL
uv run stock-compass score 005930
uv run pytest
uv run ruff check . && uv run mypy src/
```

✅ Commit: `feat: single-ticker scoring engine with KR/US adapters`

---

## Phase 2: 워치리스트 배치 + SQLite (Day 3, 4시간)

### Claude Code 프롬프트

```
CLAUDE.md 6)·8)을 따라 일일 배치 기능 구현:

1. src/stock_compass/db/schema.py:
   - SQLModel 또는 raw SQL CREATE TABLE 스크립트
   - 테이블: tickers, snapshots, factor_scores, composite_scores, alerts, trades
   - news_summaries는 Phase 4에서 추가 (지금은 정의만)
   - 모든 시각은 UTC ISO 문자열 저장
   - 자세한 스키마는 docs/DB_SCHEMA.md 참조

2. src/stock_compass/db/migrations.py:
   - schema_version 테이블
   - migrate() 함수: 현재 버전 확인 후 누락된 마이그레이션 순차 실행
   - 각 마이그레이션은 functions list로

3. src/stock_compass/db/repository.py:
   - get_db_connection() (context manager)
   - upsert_composite_score(score: CompositeScore)
   - upsert_factor_scores(ticker, factors, date)
   - get_score_history(ticker, days) -> list[CompositeScore]
   - get_last_score(ticker) -> CompositeScore | None
   - 모든 함수에 type hint + docstring

4. src/stock_compass/scoring/engine.py 확장:
   - analyze_watchlist(tickers: list[str]) -> list[CompositeScore]
   - 병렬 처리 (max_workers=5, API 부담 고려)
   - 진행률 표시 (rich.progress)
   - 결과 자동 DB 저장

5. cli.py에 batch 명령:
   - stock-compass batch (env의 WATCHLIST_KR + WATCHLIST_US 전체)
   - stock-compass batch --market kr (한국만)
   - stock-compass batch --market us (미국만)
   - stock-compass batch --tickers AAPL,MSFT (직접 지정)
   - 실행 후 rich.table로 점수 순위 출력

6. cli.py에 history 명령 (추가):
   - stock-compass history AAPL --days 30
   - 최근 N일 점수 추이 + 팩터별 변화

테스트:
- tests/test_db/test_repository.py: 메모리 SQLite로 CRUD 검증
- tests/test_scoring/test_batch.py: mock으로 병렬 처리 검증

완료 후 'uv run stock-compass batch' 실행 시 워치리스트 전체 점수 → DB 저장.
```

✅ Commit: `feat: watchlist batch + SQLite persistence`

---

## Phase 3: Craft 일일 노트 생성 (Day 4, 3시간)

### Claude Code 프롬프트

```
CLAUDE.md 10) + docs/CRAFT_TEMPLATE.md 참고:

1. src/stock_compass/output/craft.py:
   - CraftExporter 클래스
   - render_daily_note(scores: list[CompositeScore], date: datetime) -> str
   - 템플릿은 docs/CRAFT_TEMPLATE.md의 마크다운 구조 그대로
   - 종목별 카드 + 일일 요약 헤더 + 매매 일지 빈 섹션

2. CraftExporter에 메서드 추가:
   - export_to_file(content: str, date: datetime) -> Path
   - 위치: data/craft_export/YYYY-MM-DD.md
   - 동일 파일 있으면 .bak 백업 후 덮어쓰기

3. macOS Folder Watch 안내 README:
   - data/craft_export/를 Craft의 워치 폴더로 설정하는 방법
   - 또는 수동 임포트 절차

4. cli.py에 report 명령:
   - stock-compass report
   - 오늘 날짜의 모든 점수 → Craft MD 생성
   - stock-compass report --date 2026-05-21 (과거)
   - 생성된 파일 경로 출력 + Finder에서 열기 옵션 (--open)

5. src/stock_compass/output/terminal.py:
   - render_score_table(scores) -> Table (rich.table)
   - render_factor_breakdown(score) -> Tree (rich.tree)
   - 면책 문구 자동 footer 출력

테스트:
- tests/test_output/test_craft.py: 템플릿 렌더링 검증 (snapshot test)

완료 후 'uv run stock-compass report' 실행 시 오늘자 Craft 노트 생성.
```

✅ Commit: `feat: Craft daily note exporter`

---

## Phase 4: 뉴스·공시 요약 (Claude API) (Day 5, 4시간)

### Claude Code 프롬프트

```
CLAUDE.md 12) 비용·법률 주의사항 + DATA_SOURCES.md 참고:

1. src/stock_compass/llm/summarizer.py:
   - ClaudeSummarizer 클래스
   - summarize_news(ticker, news_items: list[News]) -> NewsSummary
   - summarize_disclosures(ticker, disclosures: list[Disclosure]) -> DisclosureSummary
   - 모델: claude-sonnet-4-6 (또는 claude-haiku-4-5로 비용 절약)
   - 시스템 프롬프트: "당신은 금융 뉴스 분석가다. 객관적 사실만 추출. 추측 금지. 톤(긍정/부정/중립)만 분류."
   - 출력 형식: JSON (요약 3줄 + 톤 점수 -10~+10 + 핵심 키워드 5개)

2. src/stock_compass/factors/sentiment.py 실제 구현:
   - 지난 30일 뉴스·공시 수집 (markets.get_news + get_disclosures)
   - Claude 요약 → 톤 점수 평균
   - DB news_summaries 테이블에 캐싱 (같은 source URL은 재사용)
   - 캐시 hit 시 API 호출 생략

3. 비용 관리:
   - 일 1만 토큰 한도 (config에서 조정 가능)
   - 한도 초과 시 sentiment 50점 + 경고 로그
   - 토큰 사용량 DB에 기록 (debug용)

4. CLI:
   - stock-compass news AAPL --days 7 (개별 뉴스 요약 보기)
   - score/batch 실행 시 자동으로 sentiment factor 활성화

5. 면책:
   - 모든 요약에 "AI 생성 요약. 원문 확인 필수." footer

테스트:
- tests/test_llm/test_summarizer.py: Anthropic SDK mock으로 호출 형식만 검증
- 실제 API 호출 테스트는 별도 마커 (pytest -m integration)
```

✅ Commit: `feat: news/disclosure summarization via Claude API`

---

## Phase 5: 알림 시스템 (3종 트리거) (Day 6, 4시간)

### Claude Code 프롬프트

```
CLAUDE.md 9)에 따라 3종 알림 트리거 구현:

1. src/stock_compass/alerts/threshold.py:
   - ThresholdAlert 클래스
   - check(current: CompositeScore, previous: CompositeScore | None) -> Alert | None
   - 조건: 현재 ≥80 AND (이전 <80 OR 없음) → BUY_ZONE 진입
   - 조건: 현재 ≤30 AND (이전 >30 OR 없음) → CAUTION_ZONE 진입
   - 24h 내 동일 종목·동일 zone 재발화 X

2. src/stock_compass/alerts/delta.py:
   - DeltaAlert: 24시간 내 점수 변화 ≥15
   - DB에서 24h 전 점수 조회 → 현재와 비교

3. src/stock_compass/alerts/daily.py:
   - DailyAlert: 매일 정해진 시각 (07:00 KST)
   - 전체 워치리스트 점수 요약 + 점수 상위/하위 3종목

4. src/stock_compass/output/notify.py:
   - macos_notify(title, message, subtitle=None) — osascript
   - send_alert(alert: Alert) -> None
   - 알림 후 DB alerts 테이블 기록

5. cli.py에 alert 명령:
   - stock-compass alert (모든 트리거 체크 후 발화)
   - stock-compass alert --dry-run (발화 X, 출력만)
   - 일반적으로 launchd가 호출

6. AlertManager:
   - 모든 트리거를 순회하며 check → fire
   - 중복 방지 로직 통합 (DB alerts.fired_at 기반)

테스트:
- tests/test_alerts/: 각 트리거 경계값 (=80, =79, =30, =31, delta=15, =14) 테스트
- macos_notify는 mock (subprocess.run patch)
```

✅ Commit: `feat: 3-trigger alert system with macOS notifications`

---

## Phase 6: launchd 스케줄링 + 매매 일지 (Day 7, 3시간)

### Claude Code 프롬프트

```
CLAUDE.md 11) + macOS launchd 표준:

1. launchd/com.user.stockcompass.plist 템플릿:
   - 3개 잡 또는 1개 잡 + 시간 분기 (후자 추천)
   - Label: com.user.stockcompass
   - ProgramArguments: /opt/homebrew/bin/uv run --project ~/Projects/stock-compass stock-compass batch-and-alert
   - StartCalendarInterval:
     - Weekday 1-5, Hour 16, Minute 30 (KR 배치)
     - Weekday 2-6, Hour 6, Minute 30 (US 배치, 다음날 새벽)
     - Weekday 1-5, Hour 7, Minute 0 (일일 리포트)
   - StandardOutPath, StandardErrorPath: logs/launchd-*.log

2. scripts/install_launchd.sh:
   - plist 파일을 ~/Library/LaunchAgents/ 복사
   - 프로젝트 경로를 sed로 치환 (PROJECT_PATH 변수)
   - launchctl bootstrap gui/$UID 명령 사용 (load는 deprecated)
   - 성공/실패 메시지

3. scripts/uninstall_launchd.sh:
   - launchctl bootout 후 plist 삭제

4. cli.py에 batch-and-alert 통합 명령:
   - 1) batch 실행
   - 2) 새 점수 기반 alert 체크
   - 3) 시간대에 맞는 작업 자동 선택 (KST 06~07시 → US, 16~17시 → KR, 07시 → daily)
   - 종료 코드: 0=성공, 1=일부 실패, 2=치명적 실패

5. src/stock_compass/db/repository.py에 매매 일지 추가:
   - insert_trade(ticker, side, price, qty, score_at_trade, reason) -> Trade
   - get_trades(days=30) -> list[Trade]
   - get_performance_summary() -> dict (편향 분석 — 진입 시점 점수 분포 등)

6. cli.py에 trade 명령:
   - stock-compass trade add AAPL buy --price 150 --qty 10 --reason "..."
   - stock-compass trade list
   - stock-compass trade analyze (편향 리포트)

7. README.md 작성:
   - 설치 → API 키 → launchd 등록 → 일상 사용 흐름
   - 트러블슈팅 (yfinance 실패, DART 한도 등)

테스트:
- tests/test_cli/test_batch_and_alert.py: 시간대별 분기 검증
- launchd plist는 실제 macOS에서만 테스트 가능 (CI 스킵)
```

✅ Commit: `feat: launchd scheduling + trade journal + bias report`

---

## Phase 7 (선택): Streamlit 로컬 대시보드 (Day 8+, 4시간)

```
streamlit run dashboard.py:
- 점수 히트맵 (워치리스트 전체 × 5팩터)
- 종목별 30일 점수 추이 차트 (altair)
- 본인 매매 vs 점수 산점도 (편향 시각화)
- 알림 이력 타임라인
- 모든 페이지에 면책 footer
```

✅ Commit: `feat: streamlit local dashboard`

---

## 일상 운영 흐름 (구현 완료 후)

| 시각 (KST) | 작업 | 자동/수동 |
|---|---|---|
| 06:30 | US 마감 배치 (yfinance) | launchd |
| 07:00 | 일일 종합 리포트 + Craft 노트 | launchd |
| 09:00 | iPhone 위젯에서 점수 확인 (Apple 단축어 → 로컬 DB 조회 — 별도 구현) | 수동 |
| 장중 | 매매 시: `stock-compass trade add ...` | 수동 |
| 16:30 | KR 마감 배치 | launchd |
| 주말 | `stock-compass trade analyze` 편향 리뷰 | 수동 |

---

## 트러블슈팅

| 문제 | 원인 | 해결 |
|---|---|---|
| yfinance "Too Many Requests" | 무차별 호출 | retry + 캐시 + 배치 간 sleep 추가 |
| pykrx 데이터 없음 | 휴장일 | `utils/dates.py`에서 거래일 체크 |
| Claude API 비용 폭증 | sentiment 매번 호출 | DB 캐싱 확인, 토큰 한도 조정 |
| launchd 미실행 | Mac 절전 | `pmset -g`로 wake 설정 또는 카페인 앱 |
| DART 일 한도 | 1만건 초과 | 공시는 6시간 캐시 + 신규만 조회 |

---

> Phase 0 → 1 → 2가 마일스톤. **Phase 2까지 1주 안에 안 끝나면 스코프 재검토.**
