"""Path sandboxing — resolves and validates paths against an allowed root.

Public-product safety default: all file access is restricted to the allowed
root unless explicitly overridden via unsafe mode.
"""

import os
from pathlib import Path


class SandboxError(PermissionError):
    """Raised when a path is outside the allowed root."""
    pass


def resolve_path(path: str, allowed_root: str | None) -> str:
    """
    Resolve a path to its canonical form and verify containment.

    Args:
        path: Raw path from the agent (absolute or relative).
        allowed_root: Allowed root directory. None = unsafe mode
                     (explicit developer override, not for production).

    Returns:
        Resolved canonical path string.

    Raises:
        SandboxError: If path resolves outside allowed_root.
    """
    if allowed_root is None:
        # UNSAFE MODE — explicit developer override.
        # Resolves symlinks but enforces no containment.
        # Not suitable for production or untrusted agents.
        return str(Path(path).resolve())

    allowed = Path(allowed_root).resolve()

    # Relative paths resolve inside allowed_root
    if not os.path.isabs(path):
        path = str(allowed / path)

    resolved = Path(path).resolve()

    # Containment via commonpath — handles prefix-matching edge cases
    # that a simple startswith() would miss (e.g. /home/foo vs /home/foobar)
    common = os.path.commonpath([str(resolved), str(allowed)])
    if common != str(allowed):
        raise SandboxError(
            f"Access denied: path resolves to '{resolved}' "
            f"which is outside allowed root '{allowed}'"
        )

    return str(resolved)
