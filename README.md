# stock-compass 🧭

> 개인 매매 의사결정 보조 도구 — 한국·미국 다요인 점수화 대시보드
> macOS 로컬 실행, 외부 배포 없음.

---

## ⚠️ 면책

이 도구는 **미래 예측기가 아닙니다.** 단지 펀더멘털·기술적·거시·심리 지표를 정량 종합하여 본인의 의사결정을 보조하는 도구입니다.

- 출력은 "관심권 / 중립 / 주의" 등 정보 요약일 뿐, 매수·매도 명령이 아님
- 모든 판단은 사용자 본인의 책임
- **외부 배포·판매 금지** (한국 자본시장법상 유사투자자문업 위반 가능)
- 뉴스·공시 요약은 AI 생성으로, 원문 확인 필수

---

## 핵심 기능

| 기능 | 설명 |
|---|---|
| 다요인 점수화 | Valuation 30% + Fundamentals 25% + Technical 20% + Macro 15% + Sentiment 10% |
| KR/US 자동 전환 | 6자리 숫자 → KR, 알파벳 → US (또는 `--market` 명시) |
| 일일 배치 | launchd 자동 실행 → SQLite 저장 → 히스토리 추적 |
| Craft 노트 자동 생성 | 매일 종목별 카드 + 본인 매매 일지 슬롯 |
| 3종 알림 | 임계치 진입 · 점수 급변 · 일일 리포트 (macOS Notification) |
| 매매 편향 분석 | 본인 매매 시점 점수 분포 → 충동 매매 탐지 |

---

## 빠른 시작

### 1) 사전 준비

```bash
# Homebrew + uv 설치
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install uv
```

### 2) 프로젝트 셋업

```bash
git clone <repo> stock-compass && cd stock-compass
chmod +x scripts/*.sh
./scripts/setup.sh
```

### 3) API 키 발급 + 입력

`.env.local` 편집 — `.env.example` 참조:

- `ANTHROPIC_API_KEY` (https://console.anthropic.com)
- `FRED_API_KEY` (https://fred.stlouisfed.org/docs/api/api_key.html)
- `DART_API_KEY` (https://opendart.fss.or.kr, KR 사용 시)
- `WATCHLIST_KR`, `WATCHLIST_US` 본인 종목

### 4) Claude Code로 구현

```
프롬프트:
"CLAUDE.md를 읽고 docs/IMPLEMENTATION_GUIDE.md의 Phase 0부터 시작해줘"
```

Phase 0 → 1 → 2 → ... → 6 순서로 진행. 각 Phase 끝나면 git commit.

### 5) 일일 자동 실행 등록

```bash
./scripts/install_launchd.sh
```

- 평일 06:30 KST: 미국 시장 배치
- 평일 07:00 KST: 일일 리포트 + Craft 노트 생성
- 평일 16:30 KST: 한국 시장 배치

---

## 일상 사용

```bash
# 단일 종목 점수
uv run stock-compass score AAPL
uv run stock-compass score 005930

# 워치리스트 배치
uv run stock-compass batch
uv run stock-compass batch --market kr

# 30일 히스토리
uv run stock-compass history 005930 --days 30

# 매매 기록 (편향 분석용)
uv run stock-compass trade add AAPL buy --price 215.40 --qty 5 --reason "분할 1차"
uv run stock-compass trade analyze

# Craft 노트 생성
uv run stock-compass report

# 뉴스 요약 (단독)
uv run stock-compass news 005930 --days 7
```

---

## 프로젝트 구조

```
stock-compass/
├── CLAUDE.md                          # Claude Code 컨텍스트
├── pyproject.toml                     # uv·ruff·mypy 설정
├── docs/
│   ├── IMPLEMENTATION_GUIDE.md        # Phase 0~6 가이드
│   ├── DATA_SOURCES.md                # 외부 API 명세
│   ├── DB_SCHEMA.md                   # SQLite 스키마
│   ├── MARKET_CONFIG.md               # KR/US 전환 로직
│   └── CRAFT_TEMPLATE.md              # Craft 노트 디자인
├── src/stock_compass/
│   ├── cli.py                         # typer 진입점
│   ├── markets/                       # 시장 어댑터 (KR/US)
│   ├── factors/                       # 5대 팩터
│   ├── scoring/                       # 종합 점수 엔진
│   ├── alerts/                        # 3종 트리거
│   ├── output/                        # Craft·터미널·macOS 알림
│   ├── llm/                           # Claude 요약
│   └── db/                            # SQLite
├── scripts/
│   ├── setup.sh                       # 초기 셋업
│   ├── install_launchd.sh             # 자동 실행 등록
│   └── uninstall_launchd.sh
└── launchd/
    └── com.user.stockcompass.plist    # 스케줄 템플릿
```

---

## 트러블슈팅

| 문제 | 해결 |
|---|---|
| yfinance "Too Many Requests" | `YFINANCE_THROTTLE_SEC=1.0` 늘리기 |
| pykrx 데이터 없음 | 휴장일 — 거래일 체크 자동 (코드 동작) |
| DART 일 한도 초과 | 캐시 6시간 자동 — 종목 수 줄이기 |
| Claude API 비용 폭증 | `ANTHROPIC_DAILY_INPUT_LIMIT` 조정 |
| launchd 미실행 | Mac 절전 — 카페인 앱 또는 `pmset wake` |
| `mypy` 외부 모듈 에러 | `pyproject.toml`의 overrides 확인 |

---

## 라이선스

**개인 사용 한정. 재배포·판매 금지.**

특히 한국 거주자는 자본시장법 시행령에 따라 본 도구의 출력을 타인에게 제공·판매할 경우 유사투자자문업 위반 (1년 이하 징역 또는 5천만원 이하 벌금) 가능성이 있음.
