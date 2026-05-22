# 시장 설정: KR ↔ US

> 한국·미국 전환 로직, 거래일·시간대 처리, 자동 감지.

---

## 1) 시장 자동 감지

```python
def detect_market(ticker: str) -> Literal["KR", "US"]:
    """티커 형식으로 시장 자동 감지.

    예시:
    - '005930' → KR
    - '005930.KS' → KR
    - 'AAPL' → US
    - 'BRK.B' → US

    명시 우선: CLI --market 옵션이 있으면 그 값 사용.
    """
    cleaned = ticker.upper().replace(".KS", "").replace(".KQ", "")
    if cleaned.isdigit() and len(cleaned) == 6:
        return "KR"
    if cleaned.replace(".", "").isalpha():
        return "US"
    raise ValueError(f"시장을 감지할 수 없음: {ticker}. --market 옵션으로 명시.")
```

### CLI 우선순위

1. `--market kr|us` 명시 (최우선)
2. 자동 감지
3. 감지 실패 시 에러 (`DEFAULT_MARKET`을 fallback으로 쓰지 않음 — 안전성)

---

## 2) 티커 정규화

DB에는 정규 코드만 저장, yfinance 호출 시 변환.

```python
@dataclass(frozen=True)
class TickerId:
    market: Literal["KR", "US"]
    code: str  # KR: '005930', US: 'AAPL'

    @property
    def yfinance_symbol(self) -> str:
        if self.market == "US":
            return self.code
        # KR: KOSPI=.KS, KOSDAQ=.KQ 자동 판별
        return f"{self.code}.{_kr_suffix(self.code)}"

def _kr_suffix(code: str) -> Literal["KS", "KQ"]:
    """KOSPI/KOSDAQ 자동 판별. pykrx 사용."""
    from pykrx import stock
    kospi = stock.get_market_ticker_list(market="KOSPI")
    return "KS" if code in kospi else "KQ"
```

### 캐싱

- KOSPI/KOSDAQ 종목 목록은 일 1회 갱신 (`data/cache/kr-tickers.json`)
- 신규 상장·상장폐지 반영 위해

---

## 3) 거래일 판정

### KR 거래일

```python
from pykrx.stock import get_nearest_business_day_in_a_week

def is_kr_business_day(d: date) -> bool:
    nearest = get_nearest_business_day_in_a_week(d.strftime("%Y%m%d"))
    return nearest == d.strftime("%Y%m%d")
```

### US 거래일

```python
from pandas.tseries.offsets import BDay
import pandas as pd

def is_us_business_day(d: date) -> bool:
    # NYSE/NASDAQ 휴장일 정확히 알려면 'pandas_market_calendars' 추천
    return d.weekday() < 5 and d not in US_HOLIDAYS_2025_2026
```

권장: `uv add pandas-market-calendars`

```python
import pandas_market_calendars as mcal
nyse = mcal.get_calendar("XNYS")
schedule = nyse.schedule(start_date="2025-01-01", end_date="2026-12-31")
US_BUSINESS_DAYS = set(schedule.index.date)
```

---

## 4) 시간대 (timezone)

### 원칙

- **DB 저장**: 모두 UTC ISO 8601 (`2026-05-22T14:30:00Z`)
- **사용자 표시**: Asia/Seoul (KST)
- **거래일 계산**: 각 시장 현지 시간

### 헬퍼

```python
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
NYC = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

def now_kst() -> datetime:
    return datetime.now(KST)

def to_market_local(dt: datetime, market: str) -> datetime:
    tz = KST if market == "KR" else NYC
    return dt.astimezone(tz)
```

---

## 5) 배치 실행 시각 결정

### launchd 잡 시간 (KST)

| 잡 | 시각 | 대상 | 이유 |
|---|---|---|---|
| `batch-kr` | 평일 16:30 | KR 종목 | KRX 정규장 15:30 마감 + 데이터 안정화 |
| `batch-us` | 평일 06:30 | US 종목 | NYSE 정규장 05:00 KST 마감 + 안정화 |
| `report-daily` | 평일 07:00 | 전 종목 | US 결과 종합 + Craft 발행 |

### 휴장일 처리

배치 진입 시 첫 단계:
```python
def should_run_kr_batch() -> bool:
    today = date.today()
    return is_kr_business_day(today)

# launchd가 매일 호출하지만, 휴장일이면 즉시 종료 (exit 0)
```

휴장일 강제 실행: `--force` 옵션

---

## 6) 가격 표시 단위

### KR
- 가격: 원 (정수, 콤마)
- 거래량: 주
- 시가총액: 억 원 (가독성)

### US
- 가격: $ (소수 2자리)
- 거래량: 주 (M/B 약식)
- 시가총액: $B (가독성)

```python
def format_price(value: float, market: str) -> str:
    if market == "KR":
        return f"{int(value):,}원"
    return f"${value:,.2f}"

def format_market_cap(value: float, market: str) -> str:
    if market == "KR":
        return f"{value / 1e8:,.0f}억원"
    if value > 1e9:
        return f"${value / 1e9:,.1f}B"
    return f"${value / 1e6:,.1f}M"
```

---

## 7) 워치리스트 형식

`.env.local`:
```bash
WATCHLIST_KR=005930,035720,000660,035420,005380
WATCHLIST_US=AAPL,MSFT,NVDA,GOOGL,TSLA
```

파싱:
```python
def parse_watchlist(env_value: str, market: str) -> list[TickerId]:
    codes = [c.strip() for c in env_value.split(",") if c.strip()]
    return [TickerId(market=market, code=c) for c in codes]
```

### 확장 (Phase 5+)

- DB `watchlists` 테이블 추가 → 그룹별 워치리스트 ("코어", "관찰", "테마")
- CLI: `stock-compass watch add AAPL --group core`

---

## 8) 환율 (참고용)

배치 시 USD/KRW 환율 1회 조회:
```python
import yfinance as yf
fx = yf.Ticker("KRW=X").history(period="1d")["Close"].iloc[-1]
```

종합 리포트에 KR 종목과 US 종목을 한 화면에 보여줄 때 KRW 환산 시가총액 사용.

---

## 9) 시장별 팩터 가중치 분기 (선택)

미국 vs 한국은 데이터 가용성이 다르므로 가중치 조정 가능:

```python
WEIGHTS = {
    "US": {"valuation": 0.30, "fundamentals": 0.25, "technical": 0.20, "macro": 0.15, "sentiment": 0.10},
    "KR": {"valuation": 0.25, "fundamentals": 0.20, "technical": 0.25, "macro": 0.15, "sentiment": 0.15},
    # KR은 sentiment가 단기 영향 큼, technical(외인 수급) 가산
}
```

기본값은 동일하게 시작, Phase 6 이후 백테스트로 조정 권장 (단 모든 조정은 본인 사용 한정).
