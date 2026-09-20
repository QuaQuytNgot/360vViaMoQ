# P1–P5 controlled test plan

Live tests are evidence-gated. A skip or block is a correct outcome when the
named draft-18 mechanism is absent; it is never replaced with a moq-lite or
application-level approximation.

| Test | Question | Required observations | Gate today |
|---|---|---|---|
| P1 | Does splitting equivalent aggregate traffic across independent Tracks create completion skew or unfairness? | per-Track/aggregate goodput, weak track, Group/Object completion, skew, p50/p95/p99, deadline misses | complete |
| P2 | How quickly does a native priority update affect media scheduling? | generated/sent/response/effective/first-new-priority timestamps | core complete; extended sweep incomplete |
| P3 | How does native MOQT delivery lifetime differ from the media deadline? | completed/expired/reset/useful/stale bytes, skew, deadline misses | partial; current implementation limits documented |
| P4 | Can verified Forward/cache/fan-out reduce useful-object latency and duplicate upstream data? | first useful object/Group, pre-demand/wasted bytes, cache/reuse/link bytes, fan-out | P4A/B/D complete; P4C blocked by current relay |
| P5 | What is the behavior of live subscribe, FETCH, and Joining Fetch? | first Object/complete Group, historical/redundant bytes, continuity and live-edge delay | native Standalone and Relative Joining FETCH smokes pass; controlled P5A/P5B pending root execution |

## Core invariants

1. Each logical media Group uses one fixed scheduled source time across tiles.
2. The live pacer never emits a future Group early; lateness is recorded.
3. Expected objects come from the manifest. An absent receipt is not converted
   into loss, reset, expiration, or success.
4. Cross-tile skew and viewport completion require every required tile.
5. A configured application playback deadline is not a MOQT timeout.
6. Network shaping records direction; one outbound `tc netem` delay is not RTT.

## Required smoke sequence before any sweep

Only these small checks are appropriate now:

| Smoke | Procedure | Current result |
|---|---|---|
| A | strict draft-18 raw-QUIC handshake | passed against pinned moqx |
| B | one publisher, one subscriber, one Track, ten deterministic Groups; verify sequence and payload, clean close | passed; post-P5 regression also passed |
| C | four synthetic Tracks at equal priority | passed in P1 qualification |
| D | one priority swap with P2-native support | passed; see `P2_COMPLETION_AUDIT.md` |
| P5-S1 | native Standalone Fetch for a retained complete Group | passed |
| P5-S2 | native Relative Joining Fetch plus live transition | passed |

Do not run bandwidth/loss/RTT sweeps until these are saved against a pinned
real relay.

## P5 gate and matrix

P5-S1 and P5-S2 are documented in
[P5_FETCH_AUDIT.md](P5_FETCH_AUDIT.md). They prove native relay-mediated
Standalone Fetch and Relative Joining Fetch semantics with deterministic
payload validation. They are localhost semantic smokes, not P5A/P5B network
measurements.

The controlled matrix reuses the `moq-p2-pub`, `moq-p2-relay`, and
`moq-p2-sub` namespaces. `run_p5_matrix.sh` executes serially and stops on the
first failed or invalid run. P5A contains 1 Track × 3 modes × 5 offsets × 3
repetitions. P5B contains 4 Tracks × 3 modes × 3 offsets × 3 repetitions.
Exact setup and cleanup commands are in [P5_RESULTS.md](P5_RESULTS.md).

## Status meanings

- `PLANNED_SYNTHETIC`: deterministic plan only; not a protocol result.
- `COMPLETED_SYNTHETIC`: local pacing ran; not a protocol result.
- `REPLAY_READY`: event analysis chosen; no new protocol traffic.
- `ABORTED_PROTOCOL_NEGOTIATION`: draft 18/raw QUIC/`moqt-18` was not observed.
- `SKIPPED_UNVERIFIED`: a version, native mechanism, or saved smoke is missing.
- `SKIPPED_UNIMPLEMENTED`: conditions passed but no native adapter is registered.
- `FAILED_RUNTIME`: a verified path failed; it is not relabelled unsupported.

## Future P1 factor choices

Once B passes, run 1, 2, 4, 8, 16, and 24 Tracks as explicitly configured
scenarios, maintaining constant total offered bitrate where the chosen
configuration permits it. Rates, Group duration, object size, duration, and
network conditions remain experiment configuration—not Python defaults.
