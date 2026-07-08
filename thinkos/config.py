"""Configuration loader — reads config defaults, no YAML dependency."""

import copy
import json
import os

DEFAULT_CONFIG = {
    "gates": {
        "default": "confirm",
        "overrides": {
            "read_file": "always_allow",
            "write_file": "confirm",
        }
    },
    "tools": {
        "allowed_root": None  # Resolved at load time to workspace root
    },
    "limits": {
        "max_line_bytes": 1048576,
        "max_write_content_bytes": 10485760,
        "max_read_output_bytes": 1048576,
        "max_tool_calls_per_message": 10,
    }
}


def _resolve_default_root(config_path: str | None) -> str:
    """Determine the default allowed root from the environment."""
    if config_path and os.path.isfile(config_path):
        return os.path.dirname(os.path.abspath(config_path))
    return os.getcwd()


def _deep_merge(base: dict, override: dict):
    """Recursively merge override into base."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def load_config(path: str | None = None) -> dict:
    """Load config from JSON file, or return defaults if file doesn't exist.

    The default allowed_root is set to the workspace root:
    - If a config file is found, its directory becomes the root.
    - If no config file is found, the current working directory becomes the root.
    - If config explicitly sets "tools": {"allowed_root": null}, sandboxing
      is disabled (unsafe developer override).
    """
    config = copy.deepcopy(DEFAULT_CONFIG)

    # Auto-discover config file if path not specified
    if path is None:
        for candidate in ["thinkos.json", ".thinkos.json"]:
            if os.path.isfile(candidate):
                path = candidate
                break

    # Track whether the user explicitly set allowed_root
    user_explicitly_set_allowed_root = False

    # Load user config if it exists
    if path and os.path.isfile(path):
        with open(path, "r") as f:
            user_config = json.load(f)
        # Check if user explicitly set allowed_root (including to null)
        if "tools" in user_config and "allowed_root" in user_config["tools"]:
            user_explicitly_set_allowed_root = True
        _deep_merge(config, user_config)

    # If allowed_root was not explicitly set by the user, default to workspace root
    if not user_explicitly_set_allowed_root and config.get("tools", {}).get("allowed_root") is None:
        config["tools"]["allowed_root"] = _resolve_default_root(path)

    return config


def get_allowed_root(config: dict) -> str | None:
    """Return the allowed root, or None for unsafe mode."""
    return config.get("tools", {}).get("allowed_root")


def resolve_gate(tool_name: str, config: dict, gate_registry: dict):
    """Return the gate instance for a given tool name."""
    gates_config = config.get("gates", {})
    overrides = gates_config.get("overrides", {})
    default_name = gates_config.get("default", "confirm")

    gate_name = overrides.get(tool_name, default_name)
    gate = gate_registry.get(gate_name)
    if gate is None:
        raise ValueError(f"Gate '{gate_name}' not found in registry")
    return gate


def validate_config(config: dict, tool_registry: dict, gate_registry: dict) -> list[str]:
    """Validate config references against registries.

    Checks that gate names in gates.default and gates.overrides exist
    in the gate registry, and that tool names in gates.overrides exist
    in the tool registry.

    Returns a list of error messages (empty list if config is valid).
    """
    errors: list[str] = []

    gates_config = config.get("gates", {})
    if not isinstance(gates_config, dict):
        errors.append("Config error: 'gates' must be a dict")
        return errors

    # Validate default gate
    default_gate = gates_config.get("default", "confirm")
    if default_gate not in gate_registry:
        errors.append(
            f"Config error: gates.default='{default_gate}' not found in gate registry "
            f"(available: {sorted(gate_registry.keys())})"
        )

    # Validate tool overrides
    overrides = gates_config.get("overrides", {})
    if not isinstance(overrides, dict):
        errors.append("Config error: gates.overrides must be a dict")
        return errors

    for tool_name, gate_name in overrides.items():
        if tool_name not in tool_registry:
            errors.append(
                f"Config error: gates.overrides references unknown tool '{tool_name}' "
                f"(registered tools: {sorted(tool_registry.keys())})"
            )
        if gate_name not in gate_registry:
            errors.append(
                f"Config error: gate '{gate_name}' (override for tool '{tool_name}') "
                f"not found in gate registry (available: {sorted(gate_registry.keys())})"
            )

    return errors
