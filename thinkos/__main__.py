"""Entry point for `python -m thinkos`."""

import sys
from thinkos.engine import Engine
from thinkos.config import load_config, validate_config, get_store_path
from thinkos.store.sqlite_store import SQLiteStore
from thinkos.connector.stdin import StdinConnector
from thinkos.tools import TOOL_REGISTRY, register_tool
from thinkos.tools.read_file import ReadFileAdapter
from thinkos.tools.write_file import WriteFileAdapter
from thinkos.gates import GATE_REGISTRY, register_gate
from thinkos.gates.always_allow import AlwaysAllowGate
from thinkos.gates.confirm import ConfirmGate
from thinkos.gates.deny_all import DenyAllGate


def main():
    config = load_config()
    store_path = get_store_path(config)
    store = SQLiteStore(store_path if store_path else ":memory:")
    connector = StdinConnector(
        max_line_bytes=config.get("limits", {}).get("max_line_bytes", 1048576)
    )

    register_tool("read_file", ReadFileAdapter())
    register_tool("write_file", WriteFileAdapter())
    register_gate("always_allow", AlwaysAllowGate())
    register_gate("confirm", ConfirmGate())
    register_gate("deny_all", DenyAllGate())

    # Validate config against populated registries before starting engine
    errors = validate_config(config, TOOL_REGISTRY, GATE_REGISTRY)
    if errors:
        for err in errors:
            print(f"[thinkos] ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    engine = Engine(store, connector, TOOL_REGISTRY, GATE_REGISTRY, config)
    engine.run()


if __name__ == "__main__":
    main()
