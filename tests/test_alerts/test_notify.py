"""macos_notify — subprocess mock + escape + 비 macOS fallback."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from stock_compass.output.notify import _esc, macos_notify


class TestEscape:
    def test_quote_escaped(self) -> None:
        assert _esc('a"b') == 'a\\"b'

    def test_backslash_escaped(self) -> None:
        assert _esc("a\\b") == "a\\\\b"

    def test_newline_to_space(self) -> None:
        assert _esc("a\nb") == "a b"


class TestMacosNotify:
    def test_non_darwin_returns_false(self) -> None:
        with patch("stock_compass.output.notify.sys") as mock_sys:
            mock_sys.platform = "linux"
            assert macos_notify(title="t", body="b") is False

    def test_invokes_osascript_on_darwin(self) -> None:
        with patch("stock_compass.output.notify.sys") as mock_sys, \
             patch("stock_compass.output.notify.shutil.which", return_value="/usr/bin/osascript"), \
             patch("stock_compass.output.notify.subprocess.run") as mock_run:
            mock_sys.platform = "darwin"
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            assert macos_notify(title="hi", body="msg") is True
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "osascript"
            assert "hi" in cmd[2] and "msg" in cmd[2]

    def test_subtitle_passed(self) -> None:
        with patch("stock_compass.output.notify.sys") as mock_sys, \
             patch("stock_compass.output.notify.shutil.which", return_value="/usr/bin/osascript"), \
             patch("stock_compass.output.notify.subprocess.run") as mock_run:
            mock_sys.platform = "darwin"
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            macos_notify(title="t", body="b", subtitle="sub")
            script = mock_run.call_args[0][0][2]
            assert 'subtitle "sub"' in script

    def test_osascript_failure_returns_false(self) -> None:
        with patch("stock_compass.output.notify.sys") as mock_sys, \
             patch("stock_compass.output.notify.shutil.which", return_value="/usr/bin/osascript"), \
             patch("stock_compass.output.notify.subprocess.run") as mock_run:
            mock_sys.platform = "darwin"
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="error"
            )
            assert macos_notify(title="t", body="b") is False

    def test_missing_osascript_binary(self) -> None:
        with patch("stock_compass.output.notify.sys") as mock_sys, \
             patch("stock_compass.output.notify.shutil.which", return_value=None):
            mock_sys.platform = "darwin"
            assert macos_notify(title="t", body="b") is False
