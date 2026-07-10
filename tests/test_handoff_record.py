"""TM009 v0 tests for bounded, evidence-only handoff records."""

import uuid

import pytest

from thinkos.schema.context_packet import ContextPacket
from thinkos.schema.handoff_record import (
    HandoffRecord,
    deserialize,
    serialize,
    validate,
)
from thinkos.schema.receipt import Action, Receipt, Result
from thinkos.store.sqlite_store import (
    DuplicateError,
    HandoffReferenceError,
    SQLiteStore,
)


def _id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4()}"


def _record(**changes) -> HandoffRecord:
    values = {
        "handoff_id": _id("hof_"),
        "source_session_id": "session-source",
        "target_session_id": "session-target",
        "source_agent": "agent-a",
        "target_agent": "agent-b",
        "timestamp": "2026-07-10T03:00:00Z",
        "purpose_summary": "Continue the bounded task using referenced evidence.",
    }
    values.update(changes)
    return HandoffRecord(**values)


def _packet(session_id: str = "session-source", **changes) -> ContextPacket:
    values = {
        "packet_id": _id("ctx_"),
        "session_id": session_id,
        "timestamp": "2026-07-10T02:00:00Z",
        "content": {"text": "evidence", "structured": None},
    }
    values.update(changes)
    return ContextPacket(**values)


def _receipt(session_id: str = "session-source", sequence: int = 1) -> Receipt:
    return Receipt(
        receipt_id=_id("rct_"),
        session_id=session_id,
        sequence=sequence,
        timestamp="2026-07-10T02:01:00Z",
        action=Action(agent="agent-a"),
        result=Result(summary="evidence recorded"),
    )


@pytest.fixture
def store():
    value = SQLiteStore(":memory:")
    yield value
    value.close()


def test_valid_record_passes_validation():
    assert validate(_record()) == []


@pytest.mark.parametrize("handoff_id", ["bad", "hof_not-a-uuid", "ctx_" + str(uuid.uuid4())])
def test_invalid_handoff_id_rejected(handoff_id):
    assert any("handoff_id" in error for error in validate(_record(handoff_id=handoff_id)))


def test_source_and_target_sessions_must_differ():
    errors = validate(_record(target_session_id="session-source"))
    assert "target_session_id must differ from source_session_id" in errors


@pytest.mark.parametrize("field", ["source_agent", "target_agent", "purpose_summary"])
def test_required_text_fields_reject_empty_values(field):
    assert any(field in error for error in validate(_record(**{field: ""})))


@pytest.mark.parametrize("field", ["timestamp", "expires_at"])
def test_timestamps_must_be_timezone_aware(field):
    assert any(field in error for error in validate(_record(**{field: "2026-07-10T03:00:00"})))


def test_utf8_byte_cap_counts_multibyte_text():
    assert any(
        "purpose_summary" in error
        for error in validate(_record(purpose_summary="界" * 683))
    )


def test_packet_reference_cap_is_enforced():
    packet_ids = [_id("ctx_") for _ in range(26)]
    assert any("25" in error for error in validate(_record(packet_ids=packet_ids)))


def test_receipt_reference_cap_is_enforced():
    receipt_ids = [_id("rct_") for _ in range(51)]
    assert any("50" in error for error in validate(_record(receipt_ids=receipt_ids)))


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_omitted_packet_count_must_be_non_negative_integer(value):
    assert any("omitted_packet_count" in error for error in validate(
        _record(omitted_packet_count=value)
    ))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_policy", "trusted"),
        ("authority_transfer", "delegated"),
        ("requires_fresh_approval", False),
        ("requires_fresh_approval", 1),
    ],
)
def test_non_authority_invariants_are_fixed(field, value):
    assert any(field in error for error in validate(_record(**{field: value})))


def test_tags_are_bounded_and_unique():
    assert any("10" in error for error in validate(_record(tags=[str(i) for i in range(11)])))
    assert any("64" in error for error in validate(_record(tags=["界" * 22])))
    assert any("duplicate" in error for error in validate(_record(tags=["same", "same"])))


def test_reference_lists_reject_duplicates_and_wrong_prefixes():
    packet_id = _id("ctx_")
    errors = validate(_record(packet_ids=[packet_id, packet_id], receipt_ids=["ctx_wrong"]))
    assert any("duplicate" in error for error in errors)
    assert any("rct_" in error for error in errors)


def test_serialization_round_trip_preserves_record():
    record = _record(
        expires_at="2999-01-01T00:00:00Z",
        omitted_packet_count=2,
        omissions_summary="Two packets omitted.",
        tags=["handoff"],
    )
    assert deserialize(serialize(record)) == record


def test_write_and_read_handoff_round_trip(store):
    packet = _packet()
    receipt = _receipt()
    store.write_packet(packet)
    store.write_receipt(receipt)
    record = _record(packet_ids=[packet.packet_id], receipt_ids=[receipt.receipt_id])

    store.write_handoff(record)

    assert store.read_handoff(record.handoff_id) == record


def test_write_handoff_rejects_invalid_schema(store):
    with pytest.raises(ValueError, match="evidence_policy"):
        store.write_handoff(_record(evidence_policy="trusted"))


def test_duplicate_handoff_is_rejected(store):
    record = _record()
    store.write_handoff(record)
    with pytest.raises(DuplicateError):
        store.write_handoff(record)


def test_missing_packet_reference_fails_closed(store):
    with pytest.raises(HandoffReferenceError, match="does not exist"):
        store.write_handoff(_record(packet_ids=[_id("ctx_")]))


def test_missing_receipt_reference_fails_closed(store):
    with pytest.raises(HandoffReferenceError, match="does not exist"):
        store.write_handoff(_record(receipt_ids=[_id("rct_")]))


def test_packet_from_wrong_session_fails_closed(store):
    packet = _packet("another-session")
    store.write_packet(packet)
    with pytest.raises(HandoffReferenceError, match="source session"):
        store.write_handoff(_record(packet_ids=[packet.packet_id]))


def test_receipt_from_wrong_session_fails_closed(store):
    receipt = _receipt("another-session")
    store.write_receipt(receipt)
    with pytest.raises(HandoffReferenceError, match="source session"):
        store.write_handoff(_record(receipt_ids=[receipt.receipt_id]))


def test_write_handoff_does_not_modify_referenced_evidence(store):
    packet = _packet()
    receipt = _receipt()
    store.write_packet(packet)
    store.write_receipt(receipt)
    before_packet = store.read_packet(packet.packet_id)
    before_receipt = store.read_receipt(receipt.receipt_id)

    store.write_handoff(_record(
        packet_ids=[packet.packet_id], receipt_ids=[receipt.receipt_id]
    ))

    assert store.read_packet(packet.packet_id) == before_packet
    assert store.read_receipt(receipt.receipt_id) == before_receipt


def test_list_handoffs_filters_orders_and_limits(store):
    first = _record(
        target_agent="agent-b", timestamp="2026-07-10T01:00:00Z"
    )
    second = _record(
        target_agent="agent-b", timestamp="2026-07-10T02:00:00Z"
    )
    other_agent = _record(
        target_agent="agent-c", timestamp="2026-07-10T03:00:00Z"
    )
    other_session = _record(
        target_session_id="different-target", timestamp="2026-07-10T04:00:00Z"
    )
    for record in [first, second, other_agent, other_session]:
        store.write_handoff(record)

    records = store.list_handoffs_for_target("session-target")
    assert [r.handoff_id for r in records] == [
        other_agent.handoff_id, second.handoff_id, first.handoff_id
    ]
    assert store.list_handoffs_for_target(
        "session-target", target_agent="agent-b", limit=1
    ) == [second]


@pytest.mark.parametrize("limit", [0, -1])
def test_list_handoffs_non_positive_limit_returns_empty(store, limit):
    store.write_handoff(_record())
    assert store.list_handoffs_for_target("session-target", limit=limit) == []


def test_resolve_handoff_returns_exact_references_without_dag_expansion(store):
    parent = _packet()
    child = _packet(parent_id=parent.packet_id)
    unreferenced = _packet()
    receipt = _receipt()
    for packet in [parent, child, unreferenced]:
        store.write_packet(packet)
    store.write_receipt(receipt)
    record = _record(packet_ids=[child.packet_id], receipt_ids=[receipt.receipt_id])
    store.write_handoff(record)

    resolved = store.resolve_handoff(record.handoff_id)

    assert resolved["record"] == record
    assert [p.packet_id for p in resolved["packets"]] == [child.packet_id]
    assert [r.receipt_id for r in resolved["receipts"]] == [receipt.receipt_id]


def test_resolve_handoff_rechecks_missing_references(store):
    packet = _packet()
    store.write_packet(packet)
    record = _record(packet_ids=[packet.packet_id])
    store.write_handoff(record)
    store._conn.execute("DELETE FROM packets WHERE packet_id = ?", (packet.packet_id,))
    store._conn.commit()

    with pytest.raises(HandoffReferenceError, match="does not exist"):
        store.resolve_handoff(record.handoff_id)


def test_resolve_handoff_rechecks_session_membership(store):
    packet = _packet()
    store.write_packet(packet)
    record = _record(packet_ids=[packet.packet_id])
    store.write_handoff(record)
    store._conn.execute(
        "UPDATE packets SET session_id = ? WHERE packet_id = ?",
        ("another-session", packet.packet_id),
    )
    store._conn.commit()

    with pytest.raises(HandoffReferenceError, match="source session"):
        store.resolve_handoff(record.handoff_id)


@pytest.mark.parametrize(
    ("expires_at", "expected"),
    [("2000-01-01T00:00:00Z", True), ("2999-01-01T00:00:00Z", False)],
)
def test_resolve_handoff_reports_advisory_expiry(store, expires_at, expected):
    record = _record(expires_at=expires_at)
    store.write_handoff(record)
    assert store.resolve_handoff(record.handoff_id)["expired"] is expected
    assert store.read_handoff(record.handoff_id) == record


def test_store_exposes_no_handoff_update_or_delete_methods(store):
    assert not hasattr(store, "update_handoff")
    assert not hasattr(store, "delete_handoff")
