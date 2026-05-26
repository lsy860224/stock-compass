"""screen 명령의 광고된 옵션 (--add-to-watchlist / --prompt-deepdive / --interactive) 정합."""

from __future__ import annotations

from typer.testing import CliRunner

from stock_compass.commands._app import app


def test_screen_help_exposes_advertised_options() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["screen", "--help"])
    assert result.exit_code == 0
    # README.md / docs/HYBRID_SENTIMENT.md / docs/SCREENER_SPEC.md 가 광고하는 옵션
    assert "--add-to-watchlist" in result.stdout
    assert "--group" in result.stdout
    assert "--prompt-deepdive" in result.stdout
    assert "--interactive" in result.stdout


def test_screen_interactive_friendly_exit() -> None:
    """Phase 7-8 미구현이라도 typer 에러 대신 친절 안내 후 exit code 2."""
    runner = CliRunner()
    result = runner.invoke(app, ["screen", "--interactive"])
    assert result.exit_code == 2
    assert "Phase 7-8" in result.stdout
    assert "--preset" in result.stdout  # 대안 안내가 있어야 함
