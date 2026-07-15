"""SHA-256 receipt hashing with self-exclusion rules.

Contract §21: receipt_id excluded from its own digest for receipt schemas;
checksum excluded from its own digest for manifest schemas.
"""

import hashlib
from . import serialization


def compute_receipt_hash(receipt_dict: dict) -> str:
    """Compute SHA-256 of a receipt dict, excluding receipt_id from digest.

    The receipt_id field is excluded from its own digest per §21 self-exclusion.
    """
    content = {k: v for k, v in receipt_dict.items() if k != "receipt_id"}
    canonical = serialization.serialize_to_bytes(content)
    return hashlib.sha256(canonical).hexdigest()


def compute_manifest_hash(manifest_dict: dict) -> str:
    """Compute SHA-256 of a manifest dict, excluding checksum from digest.

    The checksum field is excluded from its own digest per §21 self-exclusion.
    """
    content = {k: v for k, v in manifest_dict.items() if k != "checksum"}
    canonical = serialization.serialize_to_bytes(content)
    return hashlib.sha256(canonical).hexdigest()


def compute_sha256(data: str | bytes) -> str:
    """Compute lowercase hex SHA-256 of string or bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
