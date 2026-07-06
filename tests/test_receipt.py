"""Tests for receipt schema validation."""

import uuid
from thinkos.schema.receipt import (
    Receipt, Action, Result, GateInfo, validate, validate_receipt_id,
    serialize, deserialize, SCHEMA_VERSION
)


def _make_valid_receipt(**overrides) -> Receipt:
    r = Receipt(
        receipt_id=f"rct_{uuid.uuid4()}",
        session_id="sess_test",
        sequence=1,
        timestamp="2026-07-06T12:00:00Z",
        action=Action(type="tool_call", tool="read_file", params={"path": "/tmp/test"}, agent="test"),
        result=Result(status="ok", summary="Read file", packet_ids=[], error=None),
    )
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


class TestValidateReceiptId:
    def test_valid_uuid(self):
        rid = f"rct_{uuid.uuid4()}"
        assert validate_receipt_id(rid) == []

    def test_missing_prefix(self):
        assert validate_receipt_id("not_rct_1234") != []

    def test_invalid_uuid(self):
        assert validate_receipt_id("rct_not-a-uuid") != []


class TestValidate:
    def test_valid_receipt(self):
        r = _make_valid_receipt()
        assert validate(r) == []

    def test_wrong_schema_version(self):
        r = _make_valid_receipt(schema_version=99)
        errs = validate(r)
        assert any("schema_version" in e for e in errs)

    def test_invalid_action_type(self):
        r = _make_valid_receipt()
        r.action.type = "invalid_action"
        errs = validate(r)
        assert any("action.type" in e for e in errs)

    def test_invalid_result_status(self):
        r = _make_valid_receipt()
        r.result.status = "invalid_status"
        errs = validate(r)
        assert any("result.status" in e for e in errs)

    def test_invalid_gate_decision(self):
        r = _make_valid_receipt()
        r.gate = GateInfo(gate_name="confirm", decision="maybe")
        errs = validate(r)
        assert any("gate.decision" in e for e in errs)

    def test_sequence_must_be_positive(self):
        r = _make_valid_receipt(sequence=0)
        errs = validate(r)
        assert any("sequence" in e for e in errs)

    def test_missing_session_id(self):
        r = _make_valid_receipt(session_id="")
        errs = validate(r)
        assert any("session_id" in e for e in errs)


class TestSerializeDeserialize:
    def test_roundtrip(self):
        r = _make_valid_receipt()
        data = serialize(r)
        r2 = deserialize(data)
        assert r2.receipt_id == r.receipt_id
        assert r2.sequence == r.sequence
        assert r2.action.type == r.action.type

    def test_with_gate(self):
        r = _make_valid_receipt()
        r.gate = GateInfo(gate_name="confirm", decision="allow", reason="OK")
        data = serialize(r)
        r2 = deserialize(data)
        assert r2.gate.gate_name == "confirm"
        assert r2.gate.decision == "allow"

    def test_with_supersedes(self):
        r = _make_valid_receipt(supersedes=f"rct_{uuid.uuid4()}")
        data = serialize(r)
        r2 = deserialize(data)
        assert r2.supersedes == r.supersedes
