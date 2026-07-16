"""Entry point for `python -m thinkos` and the `thinkos` CLI."""

import argparse
import sys
from importlib.metadata import version as _pkg_version


def _print_help():
    print("ThinkOS — Agent-native operating layer for externalizing project memory.")
    print()
    print("Usage:")
    print("  thinkos [--help | --version]")
    print("  python -m thinkos")
    print()
    print("Options:")
    print("  --help       Show this help message and exit.")
    print("  --version    Show the installed version and exit.")
    print()
    print("Without options, ThinkOS reads JSON-Lines messages from stdin and")
    print("writes JSON-Lines responses to stdout.  See README.md for the")
    print("message protocol and configuration reference.")
    print()
    print("Configuration file: thinkos.json or .thinkos.json in the current")
    print("working directory.")


def _print_version():
    try:
        ver = _pkg_version("thinkos")
    except Exception:
        ver = "0.1.0"
    print(f"thinkos {ver}")


def main():
    # Quick arg scan before importing the heavy engine modules.
    # Only --help and --version are recognised; everything else (including
    # no arguments) falls through to the JSON-Lines engine.
    if len(sys.argv) > 1:
        arg0 = sys.argv[1].strip().lower()
        if arg0 in ("--help", "-h", "/?"):
            _print_help()
            return
        if arg0 in ("--version", "-v", "-V"):
            _print_version()
            return

    # Full engine import and run
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

    # TAA initialization
    handoff_service = None
    identity_provider = None
    taa_config = config.get("taa", {})
    taa_enabled = taa_config.get("enabled", False)

    if taa_enabled:
        try:
            from thinkos.identity.process_bound import ProcessBoundIdentityProvider
            from thinkos.policy.handoff_policy import HandoffPolicy
            from thinkos.service.handoff_service import TrustedHandoffService

            identity_provider = ProcessBoundIdentityProvider(config)
            ctx = identity_provider.get_context()
            policy = HandoffPolicy(config)
            handoff_service = TrustedHandoffService(store, ctx, policy)
        except (ValueError, KeyError) as e:
            print(f"[thinkos] TAA initialization failed: {e}", file=sys.stderr)
            sys.exit(1)

    engine = Engine(
        store, connector, TOOL_REGISTRY, GATE_REGISTRY, config,
        handoff_service=handoff_service,
        identity_provider=identity_provider,
    )
    engine.run()


if __name__ == "__main__":
    main()
