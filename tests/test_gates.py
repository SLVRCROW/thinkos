"""Tests for approval gates."""

import os
import io
import sys
import pytest
from thinkos.gates.always_allow import AlwaysAllowGate
from thinkos.gates.confirm import ConfirmGate
from thinkos.gates.deny_all import DenyAllGate


class TestAlwaysAllowGate:
    def test_returns_allow(self):
        gate = AlwaysAllowGate()
        result = gate.evaluate("any_tool", {})
        assert result["action"] == "allow"


class TestDenyAllGate:
    def test_returns_deny(self):
        gate = DenyAllGate()
        result = gate.evaluate("any_tool", {})
        assert result["action"] == "deny"


# ---------------------------------------------------------------------------
# Helpers for mocking /dev/tty and the non-interactive env-var
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Remove THINKOS_NONINTERACTIVE before every test so tests start clean."""
    monkeypatch.delenv("THINKOS_NONINTERACTIVE", raising=False)


class _InteractiveInput(io.StringIO):
    """String input that models stdin attached to a controlling terminal."""

    def isatty(self):
        return True


class _MockTTY:
    """Context manager that replaces open("/dev/tty") with a StringIO.

    Usage::

        with _MockTTY("y\\n") as tty:
            # code that reads from /dev/tty
    """

    def __init__(self, input_text: str):
        self._buf = io.StringIO(input_text)

    def __enter__(self):
        # Monkey-patch builtins.open so that any attempt to open "/dev/tty"
        # returns our StringIO instead.
        import builtins
        self._real_open = builtins.open
        self._tty_path = "/dev/tty"
        self._old_stdin = sys.stdin
        sys.stdin = _InteractiveInput(self._buf.getvalue())

        def _fake_open(path, *args, **kwargs):
            if path == self._tty_path:
                return self._buf
            return self._real_open(path, *args, **kwargs)

        builtins.open = _fake_open
        return self

    def __exit__(self, *exc):
        import builtins
        builtins.open = self._real_open
        sys.stdin = self._old_stdin


class _NoTTY:
    """Context manager that makes /dev/tty appear unavailable.

    Usage::

        with _NoTTY():
            # code that tries to open /dev/tty will get an OSError
    """

    def __enter__(self):
        import builtins
        self._real_open = builtins.open
        self._tty_path = "/dev/tty"

        def _fake_open(path, *args, **kwargs):
            if path == self._tty_path:
                raise OSError("[errno 6] No such device or address")
            return self._real_open(path, *args, **kwargs)

        builtins.open = _fake_open
        return self

    def __exit__(self, *exc):
        import builtins
        builtins.open = self._real_open


# ---------------------------------------------------------------------------
# ConfirmGate tests
# ---------------------------------------------------------------------------

class TestConfirmGate:

    # -- read tools -------------------------------------------------------

    def test_allows_read_tools(self):
        """Read tools are always allowed regardless of TTY availability."""
        gate = ConfirmGate()
        result = gate.evaluate("read_file", {"path": "/tmp/test"})
        assert result["action"] == "allow"

    def test_read_tools_allowed_in_non_tty(self):
        """Read tools pass through in non-TTY mode."""
        with _NoTTY():
            gate = ConfirmGate()
            result = gate.evaluate("read_file", {"path": "/tmp/test"})
            assert result["action"] == "allow"

    # -- non-TTY mode ----------------------------------------------------

    def test_write_tools_denied_in_non_tty(self):
        """Write tools are denied in non-TTY mode with a clear reason."""
        with _NoTTY():
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "deny"
            assert "Non-interactive mode" in result["reason"]

    def test_write_tools_denied_when_tty_unavailable(self):
        """Write tools are denied when /dev/tty open fails."""
        with _NoTTY():
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "deny"

    def test_write_tools_denied_without_interactive_stdin_even_if_tty_exists(self, monkeypatch):
        """A present /dev/tty must not block a noninteractive process."""
        import builtins

        monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))

        def _unexpected_tty_open(path, *args, **kwargs):
            if path == "/dev/tty":
                raise AssertionError("noninteractive execution must not open /dev/tty")
            return builtins.open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _unexpected_tty_open)
        result = ConfirmGate().evaluate("write_file", {"path": "/tmp/test"})
        assert result["action"] == "deny"
        assert result["reason"] == "Non-interactive mode: write approval unavailable"

    # -- THINKOS_NONINTERACTIVE env var ----------------------------------

    def test_noninteractive_env_var_forces_non_tty(self, monkeypatch):
        """THINKOS_NONINTERACTIVE=1 forces non-TTY behaviour even when TTY exists."""
        monkeypatch.setenv("THINKOS_NONINTERACTIVE", "1")
        # Use _MockTTY to simulate a working /dev/tty — the env var should
        # short-circuit before any TTY read is attempted.
        with _MockTTY("y\n"):
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "deny"
            assert "Non-interactive mode" in result["reason"]

    def test_noninteractive_env_var_true(self, monkeypatch):
        """THINKOS_NONINTERACTIVE=true also forces non-TTY."""
        monkeypatch.setenv("THINKOS_NONINTERACTIVE", "true")
        with _MockTTY("y\n"):
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "deny"

    def test_noninteractive_env_var_yes(self, monkeypatch):
        """THINKOS_NONINTERACTIVE=yes also forces non-TTY."""
        monkeypatch.setenv("THINKOS_NONINTERACTIVE", "yes")
        with _MockTTY("y\n"):
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "deny"

    def test_noninteractive_env_var_falsy_does_not_force(self, monkeypatch):
        """THINKOS_NONINTERACTIVE=0 does NOT force non-TTY (TTY mode still works)."""
        monkeypatch.setenv("THINKOS_NONINTERACTIVE", "0")
        with _MockTTY("y\n"):
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "allow"

    def test_noninteractive_env_var_false_does_not_force(self, monkeypatch):
        """THINKOS_NONINTERACTIVE=false does NOT force non-TTY."""
        monkeypatch.setenv("THINKOS_NONINTERACTIVE", "false")
        with _MockTTY("y\n"):
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "allow"

    # -- TTY mode --------------------------------------------------------

    def test_tty_mode_approves_write_on_y(self):
        """TTY mode with 'y' input allows the write."""
        with _MockTTY("y\n"):
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "allow"

    def test_tty_mode_approves_write_on_yes(self):
        """TTY mode with 'yes' input allows the write."""
        with _MockTTY("yes\n"):
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "allow"

    def test_tty_mode_denies_write_on_n(self):
        """TTY mode with 'n' input denies the write."""
        with _MockTTY("n\n"):
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "deny"

    def test_tty_mode_denies_write_on_no(self):
        """TTY mode with 'no' input denies the write."""
        with _MockTTY("no\n"):
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "deny"

    def test_tty_mode_default_denies_on_empty_input(self):
        """TTY mode with empty input (just Enter) denies the write."""
        with _MockTTY("\n"):
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "deny"

    def test_tty_mode_denies_on_random_input(self):
        """TTY mode with arbitrary text denies the write."""
        with _MockTTY("maybe\n"):
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "deny"

    def test_tty_mode_denies_on_eof(self):
        """TTY mode with EOF on /dev/tty denies the write (empty readline)."""
        with _MockTTY(""):
            gate = ConfirmGate()
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "deny"

    # -- prompt goes to stderr -------------------------------------------

    def test_prompt_written_to_stderr(self, capsys):
        """The prompt text is written to stderr, never stdout."""
        with _MockTTY("y\n"):
            gate = ConfirmGate()
            gate.evaluate("write_file", {"path": "/tmp/test"})
            captured = capsys.readouterr()
            # stdout must be empty (JSON-Lines channel is clean)
            assert captured.out == ""
            # stderr must contain the prompt
            assert "Allow?" in captured.err
