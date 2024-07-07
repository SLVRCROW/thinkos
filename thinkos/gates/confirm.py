"""ConfirmGate — asks for write approval via TTY, allows reads.

Protocol design
----------------
The confirm gate must NOT read approval from sys.stdin or write prompts to
sys.stdout, because those channels carry JSON-Lines protocol messages between
the engine and the driving agent.  Instead:

  TTY mode (default when a controlling terminal exists):
    - prompt on stderr (clean separation from JSON-Lines stdout)
    - read approval from /dev/tty (the actual human's terminal)
    - y / yes -> allow; anything else -> deny

  Non-TTY mode (no /dev/tty, or THINKOS_NONINTERACTIVE set):
    - read tools: allowed (same as current behaviour)
    - write tools: deny / fail closed -- no prompt, no read attempt
    - THINKOS_NONINTERACTIVE=1|true|yes forces non-TTY even when a TTY exists
"""

import os
import sys

_NONINTERACTIVE_TRUTHY = frozenset({"1", "true", "yes"})


class ConfirmGate:
    name = "confirm"

    # Tools considered read-only (no approval needed)
    READ_TOOLS = {"read_file"}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_noninteractive_forced() -> bool:
        """Return True when the env var THINKOS_NONINTERACTIVE is set to a
        truthy value (1, true, yes)."""
        val = os.environ.get("THINKOS_NONINTERACTIVE", "").strip().lower()
        return val in _NONINTERACTIVE_TRUTHY

    # ------------------------------------------------------------------
    # public interface
    # ------------------------------------------------------------------

    def evaluate(self, tool_name: str, params: dict) -> dict:
        if tool_name in self.READ_TOOLS:
            return {"action": "allow", "reason": "Read tool, no approval needed.", "ask_prompt": None}

        # Write tool -- determine mode
        if self._is_noninteractive_forced():
            return {
                "action": "deny",
                "reason": "Non-interactive mode: write approval unavailable",
                "ask_prompt": None,
            }

        # Try to open /dev/tty -- if it fails we are in non-TTY mode
        try:
            tty = open("/dev/tty", "r")
        except OSError:
            return {
                "action": "deny",
                "reason": "Non-interactive mode: write approval unavailable",
                "ask_prompt": None,
            }

        # TTY mode -- prompt on stderr, read from the already-open tty
        path = params.get("path", "unknown")
        prompt = f"Agent wants to write to '{path}'. Allow? (y/N): "
        sys.stderr.write(prompt)
        sys.stderr.flush()

        try:
            response = tty.readline().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return {"action": "deny", "reason": "No response received (EOF/interrupt).", "ask_prompt": None}
        finally:
            tty.close()

        if response in ("y", "yes"):
            return {"action": "allow", "reason": "User approved via TTY prompt.", "ask_prompt": None}

        return {"action": "deny", "reason": "User declined via TTY prompt.", "ask_prompt": None}
