# CLAUDE.md

> Claude Code가 자동으로 읽어 모든 응답에 반영하는 프로젝트 컨텍스트.
> 큰 변경 시 `docs/IMPLEMENTATION_GUIDE.md`도 함께 업데이트.

---

## 1) 프로젝트 개요

- **제품명**: stock-compass
- **한 줄 설명**: 개인 매매 의사결정 보조용 다요인 점수화 대시보드 (macOS 로컬 도구)
- **타겟 사용자**: 개인 투자자 1인 (본인). 외부 배포·판매 없음
- **핵심 기능 (MVP)**:
  1. 한국·미국 종목 다요인 점수 계산 (Valuation·Fundamentals·Technical·Macro·Sentiment)
  2. 워치리스트 일일 배치 → SQLite 저장 → 히스토리 추적
  3. 뉴스·공시 자동 요약 (Claude API)
  4. 3종 알림 트리거 (점수 임계치 · 점수 급변 · 일일 리포트)
  5. Craft 일일 노트 자동 생성

### ⚠️ 절대 원칙 (Claude Code는 이 원칙을 모든 코드·UI·문서에 반영)

- **이 도구는 미래 예측기가 아니다.** "BUY/SELL 신호" 같은 명령형 출력 금지.
- **출력은 항상 "정보 요약 + 정량 점수"**, 의사결정은 사용자가 한다.
- **금지 표현**: "매수 추천", "급등 예상", "저점/고점 확정", "100% 보장", "AI가 예측한"
- **허용 표현**: "현재 점수 X점", "팩터 분해", "과거 동일 점수 시점 N개", "참고 자료"
- **법적 회피**: 모든 출력에 면책 문구 자동 삽입 — "투자 자문 아님. 본인 판단의 보조 자료."
- **외부 배포 금지**: 코드·점수·노트를 타인에게 제공·판매 시 유사투자자문업 위반 가능

## 2) 기술 스택

- **언어**: Python 3.12+ (type hints 필수)
- **패키지 관리**: `uv` (`brew install uv`) — pip보다 10~100배 빠름
- **데이터**:
  - `yfinance` — 미국 + 한국(`.KS`/`.KQ` 접미사) 통합
  - `pykrx` — 한국 시장 보조 (수급·외인/기관)
  - `fredapi` — 미국 거시 (10Y 금리·VIX·환율)
  - `OpenDartReader` 또는 직접 REST — DART 한국 공시
- **AI**: `anthropic` SDK — 뉴스/공시 요약만 (서버 사이드 호출 없음, 로컬 실행)
- **저장**: SQLite (stdlib `sqlite3` + `sqlmodel` 선택) — 단일 사용자라 PostgreSQL 불필요
- **CLI/UI**: `typer` (CLI) + `rich` (터미널 출력) + 선택적 `streamlit` (로컬 대시보드)
- **스케줄러**: macOS `launchd` (`cron` 사용 금지 — macOS는 launchd가 정식)
- **출력 통합**: Craft (Markdown 파일 자동 생성 → Craft 임포트, 향후 MCP 직접 연동)
- **테스트**: `pytest` + `pytest-cov`
- **품질**: `ruff` (린트 + 포맷) + `mypy --strict`

## 3) 코딩 컨벤션

### 언어·도구

- Python 3.12+ (PEP 695 type alias 적극 사용)
- 모든 함수에 type hints — `mypy --strict` 통과 필수
- 포맷: `ruff format` (Black 호환)
- 린트: `ruff check` (E·F·I·N·UP·B·A·C4·SIM·PL 룰셋)
- import 순서: stdlib → 외부 → 내부 (ruff 자동)

### 네이밍

- **모듈/파일**: `snake_case.py` (analyzer.py, market_kr.py)
- **클래스**: `PascalCase` (StockAnalyzer, MarketAdapter)
- **함수/변수**: `snake_case` (calculate_score, ticker_list)
- **상수**: `UPPER_SNAKE_CASE` (MAX_WORKERS, DEFAULT_PERIOD)
- **사설(private)**: `_leading_underscore`
- **타입 별칭**: `PascalCase` (type Ticker = str)
- **DB 테이블**: `snake_case` 복수형 (tickers, snapshots, alerts)

### 코드 스타일

- 함수 길이: 50줄 이내
- 파일 길이: 300줄 이내 (예외 OK, 단 분할 고려)
- docstring: Google style — 공개 함수·클래스만 (사설은 생략 가능)
- 들여쓰기: 4 spaces (PEP 8)
- 라인 길이: 100자 (ruff 기본 88보다 약간 여유)

### 데이터 모델

- 도메인 객체: `pydantic.BaseModel` (`v2`) 또는 `dataclass` (frozen=True)
- DB 모델: `sqlmodel` (선택) 또는 raw SQL + dict
- 외부 API 응답: 즉시 도메인 모델로 변환 (raw dict 유포 금지)

### 에러 처리

- 외부 API 호출: `try/except` + 재시도 (tenacity) + 타임아웃 10초
- yfinance는 불안정 — 항상 fallback 데이터 소스 고려
- 로깅: `logging` 모듈 + `rich.logging.RichHandler` (터미널 색상)
- 사용자 친화 에러 메시지 + 상세 로그는 파일로 분리

## 4) 파일 구조

```
stock-compass/
├── CLAUDE.md                          # 이 파일
├── pyproject.toml                     # uv·ruff·mypy 설정
├── .env.example                       # 환경변수 템플릿
├── .env.local                         # 실제 키 (gitignore)
├── .gitignore
├── README.md
├── data/                              # SQLite + 캐시 (gitignore)
│   ├── stock_compass.db
│   └── cache/
├── logs/                              # 실행 로그 (gitignore)
├── src/
│   └── stock_compass/
│       ├── __init__.py
│       ├── cli.py                     # typer 진입점 (메인 CLI)
│       ├── config.py                  # 환경변수·상수
│       ├── markets/                   # 시장별 어댑터
│       │   ├── __init__.py
│       │   ├── base.py                # MarketAdapter 추상 클래스
│       │   ├── us.py                  # 미국 (yfinance)
│       │   └── kr.py                  # 한국 (yfinance + pykrx + DART)
│       ├── factors/                   # 5대 팩터 계산
│       │   ├── __init__.py
│       │   ├── valuation.py
│       │   ├── fundamentals.py
│       │   ├── technical.py
│       │   ├── macro.py
│       │   └── sentiment.py
│       ├── scoring/                   # 종합 점수 엔진
│       │   ├── __init__.py
│       │   └── engine.py
│       ├── alerts/                    # 알림 시스템
│       │   ├── __init__.py
│       │   ├── threshold.py           # 점수 임계치
│       │   ├── delta.py               # 점수 급변
│       │   └── daily.py               # 일일 리포트
│       ├── output/                    # 출력 어댑터
│       │   ├── __init__.py
│       │   ├── craft.py               # Craft Markdown 노트
│       │   ├── terminal.py            # rich 콘솔
│       │   └── notify.py              # macOS osascript 알림
│       ├── llm/                       # Claude API 래퍼
│       │   ├── __init__.py
│       │   └── summarizer.py          # 뉴스·공시 요약
│       ├── db/                        # SQLite 추상화
│       │   ├── __init__.py
│       │   ├── schema.py              # 테이블 정의
│       │   ├── migrations.py
│       │   └── repository.py
│       └── utils/
│           ├── __init__.py
│           ├── retry.py               # tenacity 래퍼
│           ├── dates.py               # 거래일·시간대
│           └── logging.py
├── scripts/
│   ├── setup.sh                       # 초기 셋업 (macOS)
│   ├── install_launchd.sh             # launchd 등록
│   └── uninstall_launchd.sh
├── launchd/
│   └── com.user.stockcompass.plist    # launchd 설정 템플릿
├── docs/
│   ├── IMPLEMENTATION_GUIDE.md
│   ├── DATA_SOURCES.md
│   ├── DB_SCHEMA.md
│   ├── MARKET_CONFIG.md
│   └── CRAFT_TEMPLATE.md
└── tests/
    ├── conftest.py
    ├── test_factors/
    ├── test_markets/
    └── test_scoring/
```

### 경로 규칙

- 시장 어댑터: `src/stock_compass/markets/[market].py` (us, kr)
- 팩터: `src/stock_compass/factors/[factor_name].py`
- 새 알림 종류: `src/stock_compass/alerts/[trigger_name].py`
- 출력 형식: `src/stock_compass/output/[target].py`

## 5) 환경변수

상세는 `.env.example` 참조. 모든 키는 **로컬 전용**, 깃 커밋 금지.

| 변수 | 필수 | 용도 | 발급처 |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | 뉴스·공시 요약 | console.anthropic.com |
| `FRED_API_KEY` | ✅ | 미국 거시 데이터 | fred.stlouisfed.org/docs/api/api_key.html |
| `DART_API_KEY` | ✅ (KR 사용 시) | 한국 공시 | opendart.fss.or.kr |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | ⛔ 선택 | 한국 뉴스 | developers.naver.com |
| `CRAFT_API_TOKEN` | ⛔ 선택 (Phase 3+) | Craft 직접 발행 | Craft → Settings → API |
| `DEFAULT_MARKET` | ✅ | KR 또는 US (기본 시장) | 본인 설정 |
| `WATCHLIST_KR` | ✅ | 한국 종목 코드 콤마 구분 (예: `005930,035720`) | 본인 |
| `WATCHLIST_US` | ✅ | 미국 티커 콤마 구분 (예: `AAPL,MSFT,NVDA`) | 본인 |

## 6) 데이터베이스 (SQLite)

상세 스키마: `docs/DB_SCHEMA.md`

### 핵심 테이블

- `tickers` — 종목 마스터 (market, code, name, sector)
- `snapshots` — 일별 가격·거래량 캐시 (티커, 날짜, OHLCV)
- `factor_scores` — 일별 팩터 점수 (티커, 날짜, factor_name, score, raw_value)
- `composite_scores` — 일별 종합 점수 (티커, 날짜, total_score, verdict)
- `news_summaries` — Claude 요약 결과 (티커, 발행일, source, summary, tone)
- `alerts` — 발화된 알림 이력 (티커, trigger_type, score_before, score_after, fired_at)
- `trades` — 본인 매매 일지 (티커, 방향, 가격, 수량, 점수_at_trade, 사유)

### 위치·관리

- 파일: `data/stock_compass.db`
- 백업: 매일 자동 `data/backups/stock_compass-YYYYMMDD.db` (Phase 5)
- 마이그레이션: `src/stock_compass/db/migrations.py` (단순 버전 테이블 + 순차 실행)

## 7) 시장 전환 (KR ↔ US)

상세 로직: `docs/MARKET_CONFIG.md`

### 추상화

- `MarketAdapter` 추상 클래스 (`markets/base.py`):
  - `get_price_history(ticker, period) -> DataFrame`
  - `get_fundamentals(ticker) -> dict`
  - `get_news(ticker, days) -> list[News]`
  - `get_disclosures(ticker, days) -> list[Disclosure]` (KR만 의미 있음)
  - `get_trading_hours() -> tuple[time, time]`
  - `get_currency() -> Literal["KRW", "USD"]`

### 시장 자동 감지

- 티커가 숫자 6자리 또는 `.KS`/`.KQ` 접미사 → KR
- 티커가 알파벳 → US
- 명시적 지정: `--market kr` / `--market us`

### 시간대 처리

- 모든 DB 저장은 UTC
- 표시는 `Asia/Seoul` (사용자 환경)
- 배치 실행 시각:
  - KR: 16:00 KST (장 마감 +30분)
  - US: 06:00 KST (장 마감 +1.5시간, 야간 처리)

## 8) 점수 엔진

### 5대 팩터 + 가중치 (기본값, config로 조정 가능)

| 팩터 | 가중치 | 지표 |
|---|---|---|
| Valuation | 30% | PER, PBR, PEG (업종 중앙값 대비) |
| Fundamentals | 25% | 매출 YoY, 영업이익 YoY, ROE, FCF 마진 |
| Technical | 20% | RSI(14), 200MA 이격률, 거래량 z-score |
| Macro | 15% | 미국 10Y 금리·VIX·DXY (Risk-on/off) |
| Sentiment | 10% | 최근 30일 뉴스·공시 톤 (Claude 요약 점수) |

### 출력 규칙

- 각 팩터: 0~100 정규화 (높을수록 매수 우호적)
- 종합: 가중 평균 → 0~100
- 등급: `≥70` 관심권 / `50~69` 중립 / `<50` 주의
- **"BUY/SELL" 절대 X** — "관심권 / 중립 / 주의"만 허용

## 9) 알림 시스템 (3종 동시 운영)

| 트리거 | 발화 조건 | 채널 |
|---|---|---|
| **임계치** | 종합 점수 ≥80 또는 ≤30 진입 시 | macOS Notification + Craft 노트 |
| **급변** | 24시간 내 점수 변화 ≥15 | macOS Notification |
| **일일** | 매일 정해진 시각 실행 | Craft 노트 + 터미널 출력 |

### macOS 알림

`output/notify.py`에서 `osascript`로 네이티브 알림:
```python
subprocess.run([
    "osascript", "-e",
    f'display notification "{message}" with title "Stock Compass"'
])
```

### 중복 방지

- 동일 티커·동일 트리거 24시간 내 재발화 X (DB `alerts` 테이블 체크)

## 10) Craft 통합

### Phase 3 (단순): Markdown 파일 생성

- 매일 16:00 KST에 `data/craft_export/YYYY-MM-DD.md` 생성
- 사용자가 Craft에 수동 임포트 (또는 Folder Watch)

### Phase 5+ (고도): Craft API 직접 발행

- `CRAFT_API_TOKEN` 환경변수
- `output/craft.py`에서 REST 호출 → 일일 노트 자동 생성

### 노트 템플릿

`docs/CRAFT_TEMPLATE.md` 참조 — 종목별 카드 + 일일 요약 헤더 + 본인 매매 일지 섹션

## 11) 스케줄링 (launchd)

### plist 위치

- 템플릿: `launchd/com.user.stockcompass.plist`
- 설치 위치: `~/Library/LaunchAgents/com.user.stockcompass.plist`

### 설치

```bash
./scripts/install_launchd.sh
# 내부적으로 launchctl load 실행
```

### 스케줄

- 한국 시장 배치: 평일 16:30 KST
- 미국 시장 배치: 평일 06:30 KST
- 일일 종합 리포트: 평일 07:00 KST (전일 결과 종합)

### 실행 로그

`logs/launchd-stdout.log`, `logs/launchd-stderr.log`

## 12) 주의사항 (보안·법률·운영)

### 보안

- ❌ `.env.local` 절대 커밋 X (`.gitignore` 확인)
- ❌ API 키를 코드에 하드코딩 X (`os.environ.get` 만)
- ❌ DB 파일을 클라우드 자동 동기화 X (iCloud Drive 등에서 제외)
- ✅ 백업은 별도 암호화 폴더 또는 외장 디스크

### 법률 (한국)

- **자본시장법**: 본인 사용 OK. **타인에게 결과 제공·판매 시 유사투자자문업 위반 가능** (1년 이하 징역 또는 5천만원 이하 벌금).
- **저작권**: 뉴스 요약 시 원문 30단어 이상 인용 금지 (cc-kickstart 기본 원칙 준수).
- 모든 출력에 면책 자동 삽입.

### 운영

- yfinance는 비공식 API — 깨질 수 있음. fallback 경로 고려.
- DART OpenAPI는 일 1만건 제한 — 캐시 필수.
- Claude API는 비용 발생 — 뉴스 요약은 캐싱 (`news_summaries.summary` 재사용).
- Mac 절전 모드에서 launchd 미동작 — `pmset` 설정 또는 카페인 앱 권장.

### 외부 API 실패 시

- yfinance 실패 → pykrx fallback (KR만) 또는 24h 캐시 활용
- FRED 실패 → 마지막 캐시값 사용 + 로그 경고
- Claude API 실패 → Sentiment 점수 50 (중립)으로 처리 + 로그
- DART 실패 → 공시 섹션 생략

## 13) 테스트

### 도구

- `pytest` + `pytest-cov` + `pytest-mock`
- `vcrpy` 또는 `respx` (외부 API mock)

### 정책

- 팩터 계산 함수: 100% 커버
- 시장 어댑터: mock으로 핵심 경로 테스트
- 알림 발화 조건: 경계값 (≥80, ≤30, =15) 테스트
- E2E: 단일 종목 → 점수 → DB 저장 → Craft MD 출력 (1개 통합 테스트)

### 실행

```bash
uv run pytest                 # 전체
uv run pytest -k factors      # 팩터만
uv run pytest --cov=src       # 커버리지
```

## 14) Claude Code 작업 시 추가 원칙

- 새 종목 데이터 소스 추가 시 → `MarketAdapter` 인터페이스 준수
- 새 팩터 추가 시 → `factors/[name].py` + `engine.py`에 등록 + 가중치 재배분
- 새 알림 종류 추가 시 → `alerts/[name].py` + DB `alerts.trigger_type` enum 확장
- 모든 외부 API 호출은 retry 데코레이터 적용 (`utils/retry.py`)
- 새 코드 작성 후 반드시 실행: `uv run ruff check . && uv run mypy src/`

---

> 이 파일을 변경하면 Claude Code 다음 세션부터 적용됩니다.
> 큰 구조 변경 시 `docs/IMPLEMENTATION_GUIDE.md`도 함께 업데이트하세요.
