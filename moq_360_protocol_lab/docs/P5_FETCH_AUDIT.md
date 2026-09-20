# P5 native FETCH and late-join audit

## Scope and conclusion

This audit is tied to `draft-ietf-moq-transport-18`, `aiomoqt==0.10.6`,
`aiopquic==0.3.11`, and OpenMOQ moqx commit
`502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6`. The protocol path is raw QUIC
with the sole ALPN `moqt-18`.

The stack has native live SUBSCRIBE, Standalone Fetch, Relative Joining Fetch,
and relay-side historical retention. Both FETCH forms traversed moqx in the P5
smokes. No Python cache, replay, second connection, disk injection, or custom
relay behavior is used. The controlled P5A/P5B measurements are not yet
results: executing the existing network-namespace topology requires root.

The normative reference is the versioned
[draft-18 text](https://datatracker.ietf.org/doc/html/draft-ietf-moq-transport-18),
especially Sections 10.7, 10.12, and 11.3.

| Capability | Draft-18 | aiomoqt wire | Python API | moqx | Native behavior | Runnable |
|------------|----------|--------------|------------|------|-----------------|----------|
| Live SUBSCRIBE | SUBSCRIBE delivers newly published Objects; historical delivery uses FETCH | `Subscribe`, request-stream send, `SubgroupHeader`/`ObjectHeader` receive | `MOQTSessionQuic.subscribe()` and `on_object_received` | Native subscription/forwarder path | Joining smoke received 19 current-Group Objects on the live path | Yes |
| Standalone FETCH | Type `0x1`; Full Track Name plus inclusive Start and exclusive End Location; End Object `0` requests the whole end Group | `messages/fetch.py:Fetch`; response on request bidi; `FetchHeader` data stream | `fetch()`, `on_fetch_object`, `await_fetch_done()` | `MoqxRelay::fetchImpl` and `MoqxCache::fetch` | Smoke fetched Group 1, Objects 0–39, from relay retention | Yes |
| Joining FETCH | Relative type `0x2` and Absolute type `0x3`; references an Established/Pending forward SUBSCRIBE | Both types in `FetchType`; `Fetch` carries Joining Request ID and Joining Start | `join()` sends LATEST_OBJECT SUBSCRIBE plus referenced FETCH | `fetchOnSubscriberExec`, `resolveJoiningFetch`, then normal fetch path | Relative Joining Fetch smoke transitioned from 21 fetched to 19 live Objects | Yes; Relative tested, Absolute not characterized |
| Historical Group fetch | FETCH stream is ordered by requested Group Order; End Location is exclusive | FETCH data stream callback plus FIN completion future | Standalone range fields exposed | Cache serves hits and can fetch misses upstream with writeback | Exact deterministic identities and payloads passed | Yes |
| Relay retention/cache | Caching is implementation-specific; draft-18 does not require a relay to cache | No endpoint cache is used | None added | `MoqxCache`; enabled, 100 Tracks, 3 Groups/Track, 16 MiB default, 86400-second default/hard duration | S1 succeeded after Group 1 was fully published | Yes for tested window |
| FETCH/live continuity | Joining range ends at the referenced subscription's Joining Location; ranges must not overlap | Separate FETCH and live callbacks retain source attribution | `join()` keeps one session and two native requests | Joining range is resolved against the local forwarder/subscription | Current Group was complete: 21 fetched-only, 19 live-only, 0 duplicates, 0 gaps | Yes |

## Draft-18 mechanism details

- A Standalone Fetch carries Request ID, Fetch Type, Track Namespace, Track
  Name, Start Location, End Location, and request parameters. The End Location
  is one beyond the requested range; End Object `0` means the entire end Group.
- A Relative Joining Fetch carries the referenced subscription Request ID and
  `Joining Start`. Its start is Group `Joining Location.Group - Joining Start`,
  Object `0`; its end is one Object beyond the subscription Joining Location.
  P5 uses `Joining Start = 0`.
- Each draft-18 request opens its own bidirectional stream. Client-originated
  Request IDs are even and increase by two. FETCH data arrives on one
  unidirectional stream beginning with Fetch Header type `0x5` and Request ID.
- Success is `FETCH_OK`. Draft-18 uses the generic `REQUEST_ERROR` response for
  a failed request; the `FetchError`/`FETCH_ERROR` names still present in parts
  of aiomoqt and moxygen are legacy or internal API terminology and are not
  treated as a draft-18 wire result by P5.
- Normal completion is the FETCH data-stream FIN after all returned Objects.
  Empty results still require a Fetch Header followed by FIN. P5 records both
  `fetch_ok` and `fetch_stream_fin`.
- Draft-18 cancellation uses stream cancellation (`STOP_SENDING` on the request
  stream and, when present, the FETCH data stream). aiomoqt exposes legacy
  `FetchCancel` code but no public, verified draft-18 cancellation operation.
  P5 does not cancel FETCH and makes no cancellation claim.

## Exact implementation mapping

### aiomoqt 0.10.6

- `.venv/lib/python3.14/site-packages/aiomoqt/messages/fetch.py`:
  `Fetch`, `FetchOk`, `FetchError`, and `FetchCancel`; `Fetch.serialize()` and
  `deserialize()` implement the Standalone/Relative Joining/Absolute Joining
  request fields.
- `.venv/lib/python3.14/site-packages/aiomoqt/types.py`: `FetchType`.
- `.venv/lib/python3.14/site-packages/aiomoqt/protocol.py`:
  `MOQTSessionQuic.join()`, `fetch()`, `fetch_ok()`, `_handle_fetch()`,
  `_admit_fetch_stream()`, `on_fetch_object`, and `await_fetch_done()`.
  `join()` sends a LATEST_OBJECT SUBSCRIBE and an immediately following
  Joining Fetch referencing that SUBSCRIBE Request ID on the same session.
- `.venv/lib/python3.14/site-packages/aiomoqt/messages/track.py`:
  `FetchHeader` and `FetchObject` are the FETCH data-stream codec.

The released control-plane implementation is usable for draft-18. Its FETCH
data codec was not: it used QUIC varints where draft-18 uses `vi64`, and decoded
later locations as absolute values instead of draft-18 deltas. Payloads above
63 bytes therefore could not interoperate reliably with moqx. The narrowly
scoped project adapter in `src/moq_360_protocol_lab/aiomoqt_d18_fetch.py`
corrects only `FetchHeader`/`FetchObject` serialization and parsing when the
profile is draft 18. It is installed only by the P5 endpoint process. It does
not implement storage, range selection, joining policy, or replay.

Tests in `tests/test_aiomoqt_d18_fetch.py` cover a multi-byte Request ID, a
300-byte `vi64` payload, ascending Group deltas, and same-Group Object deltas.
`tests/test_p5_runner.py` covers Object Status handling and P5 set/continuity
metrics.

### moqx at the pinned commit

- `.relay-src/moqx/src/MoqxRelay.cpp`: `MoqxRelay::fetch()`,
  `fetchOnSubscriberExec()`, and `fetchImpl()`. Joining requests are resolved
  through `resolveJoiningFetch()` and converted to a concrete Standalone range.
  Resolved requests use the normal upstream/cache fetch path.
- `.relay-src/moqx/src/MoqxCache.h` and `MoqxCache.cpp`:
  `MoqxCache::fetch()`, `fetchImpl()`, `fetchUpstream()`, `FetchWriteback`,
  cached interval/gap tracking, byte accounting, and eviction.
- `.relay-src/moqx/src/MoqxRelay.cpp`: the passive cache consumer attached to
  published Tracks retains arriving Objects even before the late subscriber.
- `.relay-src/moqx/src/config/ConfigResolver.cpp:resolveCacheConfig()`:
  absent size/duration fields resolve to 16 MiB and 86400 seconds; Group and
  Track limits come from the selected relay config.
- `configs/relay.moqx.p2.netns.draft18.yaml` and
  `configs/relay.moqx.draft18.example.yaml`: cache enabled,
  `max_tracks: 100`, `max_groups_per_track: 3`, and strict draft 18.

The historical source in the two smokes was the native moqx cache. The Python
publisher remained live and paced each Object against its scheduled source
timestamp; future Groups were not published early.

## Native smoke evidence

`P5-S1` valid run `run_20260918T144854Z_dc6eac7e` issued one Standalone Fetch
for Group 1 with `(start_group,start_object)=(1,0)` and
`(end_group,end_object)=(1,0)`. moqx returned all 40 deterministic Objects
(125,000 payload bytes), the payload check passed, and the FETCH stream reached
FIN. First Object and complete-Group latency after demand were 4.610 ms and
25.251 ms in this localhost semantics smoke.

`P5-S2` valid run `run_20260918T145044Z_e32007ea` used Relative Joining Fetch
with `Joining Start = 0`. Its current Group completed 470.540 ms after demand:
21 Objects/65,625 bytes came only from FETCH and 19 came only from live
SUBSCRIBE. There were no duplicated or missing identities and measured
live-edge delay at completion was 0 ms.

These localhost numbers demonstrate native behavior, not controlled network
performance. P5A/P5B use the namespace topology and 40-ms configured RTT.
