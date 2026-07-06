"""ConfirmGate — asks for write approval, allows reads."""

import sys


class ConfirmGate:
    name = "confirm"

    # Tools considered read-only (no approval needed)
    READ_TOOLS = {"read_file"}

    def evaluate(self, tool_name: str, params: dict) -> dict:
        if tool_name in self.READ_TOOLS:
            return {"action": "allow", "reason": "Read tool, no approval needed.", "ask_prompt": None}

        # Write tool — ask for approval
        path = params.get("path", "unknown")
        prompt = f"Agent wants to write to '{path}'. Allow? (y/N): "
        sys.stdout.write(prompt)
        sys.stdout.flush()
        try:
            response = sys.stdin.readline().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return {"action": "deny", "reason": "No response received (EOF/interrupt).", "ask_prompt": None}

        if response in ("y", "yes"):
            return {"action": "allow", "reason": "User approved via interactive prompt.", "ask_prompt": None}
        else:
            return {"action": "deny", "reason": "User declined via interactive prompt.", "ask_prompt": None}
