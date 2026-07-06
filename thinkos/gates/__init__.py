"""Gates package — gate registry."""

GATE_REGISTRY = {}


def register_gate(name, gate):
    GATE_REGISTRY[name] = gate


def get_gate(name):
    return GATE_REGISTRY.get(name)


def list_gates():
    return list(GATE_REGISTRY.keys())
