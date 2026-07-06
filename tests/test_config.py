"""Tests for config loader."""

import json
import tempfile
import os
from thinkos.config import load_config, resolve_gate, DEFAULT_CONFIG
from thinkos.gates.always_allow import AlwaysAllowGate
from thinkos.gates.confirm import ConfirmGate


class TestLoadConfig:
    def test_default_config(self):
        config = load_config("/nonexistent/path.json")
        assert config == DEFAULT_CONFIG

    def test_custom_config(self):
        data = {"gates": {"default": "always_allow", "overrides": {}}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            fname = f.name
        try:
            config = load_config(fname)
            assert config["gates"]["default"] == "always_allow"
        finally:
            os.unlink(fname)


class TestResolveGate:
    def test_default_gate(self):
        gate_registry = {"confirm": ConfirmGate()}
        gate = resolve_gate("write_file", DEFAULT_CONFIG, gate_registry)
        assert gate.name == "confirm"

    def test_override_gate(self):
        gate_registry = {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate()}
        gate = resolve_gate("read_file", DEFAULT_CONFIG, gate_registry)
        assert gate.name == "always_allow"

    def test_missing_gate_raises(self):
        import pytest
        with pytest.raises(ValueError):
            resolve_gate("read_file", DEFAULT_CONFIG, {})
