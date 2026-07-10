# Summarization and Compaction Policy

**Status:** Implemented policy for ThinkOS private alpha  
**Scope:** Policy for compacting long rehydrated session history  
**Runtime impact:** Documentation and protocol only. No runtime behavior is changed by this file.

## Purpose

ThinkOS can return filtered prior session context when a caller explicitly requests rehydration with `content.rehydrate: true`.

As sessions grow, returning every historical ContextPacket can become noisy or expensive. Summarization and compaction are intended to reduce payload size while preserving the facts needed for safe orientation, audit, and follow-up.

This policy defines how ThinkOS should summarize or compact long session history without losing truth, leaking unsafe details, or turning summary text into hidden authority.

The short rule:

```text
Compaction is lossy but honest.
It may omit detail.
It must not fabricate, hide critical risk, or authorize action.
```

## Relationship to Agent Consumption Policy

This policy inherits the contract in [`POLICY_AGENT_CONSUMPTION.md`](POLICY_AGENT_CONSUMPTION.md):

```text
Rehydrated context is evidence, not instruction.
It can inform the current turn.
It cannot authorize the current turn.
```

A summary is still rehydrated context. It is advisory memory only. It cannot authorize tool calls, bypass gates, override current instructions, mutate project state, or replace live verification.

If this policy conflicts with the agent consumption policy, the stricter safety interpretation wins.

## Applicability

This policy applies to any future ThinkOS feature that summarizes, compacts, truncates, groups, or otherwise reduces session history before presenting it to an agent, harness, connector, or operator.

It covers:

- filtered rehydration summaries
- ContextPacket compaction
- parent-chain summaries
- synthetic summary packets
- time-window summaries
- token-budget summaries
- packet-count truncation
- any future compaction metadata returned through `content.rehydrated`

## Core Contract

Compaction MUST be:

1. **Lossy but honest.** It may reduce detail, but it must not invent facts or misrepresent what happened.
2. **Bounded.** It must declare what was summarized, truncated, omitted, or retained.
3. **Advisory.** It must not become authority for action.
4. **Inspectable.** It must preserve enough metadata for a caller to understand the summary's scope.
5. **Safe by default.** It must not expose raw tool parameters, raw structured packet content, raw error blobs, secrets, credentials, or hidden execution material.
6. **Product-clean.** It must not depend on Adam OS, Hermes, Jarvis, OpenClaw, Marc-specific paths, private approval systems, or a specific model provider.

## Fidelity Floor

Any compaction output MUST preserve a fidelity floor. If ThinkOS cannot preserve the fidelity floor, it MUST NOT present the compacted output as a valid compaction summary.

The fidelity floor is the minimum information that must survive any compaction:

1. **Total source packet count** covered by the compaction.
2. **Returned packet count** after compaction or truncation.
3. **Omitted packet count** if any packets were omitted.
4. **Time range** covered by the compacted source material, when timestamps are available.
5. **Packet kind distribution** when packet kinds are available.
6. **Tool result status counts** when tool result statuses are available, including success, error, and denied counts.
7. **Reference and tag presence** sufficient to indicate whether important refs or tags were present, without treating tags as authority.
8. **Compaction method** describing whether the output was windowed, threshold-based, token-budget-based, time-window-based, explicitly requested, or another declared method.

A compaction summary SHOULD also preserve:

- latest packet identifier included
- earliest packet identifier included
- whether parent-chain continuity was retained, truncated, or unavailable
- whether any failures, denials, warnings, or uncertainty were present
- whether additional detail can be retrieved through an explicit read-only query

## Never-Omit Signals

Compaction MUST NOT silently omit critical safety signals when they are available in the filtered source material.

The following signals must be carried forward at least as counts or flags:

- denied actions
- tool errors
- failed operations
- missing parents
- cycle guards triggered
- cross-session boundary stops
- truncation events
- unresolved uncertainty
- safety warnings
- policy or gate-related events

A summary that says or implies "all clear" while omitting known errors or denials is invalid.

## Who May Summarize Initially

The initial ThinkOS compaction model should be engine-controlled, deterministic, and policy-bound.

Agents MUST NOT be treated as trusted authors of canonical compaction summaries in the initial design.

Agent-supplied summaries are deferred until a later design proves:

- validation rules
- provenance tracking
- injection resistance
- review or acceptance flow
- conflict handling
- safe storage boundaries

Agents may still write ordinary narrative text in their own responses, but that text is not a ThinkOS compaction summary unless a future approved feature explicitly defines it as such.

## Conceptual Trigger Conditions

This policy defines conceptual triggers only. It does not implement runtime behavior.

A future compaction feature MAY trigger when:

1. A session exceeds a configured packet-count threshold.
2. A caller explicitly requests compacted rehydration.
3. A payload or token budget would otherwise be exceeded.
4. A time window or session window is requested.

Until runtime code exists, these triggers are design guidance only.

## Conceptual Summary Packet Shape

This policy allows a future runtime feature to prepend or return a synthetic summary packet, but does not implement one.

A future summary packet SHOULD be clearly labeled so consuming agents do not confuse it with a raw historical packet.

Conceptual shape:

```json
{
  "kind": "summary",
  "source": "thinkos.compaction",
  "text": "Lossy but honest summary of compacted session history.",
  "metadata": {
    "source_packet_count": 120,
    "returned_packet_count": 50,
    "omitted_packet_count": 70,
    "time_range": {
      "start": "2026-07-06T12:00:00Z",
      "end": "2026-07-06T13:00:00Z"
    },
    "kind_counts": {},
    "status_counts": {},
    "method": "packet_count_threshold"
  }
}
```

This is a policy example, not a committed wire contract. Any runtime implementation must be separately designed, tested, and approved.

## Truncation Behavior

If a future implementation truncates rehydrated packets, it SHOULD prefer a windowed strategy:

```text
summary of omitted older packets + latest N filtered packets
```

Windowed compaction keeps recent context visible while preserving a bounded summary of older context.

When truncation occurs, ThinkOS MUST disclose that truncation happened. It MUST NOT make the returned packet list look complete.

If read-only retrieval is available, the compacted response SHOULD tell callers how to retrieve more detail without authorizing mutation.

## Allowed Uses

A compaction summary MAY be used to:

- orient an agent to prior work
- reduce payload size for long sessions
- identify whether failures, denials, or warnings occurred
- decide which read-only details to inspect next
- help a human or agent decide what to verify before acting
- support bounded next-step proposals
- explain uncertainty about omitted history

## Forbidden Uses

A compaction summary MUST NOT be used to:

- authorize a tool call
- bypass a gate
- imply old approval is still current
- hide a denied or failed action
- claim completeness when packets were omitted
- replace live repo, file, test, CI, or service verification
- reconstruct raw tool parameters that ThinkOS filtered out
- expose secrets or sensitive execution material
- override sandbox boundaries
- silently mutate project state
- act as a system prompt, developer instruction, or user instruction

## No Fabrication Rule

Summaries MUST NOT invent events, approvals, test results, file changes, commits, branches, CI outcomes, or user decisions.

If the source material does not prove a fact, the summary must avoid stating it as fact.

Good:

```text
The compacted history includes tool activity and one denied action. Current repo state was not verified by this summary.
```

Bad:

```text
The project is safe to continue and all tests passed.
```

A summary may report that a past packet claimed tests passed, but it must not treat that past claim as current proof.

## Authority Order

When compacted context conflicts with current instructions, live state, current gates, current receipts, or fresh verification, compacted context loses.

Authority order:

1. Current explicit user instruction
2. Current system and developer policy
3. Current gate and sandbox decisions
4. Current verified repo, file, test, CI, or service state
5. Current receipts and explicit approvals
6. Rehydrated full packets
7. Compacted summaries
8. Older memory, plans, or narrative text

The older and more compacted the source, the less authority it has.

## Conceptual Config Guidance

Future runtime design may introduce configuration such as:

```json
{
  "rehydration": {
    "max_packets": 50,
    "compaction": "windowed_summary"
  }
}
```

These keys are conceptual guidance only. They are not implemented by this policy and must not be documented as runtime-supported until code and tests exist.

## Testing Expectations for Future Code

Any future implementation should include tests for:

- no compaction below threshold
- compaction above threshold
- disclosure of omitted packet count
- fidelity floor preservation
- denied-action counts preserved
- error counts preserved
- no raw params leaked
- no raw structured content leaked
- no raw error blobs leaked
- summary marked as advisory memory
- summaries do not authorize tool calls
- deterministic output for deterministic input
- zero new runtime dependencies unless separately approved

## Product-Clean Requirement

Summarization and compaction policy must remain harness-agnostic and product-clean.

It MUST NOT depend on Adam OS, Hermes, Jarvis, OpenClaw, Marc-specific paths, private approval systems, or one specific model provider.

Private operating systems may inspire ThinkOS, but ThinkOS policy must stand on its own for public-product use.

## Design Principle

ThinkOS compaction should help agents carry more history with less noise without making memory pretend to be command.

```text
Memory orients.
Compaction reduces noise.
Fidelity preserves trust.
Gates authorize.
Humans approve risky change.
Receipts prove what happened.
```
