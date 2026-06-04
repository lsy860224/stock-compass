"""환경변수 로드·검증 (.env.local → Pydantic Settings).

사용:
    from stock_compass.config import settings
    print(settings.watchlist_kr)  # list[str]

필수 키 누락 시 ConfigurationError 발생 (CLI 진입점에서 catch).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """환경변수 설정 오류 (필수 키 누락·잘못된 값)."""


PROJECT_ROOT = Path(__file__).resolve().parents[2]

Market = Literal["KR", "US"]
SentimentMode = Literal["api", "prompt", "hybrid"]


class Settings(BaseSettings):
    """프로젝트 전역 설정. `.env.local`에서 자동 로드.

    필수 키가 누락되면 Pydantic ValidationError 발생 → `load_settings()`에서
    ConfigurationError로 감싸 사용자 친화 메시지로 변환.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env.local",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── 시장·워치리스트 (필수) ───
    default_market: Market = "KR"
    watchlist_kr: Annotated[list[str], NoDecode] = Field(default_factory=list)
    watchlist_us: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ─── Anthropic ───
    anthropic_api_key: SecretStr
    anthropic_model_default: str = "claude-haiku-4-5-20251001"
    anthropic_model_heavy: str = "claude-sonnet-4-6"
    anthropic_daily_input_limit: int = 500_000
    anthropic_daily_output_limit: int = 100_000

    # ─── FRED (미국 거시) ───
    fred_api_key: SecretStr

    # ─── DART (한국 공시, KR 시 필수) ───
    dart_api_key: SecretStr | None = None

    # ─── Naver 검색 (선택, KR 뉴스 보강) ───
    naver_client_id: str | None = None
    naver_client_secret: SecretStr | None = None

    # ─── KRX 로그인 (선택, 정확 지수 구성종목) ───
    # data.krx.co.kr 무료 계정. KRX가 지수 구성 데이터를 로그인 뒤로 이전 →
    # 미설정 시 FinanceDataReader 시총상위 N 프록시로 자동 폴백.
    krx_id: str | None = None
    krx_pw: SecretStr | None = None

    # ─── Craft (선택, Phase 5+) ───
    # CRAFT_API_TOKEN은 Craft Imagine 탭에서 받은 "API URL" 그대로 (secret 포함).
    # 예: https://connect.craft.do/links/<secret>/api/v1 — 헤더 인증 불필요.
    craft_api_token: SecretStr | None = None
    craft_daily_folder_id: str | None = None
    craft_tickers_folder_id: str | None = None
    # Deprecated — Craft 공식 API는 URL 자체에 secret 포함이라 별도 base 불필요.
    # 환경변수가 남아 있어도 무시 (호환성).
    craft_api_base_url: str = "https://connect.craft.do"

    # ─── Sentiment 모드 (하이브리드 기본) ───
    sentiment_mode: SentimentMode = "hybrid"
    hybrid_api_for: str = "watchlist"
    hybrid_prompt_for: str = "screener,manual"
    prompt_dir: Path = PROJECT_ROOT / "data" / "prompts"

    # ─── 점수·알림 임계치 ───
    alert_threshold_buy: int = 80
    alert_threshold_caution: int = 30
    alert_delta_min: int = 15
    daily_report_time: str = "07:00"

    # ─── 경로 ───
    db_path: Path = PROJECT_ROOT / "data" / "stock_compass.db"
    cache_dir: Path = PROJECT_ROOT / "data" / "cache"
    log_dir: Path = PROJECT_ROOT / "logs"
    craft_export_dir: Path = PROJECT_ROOT / "data" / "craft_export"

    # ─── Obsidian (자동 보고 dual-sink) ───
    # 볼트는 로컬 파일시스템 폴더 — launchd 독립 프로세스가 .md 직접 기록 (MCP 불가).
    # 미설정/미존재(iCloud 미동기) 시 graceful skip. .env.local에서 override 가능.
    obsidian_vault_dir: Path | None = Path(
        "/Users/seung-yeoblee/Library/Mobile Documents/"
        "iCloud~md~obsidian/Documents/Personal Hub"
    )
    obsidian_reports_subdir: str = "03. Stock-Compass/Reports"

    # ─── 외부 호출 동작 ───
    yfinance_throttle_sec: float = 0.5
    external_api_retry: int = 3
    external_api_timeout: int = 10
    enable_cache: bool = True

    # ─── 스크리너 ───
    screener_default_limit: int = 50

    @field_validator("watchlist_kr", "watchlist_us", mode="before")
    @classmethod
    def _parse_csv(cls, v: object) -> list[str]:
        """`AAPL,MSFT` 형식 콤마 구분 문자열을 list[str]로 변환."""
        if v is None or v == "":
            return []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        raise TypeError(f"watchlist 값이 문자열 또는 리스트여야 합니다: {v!r}")

    @field_validator("default_market", mode="before")
    @classmethod
    def _upper_market(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v

    @field_validator("sentiment_mode", mode="before")
    @classmethod
    def _lower_sentiment(cls, v: object) -> object:
        return v.lower() if isinstance(v, str) else v

    def requires_dart(self) -> bool:
        """KR 워치리스트가 있거나 DEFAULT_MARKET=KR 이면 DART 키 필요."""
        return self.default_market == "KR" or bool(self.watchlist_kr)


def load_settings() -> Settings:
    """Settings 인스턴스 생성. 누락된 필수 키를 친절한 메시지로 알려줌."""
    try:
        s = Settings()  # type: ignore[call-arg]
    except ValidationError as e:
        missing: list[str] = []
        invalid: list[str] = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"])
            if err["type"] == "missing":
                missing.append(loc.upper())
            else:
                invalid.append(f"{loc.upper()}: {err['msg']}")
        lines = ["환경변수 설정 오류 — .env.local 확인이 필요합니다."]
        if missing:
            lines.append(f"  • 필수 키 누락: {', '.join(missing)}")
        if invalid:
            lines.append("  • 잘못된 값:")
            lines.extend(f"      - {item}" for item in invalid)
        lines.append("")
        lines.append("템플릿: .env.example 참고 → cp .env.example .env.local")
        raise ConfigurationError("\n".join(lines)) from e

    if s.requires_dart() and s.dart_api_key is None:
        raise ConfigurationError(
            "DART_API_KEY가 필요합니다 (KR 시장 또는 WATCHLIST_KR 사용 중).\n"
            "발급: https://opendart.fss.or.kr → .env.local에 추가."
        )
    return s


settings = load_settings()
