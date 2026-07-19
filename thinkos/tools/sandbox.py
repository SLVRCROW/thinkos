"""Path sandboxing — resolves and validates paths against an allowed root.

Public-product safety default: all file access is restricted to the allowed
root unless explicitly overridden via unsafe mode.
"""

import os
from pathlib import Path


class SandboxError(PermissionError):
    """Raised when a path is outside the allowed root."""
    pass


def _is_path_within(path: str, root: str, path_module=os.path) -> bool:
    """Return whether *path* is contained by *root* using host path rules.

    Windows raises ``ValueError`` when ``commonpath`` compares different
    drives. Treat that as outside the sandbox rather than leaking an
    unexpected exception. ``normcase`` preserves POSIX behavior while making
    Windows drive letters and path components case-insensitive.
    """
    try:
        common = path_module.commonpath([path, root])
    except ValueError:
        return False
    return path_module.normcase(common) == path_module.normcase(root)


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
    # that a simple startswith() would miss (e.g. /home/foo vs /home/foobar).
    # A Windows drive mismatch is outside the sandbox, not an internal error.
    if not _is_path_within(str(resolved), str(allowed)):
        raise SandboxError(
            f"Access denied: path resolves to '{resolved}' "
            f"which is outside allowed root '{allowed}'"
        )

    return str(resolved)
