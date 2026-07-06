"""Tests for context packet schema validation."""

import uuid
from thinkos.schema.context_packet import (
    ContextPacket, validate, validate_packet_id, serialize, deserialize,
    check_cycle, SCHEMA_VERSION, VALID_KINDS, MAX_DAG_DEPTH
)


def _make_valid_packet(**overrides) -> ContextPacket:
    p = ContextPacket(
        packet_id=f"ctx_{uuid.uuid4()}",
        session_id="sess_test",
        timestamp="2026-07-06T12:00:00Z",
        kind="observation",
        source="test",
        content={"text": "hello world", "structured": None},
    )
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


class TestValidatePacketId:
    def test_valid_uuid(self):
        pid = f"ctx_{uuid.uuid4()}"
        assert validate_packet_id(pid) == []

    def test_missing_prefix(self):
        assert validate_packet_id("not_ctx_1234") != []

    def test_invalid_uuid(self):
        assert validate_packet_id("ctx_not-a-uuid") != []


class TestValidate:
    def test_valid_packet(self):
        p = _make_valid_packet()
        assert validate(p) == []

    def test_wrong_schema_version(self):
        p = _make_valid_packet(schema_version=99)
        errs = validate(p)
        assert any("schema_version" in e for e in errs)

    def test_invalid_kind(self):
        p = _make_valid_packet(kind="invalid_kind")
        errs = validate(p)
        assert any("kind" in e for e in errs)

    def test_empty_content_text(self):
        p = _make_valid_packet(content={"text": "", "structured": None})
        errs = validate(p)
        assert any("content.text" in e for e in errs)

    def test_missing_timestamp(self):
        p = _make_valid_packet(timestamp="")
        errs = validate(p)
        assert any("timestamp" in e for e in errs)

    def test_invalid_parent_id(self):
        p = _make_valid_packet(parent_id="not_ctx")
        errs = validate(p)
        assert any("parent_id" in e for e in errs)


class TestCycleDetection:
    def test_no_cycle_with_null_parent(self):
        p = _make_valid_packet(parent_id=None)
        assert check_cycle(p, set()) is False

    def test_direct_cycle_detected(self):
        pid = f"ctx_{uuid.uuid4()}"
        p = _make_valid_packet(packet_id=pid, parent_id=pid)
        assert check_cycle(p, set()) is True


class TestSerializeDeserialize:
    def test_roundtrip(self):
        p = _make_valid_packet()
        data = serialize(p)
        p2 = deserialize(data)
        assert p2.packet_id == p.packet_id
        assert p2.kind == p.kind
        assert p2.content["text"] == p.content["text"]

    def test_compact_json(self):
        p = _make_valid_packet()
        data = serialize(p)
        assert "\n" not in data
        assert "  " not in data
