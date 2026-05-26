"""stock-compass CLI — typer 진입점 (얇은 facade).

실제 명령 구현은 `stock_compass.commands.*` 모듈에 있다. 이 파일은 패키지를
import해서 모든 데코레이터를 등록한 뒤 `app`만 노출한다.

면책: 본 도구의 출력은 투자 자문이 아닙니다. 본인 판단의 보조 자료입니다.
"""

from __future__ import annotations

from stock_compass.commands import app

__all__ = ["app"]


if __name__ == "__main__":
    app()
