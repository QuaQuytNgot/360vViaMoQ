# Design and capability audit

## Research boundary

The evaluated protocol is **`draft-ietf-moq-transport-18`** (12 May 2026),
over raw QUIC. It is an Internet-Draft, not an RFC. The normative source is
the [versioned IETF text](https://www.ietf.org/archive/id/draft-ietf-moq-transport-18.html).
The lab does not call this version “latest MOQT”.

The primary Python backend is `Moqt18Backend`, which creates an aiomoqt raw
QUIC client with `supported_drafts=18`. A live run is allowed to proceed only
when its probe records all of:

```text
requested_draft = 18
negotiated_draft = 18
transport = raw_quic
alpn = moqt-18
success = true
```

Any missing, different, or unobservable value becomes
`ABORTED_PROTOCOL_NEGOTIATION`; the harness does not offer or accept drafts
14/16, moq-lite, WebTransport, or a permissive default as a substitute.

`MoqLiteBackend` remains for historical/sanity comparisons. It records
`protocol_family = moq-lite`, `wire_protocol = moq-lite-05`, and
`claim_scope = moq_lite_only`; its output is not combined with draft-18 data.

## What the framework owns

```text
experiment config + capability ledger
                 |
                 v
  backend audit + exact protocol probe ---- failed ---> retained aborted result
                 |
                 v
  deterministic manifest + fixed-anchor pacer
                 |
                 v
  verified native client adapter <---- raw-QUIC draft-18 relay
                 |
                 v
  normalized observations -> metrics / provenance / validity decision
```

Python owns configuration, synthetic payload planning, pacing, event/result
normalization, multi-user demand scaffolding, metrics, and provenance. A relay
owns forwarding, cache/reuse, backpressure, and fan-out. Neither is emulated by
the other. No browser/WebTransport behavior, encoded-video decoding, ABR,
viewport prediction, optimization policy, or custom scheduler is in scope.

## Evidence model

There are three distinct kinds of evidence:

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| Source/API audit | a specific released binding contains a symbol or wire codec | relay interoperability or native behavior |
| Protocol probe | a single client/relay connection negotiated exactly `moqt-18` over raw QUIC | publication, subscription, scheduling, cache, or timeout behavior |
| Mechanism smoke | a pinned relay and binding completed a narrowly specified native behavior | performance beyond that smoke's conditions |

The JSON capability ledger is a conservative gate, not a product feature list.
An installed dependency version mismatch, missing public API, absent smoke, or
failed probe prevents live execution of the affected P test.

## Current implementation status

The latest PyPI release found during the audit was `aiomoqt==0.10.6`, resolved
with `aiopquic==0.3.11` and Python 3.12+. Focused released-source draft-18
loopback tests passed (15 tests); details and source symbols are in
[AIOMOQT_API_AUDIT.md](AIOMOQT_API_AUDIT.md).

The repository now contains validated native evidence for P1, P2, and the
supported P4 cases. P3 remains a partial characterization because the current
implementation does not expose every timeout behavior; P4C is blocked by the
current relay. P5 has passed native Standalone and Relative Joining FETCH
semantic smokes. Its controlled single-/multi-Track matrices remain pending
because this session cannot enter the root-owned network namespaces. See
[P5_FETCH_AUDIT.md](P5_FETCH_AUDIT.md).

The original [relay audit](RELAY_AUDIT.md) records the selection process. The
subsequent P1–P5 evidence uses moqx at the fixed commit shown above with
explicit draft-18-only configuration; no relay default is treated as proof.

## P1–P5 experiment boundary

| Test | Purpose | Native prerequisite | Current state |
|---|---|---|---|
| P1 | multi-Track scheduling and completion skew | raw-QUIC P1 pub/sub/relay smoke | complete |
| P2 | priority change reaction | conformant `REQUEST_UPDATE` and scheduling observations | core complete |
| P3 | object/subgroup delivery lifetime | native configuration plus expiry/reset observations | partial; implementation-limited |
| P4 | Forward, pre-warm, and multi-user fan-out | relay Forward/cache/fan-out evidence | P4A/B/D complete; P4C relay-blocked |
| P5 | live subscribe, FETCH, Joining Fetch | raw-QUIC draft-18 relay smoke | native smokes complete; controlled matrices pending root |

The former T1–T9 matrix is retired; the project does not silently map a P test
to a different old mechanism.

## Result and clock rules

`manifest.jsonl` contains expected objects. `events.jsonl` is append-only and
authoritative when a native adapter emits observations. The normalized object
schema carries run/user/tile/track/group/object identity, scheduled and actual
publish timestamps, receipt/completion timestamps, payload size, completion,
drop/expiry/reset/reordering flags, and provenance.

All local runtime timestamps use `time.monotonic_ns`. `actual_publish_ts_ns`
means handed to the adapter unless native evidence defines a later boundary.
Independent host monotonic clocks are never subtracted without a captured
offset and uncertainty. A missing tile makes cross-tile skew unavailable, not
zero.

P1 reports aggregate/per-Track/weakest goodput, object and Group completion,
cross-tile Group completion skew, p50/p95/p99 completion latency, and an
optional application deadline miss ratio. P2 separately records control
response latency and effective scheduling reaction latency. P3 keeps protocol
delivery lifetime distinct from the application/media deadline.

## Claim-scope provenance

Every run records protocol family, wire protocol, draft identifier,
implementation/version, aiomoqt/aiopquic versions, selected draft, transport,
relay identity/version/commit/build flags, and `claim_scope`. The draft-18
record includes:

```text
evaluated_protocol = draft-ietf-moq-transport-18
protocol_family = moqt
wire_protocol = moqt-18
transport_mode = raw_quic
claim_scope = draft18_native  # only after an exact successful probe
```

`valid_for_protocol_claim` is true only after exact negotiation, native
support for the selected P test, successful saved mechanism smoke, and normal
provenance capture. Failed and invalid runs are retained for debugging.
