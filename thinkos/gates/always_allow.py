"""AlwaysAllowGate — allows all actions unconditionally."""


class AlwaysAllowGate:
    name = "always_allow"

    def evaluate(self, tool_name: str, params: dict) -> dict:
        return {"action": "allow", "reason": "Gate 'always_allow' permits all actions.", "ask_prompt": None}
