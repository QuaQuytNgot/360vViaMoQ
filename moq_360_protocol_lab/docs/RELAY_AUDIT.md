# Relay audit

## Selected relay — qualified for P1 and P2

| Field | Evidence |
|---|---|
| implementation | OpenMOQ [moqx](https://github.com/openmoq/moqx) |
| pinned commit | `502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6` |
| local binary | `.relay-src/moqx/build/default/moqx` (`v0.3.5-15-g502b6b8f`) |
| build | `MOQX_BUILD_JOBS=1 ./build.sh` after the pinned moxygen source fallback and system dependencies |
| configuration | `configs/relay.moqx.draft18.example.yaml` |
| transport / ALPN | raw QUIC / `moqt-18` |
| requested / negotiated draft | 18 / 18 |
| strict probe | passed; no draft-16, draft-14, WebTransport, or moq-lite fallback |
| 1-Track smoke | passed: 10/10 Objects, byte/identity checks pass — `results/P1/run_20260915T165233Z_13673576` |
| 4-Track smoke | passed: 40/40 Objects, byte/identity checks pass — `results/P1/run_20260915T165508Z_5c231a94` |
| P2 isolated static/dynamic smoke | passed: two-track 19.2 Mbps downstream bottleneck; symmetric static priority controls and native A→B/B→A updates — `docs/P2_REQUEST_UPDATE_AUDIT.md` |

The probe output is saved in each measurement's publisher/subscriber protocol
negotiation files. It records `requested_draft: 18`, `negotiated_draft: 18`,
`transport: raw_quic`, `alpn: moqt-18`, and `success: true`.

## Build and configuration record

The checkout was shallow-cloned at the selected commit. Its dependency pin for
moxygen is `70d28f5e796018efb0f5138d538ac7388df72f4c`. The machine's original
CMake 3.22.1 was below the source requirement; project-local CMake 3.31.10 and
Ninja 1.13.2 were used. The moxygen prebuilt failed its loader check
(`moqtest_client` exit 127), so the documented source fallback was built using
one job to avoid memory pressure. The required Linux development packages were
installed with the candidate's supplied dependency installer.

Validation command:

```bash
.relay-src/moqx/build/default/moqx validate-config \
  --config configs/relay.moqx.draft18.example.yaml
```

It returned `Config is valid.` The listener is loopback-only and explicitly
sets `moqt_versions: [18]`. TLS is `insecure: true` solely for local loopback
testing; it is not an acceptable remote deployment configuration.

## 24-Track qualification issue and resolution

The first 24-Track attempt timed out at the 17th SUBSCRIBE. It was not a draft
or aiomoqt interoperation failure: the pinned moqx configuration documentation
and `src/config/Config.h` set `max_bidi_streams` to 16 by default, while this
aiomoqt workload uses one client-initiated bidirectional subscription stream
per Track. Relay logs showed the first 16 requests were forwarded and no reply
for the 17th.

This was classified as a **relay configuration issue**. The audited local
listener now explicitly sets `quic.max_bidi_streams: 64`; it passed config
validation and a fresh strict probe. Re-running the exact 24-Track case passed
with 720/720 Objects and integrity checks: `results/P1/run_20260915T170520Z_00ca2719`.
This raises a QUIC resource limit only; it does not change MOQT draft,
ALPN, transport, priority, request-update behavior, or scheduling semantics.

## Known limitations

- The moqx published config documentation at this pinned revision still says
  versions 14 and 16 even though the locally built implementation successfully
  negotiated explicit version 18. The runtime negotiation evidence is
  authoritative for this qualification.
- P1 is loopback Series A only. Series B was skipped: the repository has no
  isolated namespace/veth shaping setup, and changing the host primary
  interface would not be safe or reproducible.
- These results qualify the candidate for native P1 and the narrowly defined
  P2 subscriber-priority/REQUEST_UPDATE path only. They do not qualify P3–P5.

## Other candidates

MOQtail, imquic, and xquic were not built because the pinned moqx candidate
passed the strict probe and complete P1 path. No relay was selected merely for
ease of use, and no alternative draft was tried.
