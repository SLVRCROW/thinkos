"""Tools package — tool registry."""

TOOL_REGISTRY = {}


def register_tool(name, adapter):
    TOOL_REGISTRY[name] = adapter


def get_tool(name):
    return TOOL_REGISTRY.get(name)


def list_tools():
    return list(TOOL_REGISTRY.keys())
