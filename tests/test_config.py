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


class TestStoreConfig:
    """Tests for store.path config handling."""

    def test_default_store_path_is_none(self):
        """Default config has store.path=None, meaning :memory:."""
        config = load_config("/nonexistent/path.json")
        from thinkos.config import get_store_path
        assert get_store_path(config) is None

    def test_relative_store_path_resolves_to_workspace(self):
        """A relative store.path resolves against the workspace root."""
        import tempfile, os, json
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "thinkos.json")
            with open(config_path, "w") as f:
                json.dump({"store": {"path": "thinkos.db"}}, f)
            config = load_config(config_path)
            from thinkos.config import get_store_path
            result = get_store_path(config)
            assert result == os.path.join(tmpdir, "thinkos.db")

    def test_absolute_store_path_used_as_is(self):
        """An absolute store.path is used as-is."""
        import tempfile, os, json
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "thinkos.json")
            with open(config_path, "w") as f:
                json.dump({"store": {"path": "/tmp/thinkos.db"}}, f)
            config = load_config(config_path)
            from thinkos.config import get_store_path
            result = get_store_path(config)
            assert result == "/tmp/thinkos.db"

    def test_data_survives_reopen(self):
        """Writing a packet, closing the store, and reopening with the same path preserves data."""
        import tempfile, os
        from thinkos.store.sqlite_store import SQLiteStore
        from thinkos.schema.context_packet import ContextPacket
        import uuid
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "thinkos.db")
            store = SQLiteStore(db_path)
            pid = f"ctx_{uuid.uuid4()}"
            p = ContextPacket(
                packet_id=pid,
                session_id="sess_survive",
                timestamp="2026-07-09T23:00:00Z",
                kind="tool_result",
                source="test",
                content={"text": "survived", "structured": None},
            )
            store.write_packet(p)
            store.close()
            # Reopen
            store2 = SQLiteStore(db_path)
            p2 = store2.read_packet(pid)
            assert p2 is not None
            assert p2.content["text"] == "survived"
            store2.close()

    def test_invalid_store_path_type_rejected(self):
        """store.path must be a string or None — bool/int/list/dict are rejected."""
        from thinkos.config import validate_config
        tool_registry = {"read_file": object()}
        gate_registry = {"always_allow": object(), "confirm": object()}
        for bad_val in [True, 42, ["a"], {"nested": "dict"}]:
            errors = validate_config(
                {"gates": {"default": "confirm"}, "store": {"path": bad_val}},
                tool_registry, gate_registry
            )
            assert any("store.path" in e for e in errors), f"Expected error for {type(bad_val).__name__}"

    def test_store_not_a_dict_rejected(self):
        """store must be a dict if present."""
        from thinkos.config import validate_config
        tool_registry = {"read_file": object()}
        gate_registry = {"always_allow": object(), "confirm": object()}
        errors = validate_config(
            {"gates": {"default": "confirm"}, "store": "not_a_dict"},
            tool_registry, gate_registry
        )
        assert any("store" in e and "dict" in e for e in errors)


class TestMainStoreWiring:
    """Tests that main() passes the configured store path into SQLiteStore."""

    def test_main_uses_configured_store_path(self):
        """When store.path is set in config, main() creates SQLiteStore with that path."""
        import tempfile, os, json, sys
        from unittest.mock import patch
        from thinkos.__main__ import main

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "thinkos.json")
            db_path = os.path.join(tmpdir, "thinkos.db")
            with open(config_path, "w") as f:
                json.dump({"store": {"path": "thinkos.db"}}, f)

            original_cwd = os.getcwd()
            original_argv = sys.argv
            try:
                os.chdir(tmpdir)
                sys.argv = ["thinkos"]
                # Patch stdin to return EOF immediately so main() exits
                with patch("sys.stdin") as mock_stdin:
                    mock_stdin.buffer.readline.return_value = b""
                    # Patch SQLiteStore to capture the path
                    original_init = __import__("thinkos.store.sqlite_store", fromlist=["SQLiteStore"]).SQLiteStore.__init__
                    captured_paths = []

                    def patched_init(self, db_path=":memory:"):
                        captured_paths.append(db_path)
                        original_init(self, db_path)

                    with patch("thinkos.store.sqlite_store.SQLiteStore.__init__", patched_init):
                        main()

                assert len(captured_paths) == 1
                assert captured_paths[0] == db_path
            finally:
                os.chdir(original_cwd)
                sys.argv = original_argv

    def test_main_uses_memory_when_no_store_path(self):
        """When store.path is not set, main() creates SQLiteStore with ':memory:'."""
        import tempfile, os, json, sys
        from unittest.mock import patch
        from thinkos.__main__ import main

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "thinkos.json")
            with open(config_path, "w") as f:
                json.dump({"gates": {"default": "always_allow"}}, f)

            original_cwd = os.getcwd()
            original_argv = sys.argv
            try:
                os.chdir(tmpdir)
                sys.argv = ["thinkos"]
                with patch("sys.stdin") as mock_stdin:
                    mock_stdin.buffer.readline.return_value = b""
                    captured_paths = []
                    original_init = __import__("thinkos.store.sqlite_store", fromlist=["SQLiteStore"]).SQLiteStore.__init__

                    def patched_init(self, db_path=":memory:"):
                        captured_paths.append(db_path)
                        original_init(self, db_path)

                    with patch("thinkos.store.sqlite_store.SQLiteStore.__init__", patched_init):
                        main()

                assert len(captured_paths) == 1
                assert captured_paths[0] == ":memory:"
            finally:
                os.chdir(original_cwd)
                sys.argv = original_argv


class TestRehydrationConfig:
    """Tests for rehydration config validation."""

    def test_default_max_packets_is_none(self):
        """Default config has rehydration.max_packets=None (no truncation)."""
        config = load_config("/nonexistent/path.json")
        assert config.get("rehydration", {}).get("max_packets") is None

    def test_valid_max_packets_passes_validation(self):
        """Positive integer max_packets passes validation."""
        from thinkos.config import validate_config
        config = load_config("/nonexistent/path.json")
        config["rehydration"] = {"max_packets": 50}
        tool_registry = {"read_file": object(), "write_file": object()}
        gate_registry = {"always_allow": object(), "confirm": object(), "deny_all": object()}
        errors = validate_config(config, tool_registry, gate_registry)
        assert errors == []

    def test_max_packets_none_passes_validation(self):
        """None max_packets passes validation."""
        from thinkos.config import validate_config
        config = load_config("/nonexistent/path.json")
        config["rehydration"] = {"max_packets": None}
        tool_registry = {"read_file": object(), "write_file": object()}
        gate_registry = {"always_allow": object(), "confirm": object(), "deny_all": object()}
        errors = validate_config(config, tool_registry, gate_registry)
        assert errors == []

    def test_max_packets_zero_rejected(self):
        """max_packets=0 is rejected."""
        from thinkos.config import validate_config
        config = load_config("/nonexistent/path.json")
        config["rehydration"] = {"max_packets": 0}
        tool_registry = {"read_file": object(), "write_file": object()}
        gate_registry = {"always_allow": object(), "confirm": object(), "deny_all": object()}
        errors = validate_config(config, tool_registry, gate_registry)
        assert any("max_packets" in e for e in errors)

    def test_max_packets_negative_rejected(self):
        """Negative max_packets is rejected."""
        from thinkos.config import validate_config
        config = load_config("/nonexistent/path.json")
        config["rehydration"] = {"max_packets": -5}
        tool_registry = {"read_file": object(), "write_file": object()}
        gate_registry = {"always_allow": object(), "confirm": object(), "deny_all": object()}
        errors = validate_config(config, tool_registry, gate_registry)
        assert any("max_packets" in e for e in errors)

    def test_max_packets_string_rejected(self):
        """Non-integer max_packets is rejected."""
        from thinkos.config import validate_config
        config = load_config("/nonexistent/path.json")
        config["rehydration"] = {"max_packets": "fifty"}
        tool_registry = {"read_file": object(), "write_file": object()}
        gate_registry = {"always_allow": object(), "confirm": object(), "deny_all": object()}
        errors = validate_config(config, tool_registry, gate_registry)
        assert any("max_packets" in e for e in errors)

    def test_max_packets_float_rejected(self):
        """Float max_packets is rejected (must be int or None)."""
        from thinkos.config import validate_config
        config = load_config("/nonexistent/path.json")
        config["rehydration"] = {"max_packets": 5.5}
        tool_registry = {"read_file": object(), "write_file": object()}
        gate_registry = {"always_allow": object(), "confirm": object(), "deny_all": object()}
        errors = validate_config(config, tool_registry, gate_registry)
        assert any("max_packets" in e for e in errors)

    def test_rehydration_not_a_dict_rejected(self):
        """rehydration must be a dict if present."""
        from thinkos.config import validate_config
        tool_registry = {"read_file": object()}
        gate_registry = {"confirm": object()}
        errors = validate_config(
            {"gates": {"default": "confirm"}, "rehydration": "not_a_dict"},
            tool_registry, gate_registry
        )
        assert any("rehydration" in e and "dict" in e for e in errors)
