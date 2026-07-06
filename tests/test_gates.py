"""Tests for approval gates."""

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


class TestConfirmGate:
    def test_allows_read_tools(self):
        gate = ConfirmGate()
        result = gate.evaluate("read_file", {"path": "/tmp/test"})
        assert result["action"] == "allow"

    def test_asks_for_write_tools(self):
        gate = ConfirmGate()
        # Override stdin to simulate no input (test mode)
        import io
        import sys
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("\n")
        try:
            result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert result["action"] == "deny"  # Default deny on empty input
        finally:
            sys.stdin = old_stdin
