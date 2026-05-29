"""KRX 로그인 자격증명 주입 + pykrx 출력 배너 억제.

pykrx 1.2.x 는 지수 구성종목·전종목 스냅샷 엔드포인트에 KRX 로그인
(`KRX_ID`/`KRX_PW` 환경변수)을 요구한다. 또한 import·로그인 시도 시 stdout 으로
배너("KRX 로그인 실패: ...", "KRX 로그인 시도..." 등)를 직접 출력해 CLI·배치
출력을 오염시킨다.

이 모듈은:
- `apply_krx_credentials()` — settings 의 자격증명을 pykrx auth 가 읽는 환경변수로 노출
- `krx_quiet()` — pykrx import·호출 구간의 stdout 배너를 삼키는 컨텍스트 매니저

자격증명이 없으면 pykrx 지수 엔드포인트는 빈 응답 → 호출자가 FinanceDataReader
프록시로 폴백한다(`screener.universes._sources`).
"""

from __future__ import annotations

import contextlib
import io
import os
from collections.abc import Iterator

from stock_compass.config import settings


def apply_krx_credentials() -> bool:
    """`settings.krx_id`/`krx_pw` 를 `os.environ` 에 노출 (pykrx auth 가 읽음).

    Returns:
        둘 다 설정돼 있으면 True (정확 지수 조회 가능), 아니면 False (FDR 폴백).
    """
    if settings.krx_id and settings.krx_pw:
        os.environ["KRX_ID"] = settings.krx_id
        os.environ["KRX_PW"] = settings.krx_pw.get_secret_value()
        return True
    return False


@contextlib.contextmanager
def krx_quiet() -> Iterator[None]:
    """pykrx import·로그인 호출이 stdout 으로 찍는 배너를 억제하는 컨텍스트.

    자격증명도 함께 주입한다. pykrx 의 로그인 흐름은 ``print`` 기반이라
    logging 으로 잡히지 않으므로 stdout 자체를 임시 리다이렉트한다.
    """
    apply_krx_credentials()
    with contextlib.redirect_stdout(io.StringIO()):
        yield
