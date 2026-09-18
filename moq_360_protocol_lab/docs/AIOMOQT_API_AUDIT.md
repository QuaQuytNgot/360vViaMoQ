# aiomoqt 0.10.6 API audit

## Audit basis

The newest installable PyPI release inspected on 2026-09-15 was
`aiomoqt==0.10.6`, requiring Python 3.12+. Its resolved transport package was
`aiopquic==0.3.11`. The audit used the released wheel—not a moving Git branch—
and an isolated Python 3.14 environment. The package’s focused draft-18 test
set passed: `test_loopback_d18_setup.py`, `test_loopback_d18_objects.py`,
`test_multi_version_handshake.py`, and `test_d18_data_plane.py` (**15 passed**).

Raw QUIC is selected with:

```python
MOQTClient(host, port, path=path, use_quic=True, supported_drafts=18)
```

`supported_drafts=18` is a singleton offer, so the raw-QUIC ALPN offer is only
`moqt-18`; it does not permit draft-16, draft-14, or moq-lite fallback. The
backend requires both `session.negotiated_draft == 18` and the TLS ALPN value
`moqt-18`; otherwise it writes failed negotiation evidence and aborts live
execution.

For the pinned `aiopquic==0.3.11` build, the probe reads the raw connection's
`_negotiated_alpn(None)` observation and checks it against the draft selected
from aiomoqt's `ProtocolNegotiated` event. This is intentionally a narrow,
version-pinned probe dependency: if that observation disappears or differs,
the run aborts rather than assuming the offered ALPN was negotiated.

## Required-mechanism matrix

“Not audited” means exactly that; raw QUIC is the only research transport in
scope and no result below claims WebTransport or relay behavior. “Wire test”
refers to the released package tests named above, not a real-relay P1–P5
smoke.

| Mechanism | Wire type | Serializer / parser source | Public Python API | Raw QUIC evidence | WebTransport evidence | Relay evidence | Current conclusion |
|---|---|---|---|---|---|---|---|
| Track/Subgroup | yes | `messages/track.py: SubgroupHeader` | `publish`, `subscribe`, `subgroup_header` | d18 loopback objects | not audited/unused | no selected relay | source-ready; P1 smoke pending |
| Priority | yes | draft profile parameter codec | `subscribe(priority=...)`; publish subgroup priority | API/loopback path | not audited/unused | no scheduler smoke | initial values only |
| `REQUEST_UPDATE` | yes | `messages/request.py: RequestUpdate` | project-local d18 stream wrapper | wire test | not audited/unused | two-direction P2 relay smoke | smoke-verified for P2 |
| `SUBSCRIBER_PRIORITY` | yes | `types.py: ParamType.SUBSCRIBER_PRIORITY`; `context.py: DraftProfile` | initial subscribe plus project-local update wrapper | dynamic wire test | not audited/unused | static/dynamic P2 relay smoke | smoke-verified for P2 |
| `OBJECT_DELIVERY_TIMEOUT` | yes (`0x02`) | `types.py: ParamType.DELIVERY_TIMEOUT` | no | no native behavior test | not audited/unused | no selected relay | blocked |
| `SUBGROUP_DELIVERY_TIMEOUT` | draft-18 `0x06` | absent in audited source | no | no | not audited/unused | no selected relay | blocked |
| initial `FORWARD` | yes (`0x10`) | draft parameter codec | `publish(... forward=...)`, `subscribe(... forward=...)` | no d18 relay behavior smoke | not audited/unused | candidate only | incomplete |
| dynamic Forward | via `REQUEST_UPDATE` | update codec only | no conformant sender | no | not audited/unused | no selected relay | blocked |
| `FETCH` | yes | request codec | `MOQTSessionQuic.fetch` | no saved d18 relay smoke | not audited/unused | no selected relay | optional/blocked |
| Joining Fetch | yes | request codec | `MOQTSessionQuic.join` | no saved d18 relay smoke | not audited/unused | no selected relay | optional/blocked |

## P1

| Item | Source evidence | Status |
|---|---|---|
| raw-QUIC draft-18 handshake | `aiomoqt/client.py: MOQTClient.connect`; `aiomoqt/protocol.py: MOQTSessionQuic.client_session_init`; `tests/test_loopback_d18_setup.py` | wire smoke passed |
| subscribe | `aiomoqt/protocol.py: MOQTSessionQuic.subscribe` | public API; draft-18 request bidi stream source path |
| publish | `aiomoqt/protocol.py: MOQTSessionQuic.publish`; `aiomoqt/track.py: PublishedTrack.publish` | public API |
| subgroup/object delivery | `aiomoqt/protocol.py: subgroup_header`; `aiomoqt/messages/track.py: SubgroupHeader.next_object`; `tests/test_loopback_d18_objects.py` | wire smoke passed |
| initial priority | `subscribe(priority=...)`; `SubgroupHeader(... publisher_priority=...)` | public API; no relay scheduling conclusion yet |

## P2

| Item | Wire type | Serializer/parser | Public method | Raw QUIC test | Result |
|---|---|---|---|---|---|
| `REQUEST_UPDATE` | yes, `messages/request.py: RequestUpdate` | yes, draft-18 omits `existing_request_id` | `aiomoqt_d18_update.send_subscriber_priority_update()` | explicit d18 wire test | P2 two-direction relay smoke passed |
| `SUBSCRIBER_PRIORITY` | yes, `types.py: ParamType.SUBSCRIBER_PRIORITY` | yes, uint8 under `context.py: DraftProfile` | initial `subscribe(priority=...)` plus update wrapper | explicit d18 wire test | static/dynamic P2 relay smoke passed |

Draft-18 requires `REQUEST_UPDATE` to travel on the **same bidi stream** as the
request it updates and requires exactly one response. In the audited source,
`MOQTSessionQuic._REQUEST_OPENERS` excludes `RequestUpdate`, and no public
`request_update()` method exists. Calling `send_control_message()` would use the
wrong stream. The project-local wrapper sends the codec object through
`send_stream_message()` on the established subscription stream, with a fresh
request ID. It permits only one in-flight update per subscription because the
released response demux exposes the stream's original request ID. See
`docs/P2_REQUEST_UPDATE_AUDIT.md` and `tests/test_aiomoqt_d18_update.py`.
The end-to-end isolated relay smoke now passes in both directions; see
`docs/P2_REQUEST_UPDATE_AUDIT.md`. This is a project-local, pinned extension,
not a claim that aiomoqt itself exposes a public `request_update()` method.

## P3

| Item | Wire type | Serializer/parser | Public method | Native behavior test | Result |
|---|---|---|---|---|---|
| `OBJECT_DELIVERY_TIMEOUT` | draft-18 property/parameter `0x02` | `ParamType.DELIVERY_TIMEOUT` | no native timeout API | none | blocked |
| `SUBGROUP_DELIVERY_TIMEOUT` | draft-18 property/parameter `0x06` | absent from audited public source | no | none | blocked |

The draft requires native reset/drop behavior; application timers are not a
substitute. No dependency patch has been made. Any later patch belongs under
`third_party/patches/` with a wire and behavior test.

## P4

| Item | Wire type | Serializer/parser | Public method | Relay status | Result |
|---|---|---|---|---|---|
| initial `FORWARD` | parameter `0x10` | yes | `publish(... forward=...)`, `subscribe(... forward=...)` | unverified under draft-18 | incomplete |
| dynamic Forward | `REQUEST_UPDATE` parameter | codec only | no conformant sender | unverified | blocked |
| cache/fan-out | relay behavior, not a client field | n/a | n/a | candidate source audit only | blocked |

## P5

| Item | Wire type | Serializer/parser | Public method | Raw QUIC test | Result |
|---|---|---|---|---|---|
| standalone `FETCH` | yes | yes | `MOQTSessionQuic.fetch` | no saved draft-18 relay smoke | optional/blocked |
| Joining Fetch | yes | yes | `MOQTSessionQuic.join` | no saved draft-18 relay smoke | optional/blocked |

The released package contains loopback FETCH/JOIN tests, but their helper
defaults to draft-14. That is useful source coverage but is not sufficient to
call P5 a draft-18 relay experiment.
