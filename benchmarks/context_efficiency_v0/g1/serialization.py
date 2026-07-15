"""Strict canonical JSON serialization for G1 receipts.

Contract §21: UTF-8, sorted keys, fixed separators, ensure_ascii=True,
allow_nan=False, stable arrays, reject sets, reject non-string keys,
reject unsupported types directly.
"""

import json
import math
from typing import Any


def _validate_value(obj: Any, path: str) -> None:
    """Recursively validate that obj is serializable per G1 rules.

    Raises ValueError on first violation. This runs inside canonical_json
    so callers do not need a separate validation step.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise ValueError(
                    f"{path}: dict key must be string, got {type(k).__name__}"
                )
            _validate_value(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _validate_value(v, f"{path}[{i}]")
    elif isinstance(obj, set):
        raise ValueError(f"{path}: set is not allowed")
    elif isinstance(obj, float):
        if math.isnan(obj):
            raise ValueError(f"{path}: NaN is not allowed")
        if math.isinf(obj):
            raise ValueError(f"{path}: Infinity is not allowed")
    elif obj is None or isinstance(obj, (bool, int, str)):
        pass
    else:
        raise ValueError(
            f"{path}: unsupported type {type(obj).__name__}"
        )


def canonical_json(obj: Any) -> str:
    """Serialize obj to canonical JSON per G1 contract §21.

    Validates all values inline — rejects non-string dict keys, sets,
    NaN, Infinity, and unsupported types directly. Raises ValueError.
    """
    _validate_value(obj, "<root>")
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def serialize_to_bytes(obj: Any) -> bytes:
    """Serialize to canonical JSON and encode as UTF-8 bytes."""
    return canonical_json(obj).encode("utf-8")


def parse_canonical(data: str | bytes) -> Any:
    """Parse canonical JSON. Rejects non-UTF-8, NaN, Infinity."""
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data, parse_constant=lambda x: (_ for _ in ()).throw(
        ValueError(f"NaN/Infinity not allowed: {x}")
    ))
