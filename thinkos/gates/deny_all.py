"""DenyAllGate — denies all actions unconditionally."""


class DenyAllGate:
    name = "deny_all"

    def evaluate(self, tool_name: str, params: dict) -> dict:
        return {"action": "deny", "reason": "Gate 'deny_all' blocks all actions.", "ask_prompt": None}
