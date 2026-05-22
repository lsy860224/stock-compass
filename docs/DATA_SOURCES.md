# 데이터 소스: stock-compass

> 외부 API·라이브러리 사용 명세. 자체 API 서버는 없음 (로컬 도구).

---

## 데이터 소스 매트릭스

| 소스 | 시장 | 데이터 | 인증 | 한도 | 안정성 |
|---|---|---|---|---|---|
| **yfinance** | KR + US | OHLCV, 펀더멘털, 뉴스 | 없음 | 비공식 (느슨한 IP) | ⚠️ 불안정 |
| **pykrx** | KR | OHLCV, 외인/기관 수급 | 없음 | KRX 공개 데이터 | ✅ 안정 |
| **FRED** | 거시 (US) | 10Y 금리, VIX, DXY | API Key | 무료 무제한 | ✅ 안정 |
| **OpenDART** | KR | 공시 (실적·내부자·공시) | API Key | **일 10,000건** | ✅ 안정 |
| **Naver 뉴스** | KR | 뉴스 검색 | API Key | 일 25,000건 | ✅ 안정 |
| **Anthropic Claude** | LLM | 뉴스·공시 요약 | API Key | 종량제 | ✅ 안정 |

---

## 1. yfinance (US + KR 통합)

### 설치
```bash
uv add yfinance
```

### KR 종목 매핑

| 코드 | yfinance 심볼 |
|---|---|
| 005930 (삼성전자, KOSPI) | `005930.KS` |
| 035720 (카카오, KOSPI) | `035720.KS` |
| 091990 (셀트리온헬스케어, KOSDAQ) | `091990.KQ` |

자동 매핑 함수 (`markets/kr.py`):
```python
def to_yfinance_symbol(code: str) -> str:
    """6자리 KR 코드 → yfinance 심볼.
    KOSPI/KOSDAQ 구분은 pykrx로 조회."""
    from pykrx import stock
    market = stock.get_market_ticker_name(code)  # 또는 별도 매핑
    suffix = ".KS" if is_kospi(code) else ".KQ"
    return f"{code}{suffix}"
```

### 호출 예시

```python
import yfinance as yf
t = yf.Ticker("AAPL")
hist = t.history(period="2y", auto_adjust=False)  # adj_close 보존
info = t.info  # PER, PBR, ROE 등
news = t.news  # 최신 뉴스 (불안정)
```

### 안정성 대책

- **재시도**: tenacity로 3회, exponential backoff
- **캐시**: `data/cache/{symbol}-{YYYYMMDD}.parquet` (24시간)
- **fallback**: KR은 pykrx, US는 캐시 → 그래도 실패 시 점수 50 (중립) + 경고 로그
- **rate limit**: 워치리스트 배치 시 종목 간 sleep(0.5)

### 알려진 이슈

- `t.info` 키 누락 빈번 — 항상 `.get(key, default)`
- 한국 종목 `info`에 비어있는 필드 많음 — pykrx로 보완
- `auto_adjust=True` 기본값 변경됨 (yfinance ≥ 0.2.40) — 명시적 지정

---

## 2. pykrx (한국 보조)

### 설치
```bash
uv add pykrx
```

### 주요 함수

```python
from pykrx import stock, bond
from datetime import date

# 가격 (yfinance 백업)
df = stock.get_market_ohlcv_by_date("20240101", "20240522", "005930")

# 외인/기관 수급 (한국 특화)
flows = stock.get_market_trading_value_by_investor("20240101", "20240522", "005930")

# 시가총액, PER, PBR
fund = stock.get_market_fundamental("20240522", "005930")
```

### 사용 영역

- **Fundamentals 보조**: PER/PBR (yfinance가 한국 종목 누락 시)
- **Technical 보조**: 외인/기관 5일 누적 매수 → Technical 팩터 가산점
- **거래일 판정**: `stock.get_nearest_business_day_in_a_week("20240522")`

### 주의

- 휴장일 호출 → 빈 DataFrame (사전 체크)
- KRX 공시 페이지를 스크래핑하는 함수는 robots.txt 준수
- 분당 호출 무제한이지만 매너 차원에서 sleep 권장

---

## 3. FRED (미국 거시)

### 가입
- https://fred.stlouisfed.org/docs/api/api_key.html
- 무료, 즉시 발급

### 설치
```bash
uv add fredapi
```

### 사용 시리즈

| 시리즈 ID | 의미 | Macro 팩터 영향 |
|---|---|---|
| `DGS10` | 미국 10년 국채 금리 | 상승 → Risk-off (점수 하락) |
| `VIXCLS` | VIX 변동성 지수 | 상승 → Risk-off (점수 하락) |
| `DTWEXBGS` | 달러 인덱스 (DXY 대체) | 상승 → KR 종목 점수 하락 |
| `T10Y2Y` | 장단기 스프레드 (역전 시 경기침체 신호) | 음수 → Risk-off |

### 호출 예시

```python
from fredapi import Fred
fred = Fred(api_key=settings.fred_api_key)
dgs10 = fred.get_series("DGS10", limit=30)  # 최근 30영업일
vix = fred.get_series_latest_release("VIXCLS")
```

### Macro 팩터 점수화 (예시)

```python
def calculate_macro_score(fred: Fred) -> float:
    vix = fred.get_series("VIXCLS").iloc[-1]
    spread = fred.get_series("T10Y2Y").iloc[-1]
    # VIX 15 이하 = 100점, 35 이상 = 0점
    vix_s = max(0, min(100, (35 - vix) / 20 * 100))
    # 스프레드 양수 = 100점, -1.0 = 0점
    spread_s = max(0, min(100, (spread + 1.0) / 1.0 * 100))
    return 0.6 * vix_s + 0.4 * spread_s
```

### 캐시

- FRED 일 무제한이지만 6시간 캐시 (`data/cache/fred-{series}-{date}.json`)

---

## 4. OpenDART (한국 공시)

### 가입
- https://opendart.fss.or.kr
- 무료, 신청 후 1영업일 이내 발급
- ⚠️ **일 10,000건 한도** — 캐시 필수

### 설치

```bash
uv add opendartreader  # 비공식 wrapper, 더 편함
# 또는 requests로 직접
```

### 사용 예시

```python
import OpenDartReader
dart = OpenDartReader(settings.dart_api_key)

# 종목별 공시 목록 (최근 30일)
reports = dart.list("005930", start="2024-04-22", end="2024-05-22")

# 주요 공시만 (정기보고서·내부자 거래·유상증자 등)
key_codes = ["A001", "A002", "A003", "B001", "C001"]  # DART 공시유형 코드
filtered = reports[reports["report_code"].isin(key_codes)]

# 공시 본문 (요약용)
doc = dart.document("20240522000123")  # rcept_no
```

### 캐시 정책

- 종목별 공시 목록: 6시간 캐시
- 공시 본문: 영구 캐시 (한 번 조회한 rcept_no는 재호출 X)

### 주의

- 한국 종목 코드는 6자리 (앞에 0 보존: "005930", "035720")
- 일 한도 초과 시 1일 동안 해당 키 차단

---

## 5. Naver 뉴스 (선택, 한국 종목용)

### 가입
- https://developers.naver.com/apps
- 무료, Client ID + Secret 발급
- 일 25,000건

### 호출

```python
import httpx

async def search_news(query: str, display: int = 10) -> list[dict]:
    headers = {
        "X-Naver-Client-Id": settings.naver_client_id,
        "X-Naver-Client-Secret": settings.naver_client_secret,
    }
    params = {"query": query, "display": display, "sort": "date"}
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers=headers, params=params, timeout=10
        )
        return r.json()["items"]
```

### 주의

- 검색 query는 종목명 + "주가" 같은 키워드 조합 (그냥 종목명만 하면 무관한 결과 다수)
- 응답에 HTML 태그 포함 — BeautifulSoup이나 정규식으로 제거
- description은 짧은 미리보기 — 본문 가져오려면 link로 fetch 별도

---

## 6. Anthropic Claude (뉴스·공시 요약)

### 가입
- https://console.anthropic.com
- 종량제 (선결제 크레딧 또는 후불)

### 설치
```bash
uv add anthropic
```

### 모델 선택

| 모델 | 입력 $/1M | 출력 $/1M | 용도 |
|---|---|---|---|
| `claude-haiku-4-5-20251001` | 저렴 | 저렴 | **권장** — 뉴스 요약 |
| `claude-sonnet-4-6` | 중간 | 중간 | 복잡한 공시 본문 요약 |
| `claude-opus-4-7` | 비쌈 | 비쌈 | ⛔ 이 용도엔 과함 |

권장: **Haiku 기본**, 공시 본문 1만자 초과 시만 Sonnet.

### 시스템 프롬프트 (요약용)

```
당신은 금융 뉴스 분석가다. 다음 원칙 엄수:

1. 객관적 사실만 추출. 추측·예측 금지.
2. 원문 30단어 이상 그대로 인용 금지 (저작권).
3. 자체 표현으로 3줄 요약.
4. 톤 점수 -10(매우 부정) ~ +10(매우 긍정) 부여.
   - +10: 어닝 서프라이즈, 대형 호재
   - 0: 중립적 사실 보고
   - -10: 부도, 회계 부정, 상장폐지 위기
5. 핵심 키워드 5개 추출 (회사명·금액·일자 등).
6. JSON 형식만 출력, 다른 텍스트 X.

출력 형식:
{
  "summary": "3줄 요약",
  "tone_score": 0.0,
  "keywords": ["키워드1", "키워드2", ...]
}
```

### 호출 예시

```python
from anthropic import Anthropic

client = Anthropic(api_key=settings.anthropic_api_key)

def summarize(article_text: str) -> dict:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": article_text[:8000]}]  # 8K char 제한
    )
    text = response.content[0].text
    return json.loads(text)
```

### 비용 관리

- 일 토큰 한도 (config): 기본 500K input + 100K output
- 한도 초과 시: sentiment 팩터 50점 처리 + 경고 로그
- 토큰 사용량 DB `news_summaries.tokens_used` 누적 추적
- 캐시: 동일 `source_url`은 재요약 X

---

## 시장별 데이터 조합 매트릭스

### 한국 종목 (예: 005930 삼성전자)

| 팩터 | 1차 소스 | 2차 (fallback) |
|---|---|---|
| Valuation (PER/PBR) | pykrx `get_market_fundamental` | yfinance `info` |
| Fundamentals (매출/ROE) | yfinance `info` | DART 정기보고서 파싱 |
| Technical | yfinance OHLCV | pykrx `get_market_ohlcv_by_date` |
| Macro | FRED (글로벌) | — |
| Sentiment | Naver 뉴스 + DART 공시 → Claude | (없으면 50) |

### 미국 종목 (예: AAPL)

| 팩터 | 1차 소스 | 2차 |
|---|---|---|
| Valuation | yfinance `info` | (없음) |
| Fundamentals | yfinance `info` | (없음) |
| Technical | yfinance OHLCV | (없음) |
| Macro | FRED | — |
| Sentiment | yfinance `news` → Claude | (없으면 50) |

---

## API 키 발급 체크리스트

```
☐ Anthropic API Key (console.anthropic.com)
☐ FRED API Key (fred.stlouisfed.org)
☐ DART API Key (opendart.fss.or.kr, KR 사용 시)
☐ Naver Client ID/Secret (선택, KR 뉴스 강화 시)
☐ .env.local에 입력 + 깃 제외 확인
☐ scripts/setup.sh로 자동 검증 통과
```
