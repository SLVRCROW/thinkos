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
