# P3 delivery-timeout capability audit

Audit date: 2026-09-16.  Scope: strict `draft-ietf-moq-transport-18` over
raw QUIC with ALPN `moqt-18`, `aiomoqt==0.10.6`,
`aiopquic==0.3.11`, and OpenMOQ moqx at
`502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6`.  This audit follows the frozen
P2 core evidence in `docs/P2_COMPLETION_AUDIT.md`; it does not alter P1/P2
raw artifacts.

## Decision

**P3A Object Delivery Timeout: CONDITIONALLY RUNNABLE.**  The selected relay
has a native timer and native QUIC `RESET_STREAM(DELIVERY_TIMEOUT)` action.
aiomoqt can serialize the d18 subscriber parameter, but supplies no named
P3 API or reset-observation callback.  A small project-local adapter extension
may set the existing `ParamType.DELIVERY_TIMEOUT` parameter and observe the
received QUIC reset.  It must never cancel producer tasks, discard payloads,
or emulate expiry.

**P3B Subgroup Delivery Timeout: BLOCKED.**  moqx/moxygen declares and
wire-validates key `0x06`, but its effective-timeout lookup only reads key
`0x02`.  There is no subgroup-expiry timer or subgroup-level reset path.
Sending `0x06` would therefore be a codec/interoperability exercise rather
than a valid native P3B measurement.  Do not run P3B or label it supported.

## Draft-18 requirements

Draft-18 section 8 defines Object and Subgroup Delivery Timeout as optional
time limits in milliseconds; zero means no timeout.  The publisher advertises
the relevant Track Property and the subscriber may request it as a Track
Request Parameter.  The smaller non-zero value applies.  For Object timeout,
the forwarding publisher starts retention at the first payload byte and, on
expiry before committing the underlying stream, resets that stream with
`DELIVERY_TIMEOUT`; retransmission and new subgroup streams are constrained.
For Subgroup timeout, timing starts only after the publisher knows all
subgroup objects are available (or receives FIN), then the underlying stream
must be reset if not committed.  These distinct starts/actions are material:
P3B cannot be inferred from Object behavior.

## Wire and implementation audit

| Mechanism | d18 wire parameter | aiomoqt 0.10.6 | moqx/moxygen pinned build | Observable native action | Result |
|---|---:|---|---|---|---|
| Object Delivery Timeout | `0x02` | `ParamType.DELIVERY_TIMEOUT=0x02`; `MOQTSessionQuic.subscribe(..., parameters=...)` serializes it, but has no P3-named helper | `TrackRequestParamKey::OBJECT_DELIVERY_TIMEOUT=2`; `getDeliveryTimeoutIfPresent()` reads it; `MoQDeliveryTimer` arms at object stream-send and calls reset callback with `ResetStreamErrorCode::DELIVERY_TIMEOUT=0x2` | QUIC `StreamReset` reaches aiomoqt, but stock aiomoqt silently cleans it up | CONDITIONAL PASS |
| Subgroup Delivery Timeout | `0x06` | No `ParamType.SUBGROUP_DELIVERY_TIMEOUT` or public helper; generic integer-parameter codec can encode it | Enum and REQUEST_OK validation mention `SUBGROUP_DELIVERY_TIMEOUT=6`, but `getDeliveryTimeoutIfPresent()` compares only key `DELIVERY_TIMEOUT` (`0x02`); no subgroup timer implementation found | None | BLOCKED |

### Exact local evidence

- `aiomoqt/types.py`: only `ParamType.DELIVERY_TIMEOUT = 0x02` is exposed.
- `aiomoqt/protocol.py`: `subscribe()` accepts `parameters`; its `StreamReset`
  branch logs then calls `_cleanup_stream` without exposing stream ID/error
  code to an application callback.
- `/home/fil/.cache/moqx/cpm/moxygen/a2c1/moxygen/MoQTypes.h`: aliases Object
  timeout to key 2 and declares Subgroup timeout key 6.
- `/home/fil/.cache/moqx/cpm/moxygen/a2c1/moxygen/MoQSession.cpp`:
  `getDeliveryTimeoutIfPresent()` examines only key 2; the track publisher
  passes its effective timeout into `MoQDeliveryTimer`.
- `/home/fil/.cache/moqx/cpm/moxygen/a2c1/moxygen/events/MoQDeliveryTimer.cpp`:
  expiry invokes `streamResetCallback_(ResetStreamErrorCode::DELIVERY_TIMEOUT)`.
- The local `MoQDeliveryTimerTests` executable passed its five timer unit
  tests.  This is build evidence only, not end-to-end P3 evidence.

## Required P3A acceptance evidence

1. Both endpoint negotiation JSON files must record exactly draft 18,
   raw QUIC, and `moqt-18`.
2. A no-timeout control must complete without a native delivery-timeout reset.
3. An Object-timeout treatment must record at least one received QUIC reset
   whose error code is `0x02`, with the relay log retained.
4. Object receipt/missing results must be derived from the deterministic
   manifest and native receiver callbacks only.  No application producer
   cancellation, application payload dropping, receiver ignore rule, or
   sleep-based expiry is permitted.
5. If (3) is absent, the run is `COMPLETED_INVALID` for a P3 protocol claim,
   even if loss/missing objects occur for another reason.

## Non-capabilities and next step

No native P3B result may be generated from this stack.  A future P5 only
makes sense after an implementation has a real Subgroup Delivery Timeout
timer/reset path; it is not a substitute for P3B evidence here.

## First controlled execution (2026-09-16)

The no-timeout control is valid:
`results/P3/run_20260916T042601Z_03a5a93d`.  It negotiated strict d18/raw
QUIC/`moqt-18` at both endpoints, delivered 1,280/1,280 Objects, recorded
zero stream resets, and retained relay/provenance evidence.

The 50 ms Object timeout treatment is retained but **invalid for a protocol
claim**: `results/P3/run_20260916T042629Z_6b52ee91`.  It proves the native
path was invoked (subscriber recorded two `RESET_STREAM` events with error
`0x02`, 1,277/1,280 Objects arrived), but the relay log also contains
`Failed to create uni stream` and `CrossExecFilter beginSubgroup failed`.
Those resource failures confound attribution of the three missing Objects.
The finalizer therefore records `COMPLETED_INVALID` and
`valid_for_protocol_claim=false`; this is classified as a relay/native
implementation limitation exposed by the 8-track fine-cadence workload, not
as a delivery-timeout measurement.
