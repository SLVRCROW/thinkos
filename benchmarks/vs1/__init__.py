"""VS-1 Six-Arm Succession Benchmark — isolated benchmark package.

Isolated from G0/G1 frozen files and product runtime. Implements the
frozen VS-1 protocol (see PROTOCOL_v0.1.0.md). No model, API, or network
calls anywhere in this package.
"""

from .schemas import ARMS, CONDITIONS, ARM_LABELS, SessionEvent
from .adapters import ADAPTERS, BOUNDARIES, get_adapter
from .fixtures import get_fixture, all_fixtures, FixtureSet

__all__ = [
    "ARMS",
    "CONDITIONS",
    "ARM_LABELS",
    "ADAPTERS",
    "BOUNDARIES",
    "get_adapter",
    "get_fixture",
    "all_fixtures",
    "FixtureSet",
    "SessionEvent",
]
