# P2 REQUEST_UPDATE audit

Audit date: 2026-09-16. Scope: installed `aiomoqt==0.10.6`,
`aiopquic==0.3.11`, and locally built OpenMOQ moqx at
`502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6`. All statements below concern
draft-18 over raw QUIC only.

## Draft-18 baseline

The pinned [draft-18 specification](https://www.ietf.org/archive/id/draft-ietf-moq-transport-18.txt)
requires an update to be sent on the same bidi stream as the request it
modifies (§10.9), consumes a fresh Request ID (§10.1), and receives exactly
one `REQUEST_OK` or `REQUEST_ERROR`. `SUBSCRIBER_PRIORITY` is parameter `0x20`,
is a uint8, may be carried by `REQUEST_UPDATE`, and lower numeric values are
higher priority (§§7, 10.2.7). The draft says a best effort SHOULD apply a
changed priority to not-yet-scheduled Objects; treatment of already scheduled
Objects is implementation-dependent. P2 therefore records control completion
and data-plane reaction separately.

## aiomoqt 0.10.6 audit

| Concern | Exact source / symbol | Finding |
|---|---|---|
| frame codec | `aiomoqt/messages/request.py: RequestUpdate.serialize` / `.deserialize` | implemented; draft-18 omits `existing_request_id` from the wire format |
| priority encoding | `aiomoqt/context.py: PROFILES[MOQTDraft.DRAFT_18]`, `aiomoqt/types.py: ParamType.SUBSCRIBER_PRIORITY` | implemented as fixed uint8 for draft-18 |
| request IDs | `aiomoqt/protocol.py: _allocate_request_id` | fresh, even client IDs increment by two |
| established stream lookup | `aiomoqt/protocol.py: _bidi_streams`, `_bidi_stream_requests` | tracks a request ID to its draft-18 bidi stream |
| send primitive | `aiomoqt/protocol.py: send_stream_message` | serializes a message on a caller-selected bidi stream |
| response handling | `aiomoqt/protocol.py: _handle_bidi_stream`, `_moqt_handle_control_message`, `_handle_request_ok`, `_handle_request_error` | d18 response has no on-wire ID and is injected with the original stream binding |
| public sender | `MOQTSessionQuic` outbound API | **missing**: no `request_update()` method; `_REQUEST_OPENERS` correctly excludes updates because an update must not open a new stream |

### Minimal project-local extension

[`aiomoqt_d18_update.py`](../src/moq_360_protocol_lab/aiomoqt_d18_update.py)
adds `send_subscriber_priority_update()` and `wait_for_update_response()`.
It does not patch the installed wheel. It uses the installed `RequestUpdate`
codec and `send_stream_message()` on the original `SUBSCRIBE` stream, allocates
a fresh request ID, and registers the response future under the existing stream
binding. aiomoqt currently exposes the original request ID to a d18 response,
so the extension permits at most one outstanding update **per subscription**.
That limitation is explicit rather than silently guessing response ownership;
different subscriptions can update independently.

`tests/test_aiomoqt_d18_update.py` verifies:

1. use of the existing bidi stream and a fresh request ID;
2. rejection of overlapping same-stream updates;
3. correlation of the response using the original stream binding; and
4. a draft-18 wire round-trip that proves omission of legacy
   `existing_request_id` and correct uint8 priority encoding.

The isolated relay smokes below make the wrapper relay-qualified for P2.  The
wrapper also registers each response future before emitting its frame: this
was required because two responses on separate subscription streams can arrive
before a sequential waiter begins awaiting the second one.  An initial B→A
attempt that timed out locally despite two relay replies is preserved as
invalid harness evidence; the pre-registration fix and its regression test
produced the valid rerun cited below.

## moqx / moxygen audit

| Concern | Exact source / symbol | Finding |
|---|---|---|
| receiver dispatch | `moxygen/MoQSession.h: MoQSession::onRequestUpdate` and `requestUpdate` | moxygen exposes a draft-18 update receiver path and request-stream reply context |
| subscription state | `moxygen/MoQSession.h: PublisherImpl::setSubPriority` and `handleSubscribeRequestUpdate` | source interface explicitly contains an update path that can mutate subscriber priority |
| downstream update stats | `moqx/src/stats/MoQStatsCollector.cpp: SubscriberCallback::onRequestUpdate` | counter exists; it is aggregate instrumentation, not an event-level timestamp |
| update forwarding | `moqx/src/MoqxRelay.cpp: doSubscribeUpdate`, `doNewGroupRequestUpdate`; relay cross-exec handle wrappers | relay has update forwarding paths for Forward and NEW_GROUP_REQUEST |
| known unsupported request types | `moqx/src/MoqxRelay.cpp: NamespaceSubscription::requestUpdate`, `TracksSubscription::requestUpdate`; `MoqxCache.cpp` | namespace, subscribe-tracks, and cached-fetch updates have explicit limitations; these are outside P2's direct SUBSCRIBE case |
| upstream priority policy | `moqx/src/MoqxRelay.cpp: makeUpstreamSubReq` | relay sets `kDefaultUpstreamPriority`; this agrees with draft-18's allowance not to directly use downstream priority upstream |

The source interfaces are sufficient to justify a direct-SUBSCRIBE P2 smoke,
but do **not** prove that a dynamic priority reaches the downstream scheduler
in this exact binary. The required next proof is a static-priority control and
a native update smoke under a controlled downstream bottleneck. No scheduler
source was modified for this audit.

## P2 qualification evidence

All traffic was deterministic synthetic Objects over the relay; endpoint
processes did not exchange Object data locally. The controlled relay→subscriber
egress was 19.2 Mbps TBF plus 5 ms netem delay, separately validated at
19.594 Mbps goodput with 0% ICMP loss and 5.095 ms mean RTT.

| Operation | Evidence | Result |
|---|---|---|
| equal static control | `results/P2/run_20260916T030328Z_40620409` | 480/480 integrity; equal per-track result |
| A high, B low | `results/P2/run_20260916T030458Z_e5f3236b` | A median/p95 56.5/98.9 ms; B 4.61/6.03 s |
| B high, A low | `results/P2/run_20260916T030726Z_4e3c836e` | B median/p95 56.8/106.4 ms; A 4.63/6.08 s |
| dynamic A→B | `results/P2/run_20260916T031350Z_f799275a` | 2/2 REQUEST_OK; first B completion 34.84 ms after switch; 480/480 integrity |
| dynamic B→A | `results/P2/run_20260916T033223Z_006c52fa` | 2/2 REQUEST_OK; first A completion 8.37 ms after switch; 480/480 integrity |

Every listed run records requested and negotiated draft 18, raw QUIC, ALPN
`moqt-18`, relay log, and provenance. Therefore `relay_p2_smoke` is now
`smoke_verified` for this pinned local environment only. This does not qualify
P3–P5 or claim behavior beyond this two-track, controlled-bottleneck P2
workload.

## Isolated-run topology

`scripts/run_p2_experiment.sh` deliberately starts separate Python endpoint
processes with `ip netns exec`: the publisher in `moq-p2-pub` connects to
`10.253.1.1`, while the subscriber in `moq-p2-sub` connects to `10.253.2.1`.
The pinned relay runs in `moq-p2-relay`, and `tc` is attached only to its
`p2rels0` egress.  The endpoints share only an on-disk, deterministic object
manifest and a host-wide `CLOCK_MONOTONIC` source anchor; they never exchange
payloads through a Python queue.  This fixes the common invalid arrangement
where both client sessions accidentally originate in one namespace and thus
bypass the intended downstream bottleneck.
