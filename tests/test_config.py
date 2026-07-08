"""Tests for config loader — including allowed_root sandbox defaults."""

import json
import tempfile
import os
import pytest
from thinkos.config import load_config, resolve_gate, get_allowed_root, validate_config
from thinkos.gates.always_allow import AlwaysAllowGate
from thinkos.gates.confirm import ConfirmGate


class TestLoadConfig:
    def test_default_config_has_tools_key(self):
        config = load_config("/nonexistent/path.json")
        assert "tools" in config
        assert "allowed_root" in config["tools"]

    def test_default_allowed_root_is_cwd(self):
        """When no config file exists, allowed_root should be the CWD."""
        config = load_config("/nonexistent/path.json")
        root = get_allowed_root(config)
        assert root is not None
        assert root == os.getcwd()

    def test_config_file_directory_becomes_allowed_root(self):
        """When a config file exists, its directory becomes the allowed root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "thinkos.json")
            with open(config_path, "w") as f:
                json.dump({"gates": {"default": "always_allow"}}, f)
            config = load_config(config_path)
            root = get_allowed_root(config)
            assert root == tmpdir

    def test_explicit_null_disables_sandboxing(self):
        """Explicit allowed_root: null means unsafe mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "thinkos.json")
            with open(config_path, "w") as f:
                json.dump({"tools": {"allowed_root": None}}, f)
            config = load_config(config_path)
            root = get_allowed_root(config)
            assert root is None

    def test_custom_config_merges_with_defaults(self):
        data = {"gates": {"default": "always_allow", "overrides": {}}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            fname = f.name
        try:
            config = load_config(fname)
            assert config["gates"]["default"] == "always_allow"
            # tools key should still be present from defaults
            assert "tools" in config
        finally:
            os.unlink(fname)

    # -- Limits tests ---------------------------------------------------

    def test_default_limits_exist(self):
        """DEFAULT_CONFIG has a limits key with all three defaults."""
        config = load_config("/nonexistent/path.json")
        assert "limits" in config
        assert config["limits"]["max_line_bytes"] == 1048576
        assert config["limits"]["max_write_content_bytes"] == 10485760
        assert config["limits"]["max_read_output_bytes"] == 1048576

    def test_limits_override_from_config(self):
        """Custom limits in thinkos.json override defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "thinkos.json")
            with open(config_path, "w") as f:
                json.dump({
                    "limits": {
                        "max_line_bytes": 999,
                        "max_write_content_bytes": 888,
                        "max_read_output_bytes": 777,
                    }
                }, f)
            config = load_config(config_path)
            assert config["limits"]["max_line_bytes"] == 999
            assert config["limits"]["max_write_content_bytes"] == 888
            assert config["limits"]["max_read_output_bytes"] == 777

    def test_limits_partial_override(self):
        """Setting one limit in config preserves defaults for others."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "thinkos.json")
            with open(config_path, "w") as f:
                json.dump({
                    "limits": {
                        "max_line_bytes": 500,
                    }
                }, f)
            config = load_config(config_path)
            assert config["limits"]["max_line_bytes"] == 500
            # Other limits should still have defaults
            assert config["limits"]["max_write_content_bytes"] == 10485760
            assert config["limits"]["max_read_output_bytes"] == 1048576


class TestResolveGate:
    def test_default_gate(self):
        gate_registry = {"confirm": ConfirmGate()}
        config = load_config("/nonexistent/path.json")
        gate = resolve_gate("write_file", config, gate_registry)
        assert gate.name == "confirm"

    def test_override_gate(self):
        gate_registry = {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate()}
        config = load_config("/nonexistent/path.json")
        gate = resolve_gate("read_file", config, gate_registry)
        assert gate.name == "always_allow"

    def test_missing_gate_raises(self):
        config = load_config("/nonexistent/path.json")
        with pytest.raises(ValueError):
            resolve_gate("read_file", config, {})


class TestValidateConfig:
    """Tests for config load-time validation against registries."""

    def test_valid_config_passes(self):
        config = load_config("/nonexistent/path.json")
        tool_registry = {"read_file": object(), "write_file": object()}
        gate_registry = {"always_allow": object(), "confirm": object(), "deny_all": object()}
        errors = validate_config(config, tool_registry, gate_registry)
        assert errors == []

    def test_invalid_default_gate_reported(self):
        config = load_config("/nonexistent/path.json")
        config["gates"]["default"] = "nonexistent_gate"
        tool_registry = {"read_file": object()}
        gate_registry = {"always_allow": object(), "confirm": object()}
        errors = validate_config(config, tool_registry, gate_registry)
        assert any("nonexistent_gate" in e for e in errors)
        assert any("gates.default" in e for e in errors)

    def test_unknown_tool_in_override_reported(self):
        config = load_config("/nonexistent/path.json")
        config["gates"]["overrides"]["unknown_tool"] = "confirm"
        tool_registry = {"read_file": object()}
        gate_registry = {"confirm": object()}
        errors = validate_config(config, tool_registry, gate_registry)
        assert any("unknown_tool" in e for e in errors)

    def test_invalid_gate_in_override_reported(self):
        config = load_config("/nonexistent/path.json")
        config["gates"]["overrides"]["read_file"] = "nonexistent_gate"
        tool_registry = {"read_file": object()}
        gate_registry = {"confirm": object()}
        errors = validate_config(config, tool_registry, gate_registry)
        assert any("nonexistent_gate" in e for e in errors)
        assert any("read_file" in e for e in errors)

    def test_multiple_errors_reported(self):
        config = load_config("/nonexistent/path.json")
        config["gates"]["default"] = "bad_default"
        config["gates"]["overrides"]["bad_tool"] = "bad_gate"
        tool_registry = {"read_file": object()}
        gate_registry = {"always_allow": object()}
        errors = validate_config(config, tool_registry, gate_registry)
        assert len(errors) >= 3  # bad_default + bad_tool ref + bad_gate ref

    def test_gates_not_a_dict_reported(self):
        """Structural check: 'gates' must be a dict."""
        tool_registry = {"read_file": object()}
        gate_registry = {"confirm": object()}
        errors = validate_config({"gates": "not_a_dict"}, tool_registry, gate_registry)
        assert any("gates" in e and "dict" in e for e in errors)

    def test_overrides_not_a_dict_reported(self):
        """Structural check: 'gates.overrides' must be a dict."""
        tool_registry = {"read_file": object()}
        gate_registry = {"confirm": object()}
        errors = validate_config(
            {"gates": {"default": "confirm", "overrides": "not_a_dict"}},
            tool_registry, gate_registry
        )
        assert any("overrides" in e and "dict" in e for e in errors)
