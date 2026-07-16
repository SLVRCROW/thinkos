"""Entry point for `python -m thinkos` and the `thinkos` CLI."""

import sys
from importlib.metadata import version as _pkg_version


def _print_help():
    print("ThinkOS — Agent-native operating layer for externalizing project memory.")
    print()
    print("Usage:")
    print("  thinkos [--help | --version]")
    print("  thinkos init [PROJECT_PATH] [--json]")
    print("  thinkos doctor [PROJECT_PATH] [--json]")
    print("  python -m thinkos")
    print()
    print("Commands:")
    print("  init     Initialise ThinkOS for a project.")
    print("           Creates .thinkos/ with a safe default configuration,")
    print("           persistent SQLite store, and .gitignore protection.")
    print("           Defaults to the current directory.")
    print()
    print("  doctor   Check ThinkOS installation health for a project.")
    print("           Read-only. Checks Python compatibility, ThinkOS version,")
    print("           config presence and validity, sandbox status, store")
    print("           configuration, and SQLite integrity.")
    print("           Defaults to the current directory.")
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


def _print_unknown_command(cmd: str):
    print(f"thinkos: unknown command '{cmd}'", file=sys.stderr)
    print(f"Run 'thinkos --help' for usage.", file=sys.stderr)
    sys.exit(1)


def _parse_init_doctor_args() -> tuple[str | None, bool]:
    """Parse shared args for init and doctor commands.

    Returns (project_path, json_output).
    """
    project_path = None
    json_output = False
    args = sys.argv[2:]  # skip command name
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            json_output = True
        elif a.startswith("-"):
            print(f"thinkos: unknown option '{a}'", file=sys.stderr)
            sys.exit(1)
        else:
            project_path = a
        i += 1
    return project_path, json_output


def _run_init():
    from thinkos.onboarding import init as _init
    project_path, json_output = _parse_init_doctor_args()
    _init(project_path=project_path, json_output=json_output)


def _run_doctor():
    from thinkos.onboarding import doctor as _doctor
    project_path, json_output = _parse_init_doctor_args()
    result = _doctor(project_path=project_path, json_output=json_output)
    if result["status"] != "healthy":
        sys.exit(1)


def main():
    # Quick arg scan before importing the heavy engine modules.
    # --help, --version, init, and doctor are handled here without
    # initialising the engine.
    if len(sys.argv) > 1:
        arg0 = sys.argv[1].strip().lower()
        if arg0 in ("--help", "-h", "/?"):
            _print_help()
            return
        if arg0 in ("--version", "-v", "-V"):
            _print_version()
            return
        if arg0 == "init":
            _run_init()
            return
        if arg0 == "doctor":
            _run_doctor()
            return
        # Unknown command — fail clearly rather than silently starting engine
        _print_unknown_command(arg0)

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
