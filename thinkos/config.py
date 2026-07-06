"""Configuration loader — reads config defaults, no YAML dependency."""

import json
import os

DEFAULT_CONFIG = {
    "gates": {
        "default": "confirm",
        "overrides": {
            "read_file": "always_allow",
            "write_file": "confirm",
        }
    }
}


def load_config(path: str | None = None) -> dict:
    """Load config from JSON file, or return defaults if file doesn't exist."""
    if path is None:
        # Look for thinkos.json in current directory
        for candidate in ["thinkos.json", ".thinkos.json"]:
            if os.path.isfile(candidate):
                with open(candidate, "r") as f:
                    return json.load(f)
    elif os.path.isfile(path):
        with open(path, "r") as f:
            return json.load(f)
    return dict(DEFAULT_CONFIG)


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
