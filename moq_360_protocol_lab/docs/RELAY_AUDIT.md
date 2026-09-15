# Relay audit

This audit selects no relay yet. A candidate must pass a locally built,
pinned, raw-QUIC draft-18 handshake and media-path smoke before it is used for
a protocol claim.

| Relay | Draft-18 handshake | Basic subscribe | Priority | `REQUEST_UPDATE` | Delivery timeout | Forward | Cache | Fan-out | Instrumentation | Build difficulty | Selected? |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aiomoqt interop relay | package-local only | suitable for loopback/basic smoke | not audited for relay scheduling | not suitable | not suitable | not suitable | no research evidence | no research evidence | application logs only | low | no — smoke utility only |
| OpenMOQ moqx `502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6` | source has opt-in paths; not locally smoked | source unit coverage; not locally smoked | source dependent | source update paths; client P2 still blocked | reset metrics/source references; no draft-18 behavior smoke | source code present | `MoqxCache` source present | `MoQForwarder` source present | Prometheus metrics plus mlog/qlog | high: CMake/C++20/Ninja and pinned moxygen | no |
| MOQtail | interop registry reports draft-18 endpoint; source audit not completed | not locally verified | not audited | not audited | not audited | not audited | not audited | not audited | not audited | medium (Rust) | no |
| imquic | interop registry reports drafts 16–18; source audit not completed | not locally verified | not audited | not audited | not audited | not audited | not audited | not audited | not audited | medium (C) | no |
| xquic | interop registry reports draft-18; source audit not completed | not locally verified | not audited | not audited | not audited | not audited | not audited | not audited | not audited | high (C/C++) | no |

## moqx source facts

The checkout was shallow-cloned at `502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6`.
`cmake/dependencies.cmake` pins OpenMOQ moxygen at
`70d28f5e796018efb0f5138d538ac7388df72f4c`. `src/MoqxCache.h` describes
cache write-back/fetch coalescing; `MoqxRelay.cpp` and the relay tests contain
Forward and `REQUEST_UPDATE` paths; `docs/metrics.md` names subscription,
update, payload, QUIC, and reset counters.

However, `src/config/ConfigResolver.cpp` keeps default versions at `14,16` and
comments draft-18 as opt-in/not yet interoperable. The published config
reference also says supported versions are 14 and 16. That conflict means a
source-code presence check is insufficient. The candidate remains unselected
until the exact pinned build succeeds with `moqt_versions: [18]`, negotiates
only `moqt-18`, and completes the required P1 media-path smoke.

## Selection evidence required

1. Save the relay remote, commit, moxygen pin, binary version, compiler/CMake
   versions, and build flags.
2. Start only a listener configured with `moqt_versions: [18]`.
3. Run `scripts/probe_relay.sh` and save `protocol_negotiation.json` showing
   draft 18, raw QUIC, and ALPN `moqt-18`.
4. Complete 10 deterministic Groups from one publisher through the relay to
   one subscriber, checking sequence and payload bytes.
5. Only then mark `relay_p1_smoke` `smoke_verified`. P2–P5 each need their
   own mechanism-specific evidence.
