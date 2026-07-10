"""Engine — core dispatch loop."""

import uuid
from datetime import datetime, timezone
from thinkos.schema.context_packet import ContextPacket, validate as validate_packet, serialize as serialize_packet
from thinkos.schema.receipt import Receipt, Action, Result, GateInfo, validate as validate_receipt, serialize as serialize_receipt
from thinkos.config import resolve_gate, get_allowed_root
from thinkos.store.sqlite_store import DepthError


class Engine:
    """Core dispatch loop: parse message → resolve tool → evaluate gate → execute → record receipt."""

    def __init__(self, store, connector, tool_registry, gate_registry, config):
        self.store = store
        self.connector = connector
        self.tool_registry = tool_registry
        self.gate_registry = gate_registry
        self.config = config
        self._sequence = 0
        self._last_packet_id: dict[str, str | None] = {}

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _make_receipt(self, session_id: str, action_type: str, tool: str | None,
                      params: dict | None, agent: str, result_status: str,
                      result_summary: str, packet_ids: list, error: str | None,
                      gate_name: str | None, gate_decision: str | None,
                      gate_reason: str | None) -> Receipt:
        rid = f"rct_{uuid.uuid4()}"
        now = datetime.now(timezone.utc).isoformat()
        gate = None
        if gate_name:
            gate = GateInfo(gate_name=gate_name, decision=gate_decision, reason=gate_reason)
        return Receipt(
            receipt_id=rid,
            session_id=session_id,
            sequence=self._next_sequence(),
            timestamp=now,
            action=Action(type=action_type, tool=tool, params=params, agent=agent),
            result=Result(status=result_status, summary=result_summary, packet_ids=packet_ids, error=error),
            gate=gate,
        )

    def run(self):
        while True:
            msg = self.connector.read_message()
            if msg is None:
                break  # EOF

            session_id = msg.get("session_id", "default")
            sender = msg.get("sender", "unknown")
            tool_calls = msg.get("content", {}).get("tool_calls", [])
            response_text = ""
            tool_results = []
            context_packets = []
            receipt_ids = []

            # Opt-in session rehydration
            rehydrated_data = None
            if msg.get("content", {}).get("rehydrate", False):
                try:
                    packets, receipts = self.store.rehydrate(session_id)
                    rehydrated_data = {
                        "session_id": session_id,
                        "status": "ok",
                        "packet_count": len(packets),
                        "receipt_count": len(receipts),
                        "packets": [
                            {
                                "id": p.packet_id,
                                "kind": p.kind,
                                "source": p.source,
                                "parent_id": p.parent_id,
                                "refs": p.refs,
                                "tags": p.tags,
                                "summary": (p.content.get("text") or "")[:500],
                            }
                            for p in packets
                        ],
                        "receipts": [
                            {
                                "id": r.receipt_id,
                                "status": r.result.status,
                                "tool": r.action.tool,
                                "created_at": r.timestamp,
                            }
                            for r in receipts
                        ],
                    }
                except Exception:
                    rehydrated_data = {
                        "session_id": session_id,
                        "status": "error",
                        "packet_count": 0,
                        "receipt_count": 0,
                        "packets": [],
                        "receipts": [],
                    }

            # All-or-nothing tool call limit check
            max_calls = self.config.get("limits", {}).get("max_tool_calls_per_message", 10)
            if max_calls and len(tool_calls) > max_calls:
                receipt = self._make_receipt(
                    session_id, "tool_call", "tool_call_limit",
                    {"tool_call_count": len(tool_calls),
                     "max_tool_calls_per_message": max_calls},
                    sender, "denied",
                    f"Message exceeds maximum of {max_calls} tool calls",
                    [],
                    f"Message contained {len(tool_calls)} tool calls, limit is {max_calls}",
                    None, None, None,
                )
                self.store.write_receipt(receipt)
                receipt_ids.append(receipt.receipt_id)
                response_text = (
                    f"Message rejected: exceeds maximum of {max_calls} tool calls "
                    f"(got {len(tool_calls)}). Zero tools executed."
                )
            else:
                for tc in tool_calls:
                    tool_name = tc.get("tool", "")
                    params = tc.get("params", {})
                    call_id = tc.get("call_id", "")

                    # Resolve tool
                    tool_adapter = self.tool_registry.get(tool_name)
                    if tool_adapter is None:
                        receipt = self._make_receipt(
                            session_id, "tool_call", tool_name, params, sender,
                            "error", f"Unknown tool: '{tool_name}'", [], f"Unknown tool: '{tool_name}'",
                            None, None, None
                        )
                        self.store.write_receipt(receipt)
                        tool_results.append({"tool": tool_name, "call_id": call_id,
                                             "status": "error", "output": "", "receipt_id": receipt.receipt_id})
                        receipt_ids.append(receipt.receipt_id)
                        continue

                    # Resolve gate
                    try:
                        gate = resolve_gate(tool_name, self.config, self.gate_registry)
                    except ValueError as e:
                        receipt = self._make_receipt(
                            session_id, "tool_call", tool_name, params, sender,
                            "error", str(e), [], str(e), None, None, None
                        )
                        self.store.write_receipt(receipt)
                        tool_results.append({"tool": tool_name, "call_id": call_id,
                                             "status": "error", "output": "", "receipt_id": receipt.receipt_id})
                        receipt_ids.append(receipt.receipt_id)
                        continue

                    # Evaluate gate
                    gate_decision = gate.evaluate(tool_name, params)

                    if gate_decision["action"] == "deny":
                        receipt = self._make_receipt(
                            session_id, "tool_call", tool_name, params, sender,
                            "denied", gate_decision.get("reason", "Denied by gate"), [],
                            gate_decision.get("reason"), gate.name, "deny", gate_decision.get("reason")
                        )
                        self.store.write_receipt(receipt)
                        tool_results.append({"tool": tool_name, "call_id": call_id,
                                             "status": "denied", "output": "", "receipt_id": receipt.receipt_id})
                        receipt_ids.append(receipt.receipt_id)
                        continue

                    elif gate_decision["action"] == "allow":
                        pass  # proceed to tool execution

                    else:
                        raise ValueError(
                            f"Gate '{gate.name}' returned unknown action "
                            f"'{gate_decision['action']}'. Expected 'allow' or 'deny'."
                        )

                    # Execute tool
                    context = {
                        "session_id": session_id,
                        "agent_id": sender,
                        "store": self.store,
                        "allowed_root": get_allowed_root(self.config),
                        "limits": self.config.get("limits", {}),
                    }
                    result = tool_adapter.execute(params, context)

                    receipt = self._make_receipt(
                        session_id, "tool_call", tool_name, params, sender,
                        result.get("status", "ok"), result.get("output", "")[:200],
                        [], result.get("error"), gate.name, "allow",
                        gate_decision.get("reason", "Allowed by gate")
                    )
                    self.store.write_receipt(receipt)

                    # Create a context packet for every successful tool result
                    if result.get("status") == "ok":
                        last_pid = self._last_packet_id.get(session_id)
                        packet = ContextPacket(
                            packet_id=f"ctx_{uuid.uuid4()}",
                            session_id=session_id,
                            parent_id=last_pid,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            kind="tool_result",
                            source="thinkos",
                            content={
                                "text": f"Tool '{tool_name}' completed: {result.get('output', '')[:200]}",
                                "structured": {
                                    "tool": tool_name,
                                    "params": params,
                                    "status": "ok",
                                },
                            },
                            tags=[tool_name],
                            refs=[receipt.receipt_id],
                        )
                        try:
                            self.store.write_packet(packet)
                        except DepthError:
                            # Depth limit reached — retry without parent link
                            packet.parent_id = None
                            self.store.write_packet(packet)
                        self._last_packet_id[session_id] = packet.packet_id
                        context_packets.append(packet.packet_id)

                    tool_results.append({
                        "tool": tool_name,
                        "call_id": call_id,
                        "status": result.get("status", "ok"),
                        "output": result.get("output", ""),
                        "receipt_id": receipt.receipt_id,
                    })
                    receipt_ids.append(receipt.receipt_id)

            # Build response
            response = {
                "type": "agent_response",
                "in_response_to": msg.get("message_id", ""),
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "content": {
                    "text": response_text,
                    "tool_results": tool_results,
                    "context_packets": context_packets,
                    "receipts": receipt_ids,
                }
            }
            if rehydrated_data is not None:
                response["content"]["rehydrated"] = rehydrated_data
            self.connector.write_response(response)
