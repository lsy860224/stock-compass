# 하이브리드 Sentiment 모드

> 워치리스트는 자동 API, 스크리너·심층 분석은 수동 프롬프트.
> Claude Pro 구독($20/월) 정액 한도 활용으로 가변 비용 차단.

---

## 1) 모드 비교

| 모드 | 동작 | 월 예상 비용 | 적합 상황 |
|---|---|---|---|
| `api` | 모든 sentiment 자동 API | 10종목 $4 / 50종목 $20 | 소수 종목 매일 |
| `prompt` | 모든 sentiment 수동 (Claude.ai 복붙) | API $0 + Pro $20 | 대량 분석, API 키 없음 |
| **`hybrid`** | 워치리스트 API + 스크리너/대량 prompt | 10종목 API $4 + Pro $20 | **현재 선택** |

---

## 2) 자동 경로 (API) — 워치리스트

기존 Phase 4 동작 그대로:

```bash
uv run stock-compass batch  # 워치리스트 일일 점수
# → 종목당 5건 뉴스 → Anthropic API 호출 → DB 저장
```

비용 통제:
- `ANTHROPIC_DAILY_INPUT_LIMIT` 환경변수
- 한도 초과 시 sentiment 50점 + 경고 로그
- 캐싱: 동일 `source_url` 재요약 X

---

## 3) 수동 경로 (Prompt) — 스크리너 / 대량 분석

### 3.1 흐름

```
┌─────────────────────┐
│ 스크리너 실행       │
│ (또는 명시 분석)    │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 뉴스/공시 수집      │ ← 데이터 소스 그대로 (yfinance/DART/Naver)
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Prompt 파일 생성    │ → data/prompts/YYYYMMDD-deepdive.md
└──────────┬──────────┘
           │
           ▼
    [사용자 작업]
   Claude.ai에 복붙
   응답 받음
           │
           ▼
┌─────────────────────┐
│ 응답 import         │ → stock-compass sentiment import <file>
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ DB 저장 (api와 동일)│
└─────────────────────┘
```

### 3.2 CLI

```bash
# Prompt 파일 생성
uv run stock-compass sentiment prompt --tickers 005930,035720,000660 --days 7
# → data/prompts/2026-05-22-sentiment-3tickers.md

# 스크리너 결과 종목에 대해 자동 생성
uv run stock-compass screen --preset value_growth_kr --prompt-deepdive
# → data/prompts/2026-05-22-deepdive-value_growth_kr.md

# 응답 import
uv run stock-compass sentiment import \
  --file ~/Downloads/claude_response.txt \
  --batch-id 2026-05-22-deepdive-value_growth_kr
```

### 3.3 Prompt 파일 형식

```markdown
# stock-compass — Sentiment 분석 요청
> Batch ID: 2026-05-22-deepdive-value_growth_kr
> 종목 수: 10
> 생성: 2026-05-22 16:35 KST

## 작업 지시

다음 종목별로 최근 뉴스·공시를 분석하여 JSON 형식으로 응답해줘.

**원칙**:
1. 객관적 사실만. 추측·예측 금지.
2. 원문 30단어 이상 그대로 인용 금지.
3. 자체 표현으로 3줄 요약.
4. 톤 점수 -10(매우 부정) ~ +10(매우 긍정).
5. 핵심 키워드 5개 추출.

**응답 형식** (반드시 이 형식 그대로, 다른 텍스트 X):

\`\`\`json
{
  "batch_id": "2026-05-22-deepdive-value_growth_kr",
  "results": [
    {
      "ticker": "005930",
      "summary": "3줄 요약...",
      "tone_score": 1.2,
      "keywords": ["키워드1", "키워드2", ...],
      "concerns": ["주의사항 있으면"]
    },
    ...
  ]
}
\`\`\`

---

## 종목 1: 삼성전자 (005930)

### 최근 7일 뉴스 (5건)

#### 1. [2026-05-21] HBM3E 양산 확대, 엔비디아 추가 공급계약 임박
출처: 매일경제 / URL: https://...
요약 원문: 삼성전자가 HBM3E 양산을 확대하며 엔비디아와의 추가 공급계약이 임박한 것으로 알려졌다. ...

#### 2. [2026-05-20] ...
[...]

### 최근 30일 공시 (3건)

#### 1. [2026-05-15] 자기주식 취득 처분 신탁계약 체결
공시번호: 20260515000123
DART URL: https://dart.fss.or.kr/...
주요 내용: 5,000억원 규모 자사주 매입 신탁계약 체결. 기간: 2026-05-20 ~ 2026-08-19.

---

## 종목 2: 카카오 (035720)
[...]

---

## 응답 가이드 (한 번 더)

- JSON 코드블록 1개만 응답
- batch_id 정확히 일치
- results 배열 순서는 위 종목 순서 유지
- 누락 종목 있으면 `"summary": "데이터 부족"`, `"tone_score": 0`
```

### 3.4 응답 import 로직

```python
# src/stock_compass/llm/prompt_importer.py

def import_response(file_path: Path, batch_id: str) -> ImportResult:
    """Claude.ai 응답 파일을 파싱하여 DB 저장.

    1. 파일에서 JSON 코드블록 추출 (정규식)
    2. JSON 파싱 + Pydantic 검증
    3. batch_id 일치 확인
    4. 각 종목별 sentiment 점수 계산 (tone_score → 0~100 정규화)
    5. news_summaries 테이블에 일괄 저장 (source='manual_prompt')
    6. factor_scores.sentiment 갱신
    """
```

### 3.5 모드 표시

`composite_scores.sentiment_source` 칼럼 추가:
- `'api'` — Anthropic API
- `'manual'` — Claude.ai 복붙
- `'fallback'` — 한도 초과로 50점 처리

리포트에 표시:
```
삼성전자 점수 78점 (sentiment: manual 2일전)
```

---

## 4) 비용 비교 (실제 시나리오)

### 시나리오 A: 워치리스트 10종목만 추적

- API 자동: 월 ~$4
- Pro 정액: 사용 안 함
- **총 ~$4/월** (API 모드와 동일)

### 시나리오 B: 워치리스트 10 + 주 1회 스크리너 30종목 심층

- API: 워치리스트만 → 월 $4
- Prompt: 주 1회 × 30종목 → Claude.ai에서 처리
- Pro: 월 $20 (어차피 구독자라면 추가 비용 0)
- **총 $4/월 (API) + $20/월 (Pro, 이미 사용 중이면 무료 효과)**

### 시나리오 C: 같은 작업을 API로만

- 월 4 + (30종목 × 4주 × 5뉴스 × 호출당 비용) = 월 ~$8 (Haiku) / ~$25 (Sonnet)

→ Pro 구독자가 아니라면 hybrid가 의미 없음. **Pro 사용 중이라면 hybrid 우위.**

⚠️ Claude.ai Pro 사용 한도가 있으니, 30종목 일괄 분석은 분할 필요할 수 있음. 한 번에 5~10종목씩.

---

## 5) 설정

`.env.local`:

```bash
# 모드 선택
SENTIMENT_MODE=hybrid    # api / prompt / hybrid

# Hybrid 동작 설정
HYBRID_API_FOR=watchlist            # API 사용 대상
HYBRID_PROMPT_FOR=screener,manual   # Prompt 모드 대상

# Prompt 파일 위치
PROMPT_DIR=data/prompts
PROMPT_RESPONSE_INBOX=~/Downloads   # 응답 파일을 여기서 자동 탐색
```

---

## 6) 자동 import 워크플로우 (Apple Shortcuts 통합)

선택 사항. Apple Shortcuts로 워크플로우:

1. **Shortcut: "Claude 응답 import"**
   - 클립보드 → `~/Downloads/claude_latest.txt` 저장
   - `stock-compass sentiment import --file ~/Downloads/claude_latest.txt` 실행
   - 결과 알림

2. **iOS 단축어**: Claude.ai에서 응답 복사 → 단축어 실행 → Mac SSH로 명령 전달
   (조금 복잡, Phase 7 이후 별도 작업)

---

## 7) 보안 / 법률

- Prompt 파일은 본인 워치리스트·전략 정보 포함 → **공유 금지**
- Claude.ai 응답에 원문 인용이 포함되어 들어올 수 있음 → import 시 자동 단어 수 체크 (30단어 이상 인용 감지 시 경고)
- 응답 파일은 import 후 자동 보관 (`data/prompts/archive/`)
- DB의 `news_summaries.source='manual_prompt'`로 표시되어 audit 가능
