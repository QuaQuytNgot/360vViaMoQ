# MOQT 360 Streaming Protocol Characterization

## 1. Objective

This is the authoritative consolidation of the repository's existing P1–P5 evidence. It records what was tested, what is valid, what failed, and what remains unknown. It does not add an experiment, repair historical data, compare newer drafts, claim novelty, or design a solution.

The evidence unit is a run directory. A completed process is not automatically a research result: this report honors each `valid_for_protocol_claim` flag and the stricter per-test gates in `docs/P2_COMPLETION_AUDIT.md`, `docs/P3_COMPLETION_AUDIT.md`, `docs/P4_FORWARD_AUDIT.md`, and `docs/P5_RESULTS.md`.

Evidence accounting is:

- **33 valid research runs:** P1 Series A (6), P2 (8), P3 control (1), and clean P4A/B-Reactive/D (18).
- **20 invalid preserved runs:** finalized `COMPLETED_INVALID` summaries across P1–P5.
- **8 semantic-only smoke runs:** P1 native qualification/regression smokes (3) and valid P5 localhost FETCH/joining smokes (5).
- Separately, the repository retains P4's validation-incomplete exploratory runs (4), a synthetic harness-only P1 run (1), and run directories without a `summary.json` (5).

These counts are derived from `results/P1/run_*/summary.json` through `results/P5/run_*/summary.json`, the run-directory inventory under `results/P1/` through `results/P5/`, and the exclusions documented in `docs/P4_FORWARD_AUDIT.md`.

## 2. Experimental Platform

Stored per-run provenance identifies host `filpc`, kernel `6.8.0-138-generic`, architecture `x86_64`, glibc `2.35`, and total memory `16,207,276 KiB`. It also records an NVIDIA GeForce GTX 1660 with driver `595.91.07`, although the GPU is not in the protocol data path. The CPU model, distribution release name, and physical-NIC model were not captured in the historical provenance; they are therefore **unknown for publication purposes**, rather than retroactively filled from the current host. Source: `results/P1/run_20260915T165654Z_b1c69629/provenance.json` (corroborated by `results/P5/run_20260918T144854Z_dc6eac7e/provenance.json`).

The measurements use `time.monotonic_ns` in one host clock domain. Provenance warns against subtracting independent host clocks without synchronization uncertainty. Source: `results/P1/run_20260915T165654Z_b1c69629/provenance.json`.

The controlled path used Linux namespaces and veth interfaces, not the host's physical NIC. The relay-to-subscriber shaping point was `p2rels0`. Source: `scripts/setup_p2_netns.sh`, `scripts/apply_p2_shaping.sh`, and `docs/P2_REQUEST_UPDATE_AUDIT.md`.

## 3. Protocol and Implementation Stack

| Layer | Evaluated stack | Evidence |
|---|---|---|
| Protocol | `draft-ietf-moq-transport-18`; requested/negotiated draft `18` | per-run `protocol_negotiation*.json`; `results/P1/run_20260915T165654Z_b1c69629/provenance.json` |
| Transport | raw QUIC; ALPN `moqt-18` | same provenance and negotiation artifacts |
| Endpoint | `aiomoqt==0.10.6`; `aiopquic==0.3.11` | `results/P1/run_20260915T165654Z_b1c69629/provenance.json` |
| Runtime | Python `3.14.6`, Anaconda build, GCC `14.3.0` | same provenance |
| Relay | OpenMOQ moqx `v0.3.5-15-g502b6b8f` | `docs/RELAY_AUDIT.md` |
| Relay commit | `502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6` | `docs/RELAY_AUDIT.md`; per-run provenance |
| Repository state | P1–P4 provenance records commit `4b6797f04eee1d883a755a7f758dcc20ee6b89f6`; P5 records `9300fad74e127f67fd814c44f852122255710637`; both dirty | `results/P1/run_20260915T165654Z_b1c69629/provenance.json`; `results/P5/run_20260918T144854Z_dc6eac7e/provenance.json` |

### Relay bidirectional-stream configuration

The first high-track-count attempt stopped at the seventeenth `SUBSCRIBE`: moqx's default `max_bidi_streams` was `16`, and this endpoint uses one client-initiated bidirectional request stream per Track. The listener was explicitly changed to `quic.max_bidi_streams: 64`; the subsequent 24-Track run delivered all 720 expected Objects. This is a **CONFIGURATION LIMIT**, not a MOQT behavior result. Sources: `docs/RELAY_AUDIT.md`, `configs/relay.moqx.draft18.example.yaml`, and `results/P1/run_20260915T170520Z_00ca2719/summary.json`.

## 4. Network Topologies

The common data path was:

```text
Synthetic Publisher
      │  MOQT draft-18 / raw QUIC / moqt-18
      ▼
   moqx Relay
      │  MOQT draft-18 / raw QUIC / moqt-18
      ▼
Subscriber(s)
```

Three environments must not be merged:

| Environment | Tests | Actual conditions | Evidence |
|---|---|---|---|
| Unconstrained loopback experiment | P1 Series A | loopback; bandwidth, RTT, loss, and jitter unset | `results/P1/run_20260915T165654Z_b1c69629/config.yaml` and the other Series A `config.yaml` files |
| Controlled namespace measurement | P2/P3 | publisher, relay, and subscriber namespaces joined by veth pairs; relay egress TBF `19.2 Mbit/s`; configured RTT `5 ms`; loss `0%`; jitter `0 ms` | `configs/p2.switch.a_to_b.draft18.yaml`, `configs/p3.control-no-timeout.draft18.yaml`, `docs/P2_REQUEST_UPDATE_AUDIT.md` |
| Controlled namespace measurement | P4 | same namespace/veth architecture; downstream limit `100 Mbit/s`; configured RTT `40 ms`; loss `0%`; jitter `0 ms` | `configs/p4.forward-smoke.0-to-1.draft18.yaml`, `configs/p4.reactive.8track.draft18.yaml` |
| Localhost semantic smoke | P5 and P1 qualification | loopback/native relay path; used to prove mechanism and integrity, not controlled performance | `configs/p5.smoke.fetch.local.draft18.yaml`, `configs/p5.smoke.join.local.draft18.yaml`, `docs/RELAY_AUDIT.md` |

The independent P2 sanity check measured `19.594 Mbit/s`, `5.095 ms` mean RTT, and `0%` ICMP loss. This qualifies the shaped path but is not an experiment outcome. Source: `docs/P2_REQUEST_UPDATE_AUDIT.md`.

## 5. Synthetic Workload Model

The model maps one spatial tile/logical stream to one MOQT Track, one media or GOP-like interval to one Group, and one temporal payload unit to one Object (or a Subgroup/Object on the wire). The publisher paces Objects against wall-clock scheduled timestamps. Each payload deterministically encodes Track ID, Group ID, Object ID, and seeded content; the subscriber checks identity, missing/duplicate/unexpected Objects, and payload integrity. Sources: `src/moq_360_protocol_lab/workload.py`, `src/moq_360_protocol_lab/pacing.py`, `src/moq_360_protocol_lab/metrics.py`, and P1–P5 effective configs.

This intentionally isolates protocol/implementation behavior from codec, FFmpeg, disk I/O, decoder, and keyframe dependencies. Consequently, a complete synthetic Group is not evidence of a decodable video frame. Source: `docs/DESIGN.md` and `docs/P5_RESULTS.md`.

| Test | Workload | Evidence |
|---|---|---|
| P1 | `24 Mbit/s` aggregate, `1,000 ms` Groups, `30` Groups, `1` Object/Group, Track counts `1/2/4/8/16/24` | Series A `config.yaml`; `results/P1/p1_summary.csv` |
| P2 | `24 Mbit/s`, `2` Tracks, `1,000 ms` Groups, `12` Groups, `20` Objects/Group at `50 ms` cadence | `configs/p2.switch.a_to_b.draft18.yaml` |
| P3 | `24 Mbit/s`, `8` Tracks, `1,000 ms` Groups, `8` Groups, `20` Objects/Group at `50 ms` cadence | `configs/p3.control-no-timeout.draft18.yaml` |
| P4A | `1 Mbit/s`, `1` Track, `500 ms` Groups, `12` Groups, `5` Objects/Group | `configs/p4.forward-smoke.0-to-1.draft18.yaml` |
| P4B/P4D | `8 Mbit/s`, `8` Tracks, `500 ms` Groups, `16` Groups, `5` Objects/Group | `configs/p4.reactive.8track.draft18.yaml`; `configs/p4.fanout.2user.50overlap.draft18.yaml` |
| P5 smoke | `1 Mbit/s`, `1` Track, `1,000 ms` Groups, `40` Objects/Group at `25 ms` cadence | `configs/p5.smoke.fetch.local.draft18.yaml`; `configs/p5.smoke.join.local.draft18.yaml` |

## 6. Experiment Status Overview

| Test | Research Question | Mechanism | Status | Valid Measurements? | Main Limitation |
|---|---|---|---|---|---|
| P1 | Does splitting a fixed workload across more Tracks change completion, skew, weakest-track goodput, or Group completion? | Tracks, subscriptions, subgroup streams | `COMPLETE_BASELINE` | Yes: 6 Series A runs | one repetition/configuration; unshaped loopback; synthetic media |
| P2 | Does native priority and active `REQUEST_UPDATE` change delivery under contention? | subscriber priority; same-stream `REQUEST_UPDATE` | `COMPLETE_CORE`; extended sweep incomplete | Yes: 8 runs | receiver proxy, no relay scheduler timestamp; narrow 2-Track case |
| P3 | What does native delivery timeout do under overload? | Object/Subgroup Delivery Timeout; stream reset | `CHARACTERIZATION_CONCLUDED_PARTIAL` | Valid control only | P3A treatment confounded by relay resource errors; P3B missing in implementation |
| P4 | What do Forward transitions and subscription aggregation do? | `FORWARD`, `REQUEST_UPDATE`, relay fan-out | P4A `COMPLETE`; P4B `PARTIAL_REACTIVE_ONLY`; P4C `BLOCKED_BY_CURRENT_RELAY`; P4D `COMPLETE` | Yes for P4A, Reactive-only P4B, and P4D | Dormant results absent; no inactive-demand prewarm policy |
| P5 | Do native live, Standalone FETCH, and Relative Joining FETCH work and preserve continuity? | `SUBSCRIBE`, `FETCH`, Relative Joining FETCH | `NATIVE_SEMANTICS_COMPLETE`; controlled matrix pending root | Semantic smokes only | no controlled P5A/P5B execution |

Run counts and statuses come from `results/experiment_summary.json`; test-level decisions come from `docs/P2_COMPLETION_AUDIT.md`, `docs/P3_COMPLETION_AUDIT.md`, `docs/P4_FORWARD_AUDIT.md`, and `docs/P5_RESULTS.md`, with the P4B correction described below.

## 7. P1 — Multi-Track Scheduling

### What and why

P1 asks whether dividing a constant aggregate workload among independent Tracks affects aggregate and weakest-track goodput, Object completion latency, cross-Track Group-completion skew, Group completion, or playback-deadline misses. It exercised normal native subscriptions and subgroup data streams; it did not test a special correlated-track scheduler.

### Valid Series A results

| Tracks | Aggregate goodput (Mbit/s) | Weakest Track (Mbit/s) | Median completion (ms) | P95 completion (ms) | Median skew (ms) | P95 skew (ms) | Group completion | Deadline miss |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 22.657 | 22.657 | 41.252 | 73.369 | 0.000 | 0.000 | 1.000 | 0.000 |
| 2 | 22.657 | 11.329 | 33.215 | 66.913 | 6.317 | 6.973 | 1.000 | 0.000 |
| 4 | 22.654 | 5.664 | 27.873 | 258.698 | 12.849 | 16.853 | 1.000 | 0.000 |
| 8 | 22.654 | 2.832 | 25.437 | 225.643 | 12.817 | 17.676 | 1.000 | 0.000 |
| 16 | 22.658 | 1.416 | 24.602 | 219.872 | 11.488 | 20.556 | 1.000 | 0.000 |
| 24 | 22.625 | 0.943 | 26.211 | 224.163 | 11.951 | 34.967 | 1.000 | 0.000 |

Source for every value and run mapping in this table: `results/P1/p1_summary.csv`. Exact unrounded values and run IDs are retained in `results/experiment_summary.json`.

**MEASURED OBSERVATION**

Across the one-run-per-setting Series A, aggregate goodput stayed between `22.625` and `22.658 Mbit/s`, all Groups completed, and P95 skew was `34.967 ms` at 24 Tracks. Source: `results/P1/p1_summary.csv`.

**IMPLEMENTATION INTERPRETATION**

The pinned aiomoqt/moqx stack sustained this fixed aggregate loopback workload after the relay's QUIC stream limit was raised. Weakest-track goodput falls approximately with per-Track offered load because the aggregate is divided among more Tracks; this dataset alone does not identify a scheduling bottleneck.

**PROTOCOL QUESTION**

Does an application with correlated multi-Track completion requirements need scheduling semantics or signalling beyond independent subscription priority?

Limitations: Series A is unshaped loopback, has one repetition per Track count, uses one Object per Group, and contains no real codec. Series B was not run. Source: `results/P1/run_20260915T165654Z_b1c69629/config.yaml`, the other Series A configs, and `docs/RELAY_AUDIT.md`.

## 8. P2 — Dynamic Priority / REQUEST_UPDATE

### What and why

P2 used two equally offered Tracks under a `19.2 Mbit/s` downstream bottleneck against a `24 Mbit/s` aggregate offer. Static controls used equal priority, A-high/B-low, and B-high/A-low. Dynamic runs began with one high-priority Track, switched both priorities at `5,000 ms`, and sent one native `REQUEST_UPDATE` per active subscription on that subscription's existing bidirectional stream. Priority values were `0` (high) and `255` (low); reaction used `500 ms` receiver windows and a `0.70` new-high delivery-share threshold. Source: `configs/p2.static.equal.draft18.yaml`, `configs/p2.static.a_high.draft18.yaml`, `configs/p2.static.b_high.draft18.yaml`, and `configs/p2.switch.a_to_b.draft18.yaml`.

The stock aiomoqt release had the draft-18 codec but no public conformant sender. The project-local wrapper binds the update to the original request stream, allocates a fresh Request ID, permits only one outstanding update per subscription, and does not unsubscribe, reconnect, or resubscribe. Source: `docs/P2_REQUEST_UPDATE_AUDIT.md` and `src/moq_360_protocol_lab/aiomoqt_d18_update.py`.

### Static validation

| Run | Priority | High Track median/P95 (ms) | Low Track median/P95 (ms) | Integrity |
|---|---|---:|---:|---:|
| `run_20260916T030328Z_40620409` | equal | A median `2037.2`; B median `2038.0` | not applicable | 480/480 |
| `run_20260916T030458Z_e5f3236b` | A high | A `56.5/98.9` | B `4.61/6.03 s` | 480/480 |
| `run_20260916T030537Z_840a7a11` | B high | B median `53.9` | A median `4597.2` | 480/480 |
| `run_20260916T030726Z_4e3c836e` | B high | B `56.8/106.4` | A `4.63/6.08 s` | 480/480 |

Sources: medians are derived from each run's `subscriber.csv`; rounded P95 values are the frozen values in `docs/P2_COMPLETION_AUDIT.md`. The second B-high run is the audit's frozen evidence; the earlier valid duplicate remains counted and indexed.

### Dynamic results

| Run/direction | ACK samples (ms) | ACK median (ms) | First new-high completion (ms) | Stable dominance bound (ms) | Old-high after switch | First post-switch 500-ms new-high share | Out of order | Integrity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `f799275a` A→B | 25.628, 28.312 | 26.970* | 34.837 | 500 | 141 Objects / 10,575,000 B | 93.33% | 49 | 480/480 |
| `942d19b5` A→B | 26.717, 28.199 | 27.458 | 36.276 | 500 | 141 Objects / 10,575,000 B | 93.33% | 49 | 480/480 |
| `6dc3294e` A→B | 17.113, 20.044 | 18.578 | 52.950 | 500 | 141 Objects / 10,575,000 B | 93.33% | 50 | 480/480 |
| `006c52fa` B→A | 21.228, 20.546 | 20.887 | 8.370 | 500 | 140 Objects / 10,500,000 B | 93.33% | 50 | 480/480 |

Sources: each run's `request_updates.csv`, `subscriber.events.jsonl`, and `summary.json`. `f799275a`'s marked ACK median is recomputed from its two raw ACK samples; its stored `summary.json` incorrectly repeats the maximum (`28.312 ms`) as the median. The `500 ms` stable value is a conservative two-window receiver bound, not a relay-internal timestamp. The post-switch share is the byte share in the first window anchored at the later update-send timestamp, derived using the same events and window definition as `src/moq_360_protocol_lab/p2_runner.py`.

In the immediately preceding `500 ms` windows, the future-high Track shares were `37.5%`, `40.0%`, `37.5%`, and `26.7%` for the rows above; observed aggregate delivery was `18.0–19.2 Mbit/s`. Sources: the same `request_updates.csv` and `subscriber.events.jsonl` files.

**MEASURED OBSERVATION**

All four retained dynamic runs received both `REQUEST_OK` responses and all 480 Objects. ACK medians ranged from `18.578` to `27.458 ms`, while the first receiver-observed new-high completions ranged from `8.370` to `52.950 ms`. Source: the four valid dynamic `request_updates.csv` and `summary.json` artifacts listed above.

**IMPLEMENTATION INTERPRETATION**

The project wrapper and pinned moqx applied active-subscription priority updates under contention. A `REQUEST_OK` timestamp is control-plane acceptance, whereas first effect and window dominance are receiver-observed data-plane proxies. The large stale totals include all later old-high Objects, not only an estimated in-flight queue at ACK time.

**PROTOCOL QUESTION**

Should an application be able to distinguish update acknowledgement from data-plane scheduling effectiveness, and if so what observable contract is useful?

`P2_CORE = COMPLETE`; update-rate, Track-count, capacity, and direct relay-scheduler instrumentation remain unrun. Two race-affected update runs remain `COMPLETED_INVALID`. Source: `docs/P2_COMPLETION_AUDIT.md` and `docs/P2_REQUEST_UPDATE_AUDIT.md`.

## 9. P3 — Delivery Timeout

P3 used 8 Tracks and 1,280 expected Objects under the P2 controlled bottleneck. The valid no-timeout control received `1,280/1,280` Objects and observed zero native stream resets. Source: `configs/p3.control-no-timeout.draft18.yaml` and `results/P3/run_20260916T042601Z_03a5a93d/summary.json`.

P3A set `OBJECT_DELIVERY_TIMEOUT=50 ms`. The subscriber observed 2 native `DELIVERY_TIMEOUT` resets and received `1,277/1,280` Objects, leaving 3 missing. The same run's relay log contains `Failed to create uni stream` and `CrossExecFilter beginSubgroup failed`; it is therefore `COMPLETED_INVALID` and its latency/goodput cannot be used as timeout performance. Source: `configs/p3.object-timeout.draft18.yaml`, `results/P3/run_20260916T042629Z_6b52ee91/summary.json`, and that run's `relay.log`.

P3B was not run. The pinned moxygen source declares and validates Subgroup Delivery Timeout key `0x06`, but its effective-timeout lookup reads only Object Delivery Timeout key `0x02`; no native subgroup timer/reset behavior was found. Merely encoding the parameter would not be a native experiment. Source: `docs/P3_TIMEOUT_AUDIT.md` and `docs/P3_COMPLETION_AUDIT.md`.

**MEASURED OBSERVATION**

The clean control had no resets; the invalid P3A treatment exercised the native timeout path with 2 resets and 3 missing Objects, but also had fatal relay resource signatures. Source: the two P3 `summary.json` files and the P3A `relay.log`.

**IMPLEMENTATION INTERPRETATION**

Object-timeout wiring exists in this aiomoqt/moqx stack, but the sole lossy treatment is causally confounded by relay stream-resource failure. Subgroup-timeout native behavior is missing from this pinned implementation.

**PROTOCOL QUESTION**

How should partial-reliability behavior be evaluated across implementations once native Object and Subgroup timeout paths can be exercised without resource faults?

Final status: P3A `NATIVE_PATH_CONFIRMED_BUT_MEASUREMENT_BLOCKED_BY_RELAY_RESOURCE_ERROR`; P3B `BLOCKED_UNIMPLEMENTED_NATIVE_BEHAVIOR`; overall `CHARACTERIZATION_CONCLUDED_PARTIAL`. Source: `docs/P3_COMPLETION_AUDIT.md`.

## 10. P4 — Forward / Multi-user Fan-out

### P4A — Forward state and transitions

P4A used one Track at `1 Mbit/s`, `500 ms` Groups, 5 Objects/Group, and a controlled `100 Mbit/s`, `40 ms` RTT path. It tested initial `Forward=1`, initial `Forward=0`, native `0→1`, and native `1→0`. Source: `configs/p4.forward-smoke.*.draft18.yaml`.

Initial-state results (three valid repetitions each):

| Mode | Downstream payload bytes | Downstream wire bytes by repetition |
|---|---:|---:|
| `Forward=1` | 250,000 each | 262,837; 262,753; 262,753 |
| `Forward=0` | 0 each | 4,220; 4,263; 4,265 |

Source: `results/P4/p4_summary.csv` and the six corresponding clean `summary.json` files (`144435` through `144519`).

Activation and deactivation results:

| Transition/run | REQUEST_UPDATE→OK (ms) | First Object (ms) | First complete Group (ms) | Objects / bytes after ACK | Signed last Object−ACK (ms) | Downstream wire bytes |
|---|---:|---:|---:|---:|---:|---:|
| `0→1` `868d5d7e` | 40.849 | 58.176 | 582.387 | 19 / 237,500 | n/a | 249,585 |
| `0→1` `3f17ffc4` | 40.814 | 58.297 | 581.456 | 19 / 237,500 | n/a | 249,627 |
| `0→1` `c066a736` | 40.816 | 58.994 | 582.707 | 19 / 237,500 | n/a | 249,707 |
| `1→0` `f8b85f6c` | 41.885 | n/a | n/a | 3 / 37,500 | 7.045 | 236,980 |
| `1→0` `1cc39ecb` | 41.994 | n/a | n/a | 4 / 50,000 | 9.741 | 249,822 |
| `1→0` `1872e4a7` | 41.077 | n/a | n/a | 0 / 0 | -482.277 | 198,261 |

Source: each run's `summary.json`/`summary_rows.csv`. The negative signed value means the last Object preceded the ACK; it is not a negative physical drain. First-Object/Group times are demand-relative, while ACK time is update-send-relative.

The first 12 reruns (`144012` through `144150`) were invalid because monitoring/process-liveness gates failed. Reliability work then placed lifecycle phases around endpoints, started monitoring before launch, scoped the long-lived relay log by byte offsets, treated resource signatures as fatal, and separated teardown-only `requestUpdate failed: Session closed` messages from active-window errors. The subsequent 12 P4A runs passed all gates. Source: those invalid/valid `summary.json` files and `docs/P4_FORWARD_AUDIT.md`.

### P4B — Reactive versus Dormant

Reactive means no subscription for the demanded Tracks until demand, followed by native `SUBSCRIBE Forward=1`. Dormant is configured as an already-established subscription with `Forward=0`, later changed through native `REQUEST_UPDATE Forward=1`. Dormant is **not cache prewarm**. Source: `configs/p4.reactive.8track.draft18.yaml`, `configs/p4.dormant.8track.draft18.yaml`, and `docs/P4_FORWARD_AUDIT.md`.

The repository contains three clean Reactive runs and no Dormant run directory or `mode=dormant` aggregate row:

| Reactive run | First Object (ms) | First complete Group (ms) | Viewport/set activation |
|---|---:|---:|---|
| `81cdad7f` | 42.766 | 164.857 | not emitted for Reactive |
| `e07e4ef3` | 543.074 | 717.624 | not emitted for Reactive |
| `f684e719` | 42.589 | 164.085 | not emitted for Reactive |
| Median | 42.766 | 164.857 | unsupported |
| Range | 42.589–543.074 | 164.085–717.624 | unsupported |
| Mean / population SD | 209.476 / 235.889 | 348.855 / 260.759 | unsupported |

Sources: the three Reactive `summary.json` files and `results/P4/p4_summary.csv`; descriptive statistics are computed directly from those three stored values. The high-latency repetition remains visible. Reactive uses new SUBSCRIBEs, so `REQUEST_UPDATE→REQUEST_OK` is not applicable; the missing Dormant side means no update-latency or Reactive-vs-Dormant comparison exists.

This conflicts with older statements in `README.md`, `docs/DESIGN.md`, and `docs/TESTS.md` that P4B is complete. Based on result artifacts, the authoritative status is `PARTIAL_REACTIVE_ONLY_DORMANT_RESULTS_ABSENT`.

### P4C — Inactive-demand prewarm

`P4C_PREWARM = BLOCKED_BY_CURRENT_RELAY`. moqx has cache and FETCH paths, but its current upstream Forward policy follows active forwarding subscribers; no hook was found to keep upstream media flowing solely to prewarm inactive downstream demand. This is an **IMPLEMENTATION POLICY** limitation, not proof that MOQT lacks prewarming. No P4C measurement was run. Source: `docs/P4_FORWARD_AUDIT.md`.

### P4D — Multi-user fan-out

P4D used 2 independent users over 8 published Tracks. User A requested Tracks 0–3 and user B requested Tracks 2–5: 2 overlapping Tracks, 6 unique demanded Tracks, and 8 Track memberships, i.e. 50% overlap and a configuration membership-reuse factor of `8/6 = 1.333`. Source: `configs/p4.fanout.2user.50overlap.draft18.yaml`.

| Run | Publisher→relay wire bytes | Relay→users wire bytes | Stored upstream amplification | SUBSCRIBE→OK ms (A/B) | Integrity |
|---|---:|---:|---:|---:|---|
| `df7aa83b` | 8,265,007 | 1,748,759 | 1.8367 | 41.727 / 41.008 | pass |
| `0eea6fb0` | 8,268,170 | 1,813,241 | 1.8374 | 42.647 / 41.567 | pass |
| `ccbfabd8` | 8,271,069 | 2,030,761 | 1.8380 | 41.700 / 40.889 | pass |

Source: each fan-out run's `summary.json` and `summary_rows.csv`. The stored “upstream amplification” is publisher→relay wire bytes divided by unique required media bytes; it is not the membership-reuse factor and not proof of a predictive relay policy.

P4D establishes subscriptions at demand and does not perform a Forward `REQUEST_UPDATE`; Forward-update latency is therefore not applicable. Its table reports native SUBSCRIBE response latency instead. Source: `src/moq_360_protocol_lab/p4_adapter.py` and the fan-out `forward_updates.csv`/`summary_rows.csv` artifacts.

**MEASURED OBSERVATION**

P4A's native transitions completed with roughly `41 ms` ACK latency and retained small post-deactivation drains in two repetitions; Reactive P4B had two roughly `43 ms` first-Object results and one `543 ms` result; P4D passed integrity for both users in all 3 repetitions. Sources: the P4 tables and artifacts above.

**IMPLEMENTATION INTERPRETATION**

The pinned moqx supports native Forward state/update and matching-subscription fan-out. Reactive activation is variable in this small series. The result set does not contain Dormant evidence and does not establish inactive-demand cache prewarming.

**PROTOCOL QUESTION**

Can retained subscription state and relay policy reduce demand-activation delay with bounded upstream and post-deactivation cost, and what must be protocol-visible versus implementation-local?

## 11. P5 — FETCH / Joining FETCH

P5 mapped `live_only` to `LATEST_OBJECT SUBSCRIBE` with `Forward=1`, Standalone FETCH to an explicit historical range, and `joining_fetch` to a live subscription plus Relative Joining FETCH with `Joining Start=0`. Absolute Joining FETCH and cancellation were not characterized. Source: `docs/P5_FETCH_AUDIT.md` and `docs/P5_RESULTS.md`.

Authoritative native localhost semantic smokes:

| Mechanism/run | Actual demand offset | First Object | First complete Group | Historical delivery | Continuity |
|---|---:|---:|---:|---:|---|
| Standalone FETCH `dc6eac7e` | 501.066 ms | 4.610 ms | 25.251 ms | 40 Objects / 125,000 B | 0 missing; 0 redundant bytes |
| Relative Joining FETCH `e32007ea` | 505.410 ms | 5.097 ms | 470.540 ms | 21 fetched Objects / 65,625 B; 19 live Objects | 0 gaps; 0 duplicates; 0 ms live-edge delay at completion |

Sources: `results/P5/run_20260918T144854Z_dc6eac7e/summary.json` and `results/P5/run_20260918T145044Z_e32007ea/summary.json`. Four additional finalized smokes are indexed in `results/P5/p5_summary.csv`; the first Standalone run is retained invalid because the harness misclassified an `END_OF_GROUP` status and failed its monitoring/integrity gates (`docs/P5_RESULTS.md`).

**MEASURED OBSERVATION**

moqx returned a complete historical Group through native Standalone FETCH and delivered one complete current Group through a gap-free mix of 21 fetched and 19 live Objects in Relative Joining FETCH. Source: the two authoritative P5 summaries.

**IMPLEMENTATION INTERPRETATION**

Native historical retrieval, the joining relationship, source attribution, and FETCH/live continuity work in this pinned aiomoqt/moqx localhost setup. The measured latencies are semantic-smoke timings, not controlled performance.

**PROTOCOL QUESTION**

Under controlled delay and multi-Track workloads, how should historical catch-up and live delivery be balanced to minimize complete-set activation time and redundant bytes?

P5 Live, Fetch, and Relative Joining Fetch are `NATIVE_SEMANTICS_COMPLETE`. The direct standalone `live_only` smoke is not present, but the Joining smoke validates its live subscription leg. The controlled P5A/P5B matrices are `NOT_RUN / CONTROLLED_MATRIX_PENDING_ROOT`: namespace entry requires root, and localhost results must not be relabelled. Source: `docs/P5_RESULTS.md` and `scripts/run_p5_matrix.sh`. No first-decodable-frame conclusion is supported because payloads are synthetic.

## 12. Implementation and Resource Limitations

| Finding | Test | Category | Evidence | Protocol Gap? |
|---|---|---|---|---|
| Default `max_bidi_streams=16` stopped the 17th request stream; configured `64` allowed the 24-Track run | P1 | CONFIGURATION LIMIT | `docs/RELAY_AUDIT.md`; relay configs; 24-Track summary | No |
| Missing relay log made an otherwise completed P1 run invalid | P1 | EXPERIMENT HARNESS | `results/P1/run_20260915T165541Z_d2e68928/summary.json` | No |
| Two update runs received only one of two expected responses before the response-registration fix | P2 | IMPLEMENTATION BUG | `docs/P2_REQUEST_UPDATE_AUDIT.md`; invalid summaries | No |
| `Failed to create uni stream` / `CrossExecFilter beginSubgroup failed` confounded Object timeout | P3A | RESOURCE LIMIT | P3A `relay.log` and summary | No |
| Subgroup timeout parameter exists but effective native timer/reset behavior does not | P3B | IMPLEMENTATION MISSING FEATURE | `docs/P3_TIMEOUT_AUDIT.md` | No |
| Forward=0 does not provide the required inactive-demand upstream prewarm policy | P4C | IMPLEMENTATION POLICY | `docs/P4_FORWARD_AUDIT.md` | No |
| First P4A reruns failed monitoring/liveness gates; first P4B attempts failed transition gates | P4 | EXPERIMENT HARNESS | invalid P4 summaries | No |
| Teardown-scoped `requestUpdate failed: Session closed` messages occur after endpoint teardown and are retained as warnings | P4/P5 | EXPERIMENT HARNESS | clean P4/P5 summaries; `run_phases.events.jsonl` | No |
| Controlled namespace matrix could not be entered in the recorded session | P5 | EXECUTION PERMISSION | `docs/P5_RESULTS.md` | No |
| No Dormant artifacts despite old “P4B complete” text | P4B | UNRESOLVED | `results/P4/`; `results/P4/p4_summary.csv`; older docs | No finding possible |

No row above is evidence of a protocol defect. “PROTOCOL SEMANTIC” is deliberately unused because the retained failures were traced to configuration, implementation, resources, harnessing, permissions, or missing evidence.

## 13. Cross-Test Observations

### Correlated Track completion without aggregate collapse

**MEASURED OBSERVATION**

P1 kept aggregate goodput in a narrow `22.625–22.658 Mbit/s` range and completed every Group from 1 through 24 Tracks, while P95 cross-Track skew rose from `0 ms` at 1 Track to `34.967 ms` at 24 Tracks. Source: `results/P1/p1_summary.csv`.

**IMPLEMENTATION INTERPRETATION**

The evaluated moqx scheduler and aiomoqt endpoints did not show an aggregate-throughput collapse in this unshaped fixed-load series, but independently delivered Tracks can still differ in completion time. Confidence: **moderate for this stack, low for generalization**, because each setting has one repetition and no shaped network.

**PROTOCOL QUESTION**

How should correlated multi-Track completion objectives be expressed or evaluated when delivery units are independent subscriptions?

### Acknowledgement is not data-plane reaction

**MEASURED OBSERVATION**

P2 ACK medians were `18.578–27.458 ms`, first effects were `8.370–52.950 ms`, and the conservative dominance bound was `500 ms`; P4A Forward updates were acknowledged in `40.814–41.994 ms`, while activation to a complete Group was about `581–583 ms`. Sources: valid P2 dynamic summaries/request-update CSVs and the six P4A transition summaries.

**IMPLEMENTATION INTERPRETATION**

For this implementation, request acceptance and receiver-visible useful delivery are distinct events. P2 additionally retained `140–141` old-high Objects after the switch, while P4 deactivation saw `0–4` Objects after ACK, but these metrics have different workloads and definitions and must not be directly equated. Sources: the same P2/P4 summaries. Confidence: **moderate**.

**PROTOCOL QUESTION**

What data-plane observability, if any, should accompany demand-changing control operations so applications can reason about effectiveness and in-flight work?

### Native state/history mechanisms exist, but policy and controlled comparisons are incomplete

**MEASURED OBSERVATION**

P4D served 2 users with overlapping Track sets in 3 valid fan-out runs; P5 returned 40 historical Objects in Standalone FETCH and a gap-free 21-fetch/19-live joining transition. However, P4 has no Dormant results, P4C is blocked by current relay policy, and the controlled P5 matrices were not run. Sources: P4D and P5 summaries, `docs/P4_FORWARD_AUDIT.md`, and `docs/P5_RESULTS.md`.

**IMPLEMENTATION INTERPRETATION**

The pinned stack has functional fan-out, cache-backed FETCH, and Joining FETCH, but current evidence cannot quantify the benefit/cost of retained dormant state or inactive-demand prewarm. Confidence: **high for native semantic existence; low for comparative performance**.

**PROTOCOL QUESTION**

Can subscription state, relay retention policy, and joining retrieval be coordinated to reduce activation latency without excessive upstream, stale, or redundant delivery?

## 14. Ideas Ruled Out as Research Gaps

- **“MOQT cannot support multi-user fan-out.”** Not supported: P4D has 3 valid native two-user runs with overlapping Track sets and passing integrity. Evidence: P4D summaries.
- **“MOQT has no late-join mechanism.”** Not supported for draft-18: native Standalone FETCH and Relative Joining FETCH both passed semantic smokes. Evidence: authoritative P5 summaries and `docs/P5_FETCH_AUDIT.md`.
- **“Forward=0 is equivalent to prewarm.”** False in this implementation and not implied by the evaluated semantics. Evidence: `docs/P4_FORWARD_AUDIT.md`.
- **“Delivery timeout performs poorly.”** Not supported: the only Object-timeout treatment with loss is invalid due relay resource errors, and Subgroup timeout was not run. Evidence: P3 completion audit.
- **“Many Tracks necessarily reduce aggregate throughput.”** Not supported by P1: the valid loopback series retained roughly constant aggregate goodput through 24 Tracks. Evidence: `results/P1/p1_summary.csv`. This does not prove invariance under other networks or implementations.
- **“REQUEST_OK proves immediate scheduling effect.”** Not supported: P2 and P4 store separate ACK and receiver-effect times. Evidence: P2 and P4 transition summaries.
- **“P5 measures first-decodable-frame latency.”** False: it measures deterministic synthetic Object/Group completeness, with no codec or decoder. Evidence: P5 configs and `docs/P5_RESULTS.md`.

## 15. Candidate Research Questions

No candidate is ranked and none is claimed novel.

1. **How should correlated multi-Track media completion requirements be represented and scheduled when each Track is an independent subscription?** Evidence: P1 preserved aggregate goodput but exposed nonzero cross-Track skew. Unknown: shaped/repeated behavior, realistic media dependencies, other relays, and scheduler policies. A later stage must check newer MOQT drafts and literature for existing dependency/grouping/prioritization work.

2. **Does application demand-change signalling need an observable data-plane effectiveness contract distinct from control acknowledgement?** Evidence: P2/P4 separate ACK, first effect, stable dominance, and drain; their timings differ. Unknown: relay-internal application time, causal bounds, rapid updates, scale, and cross-implementation behavior. A later stage must check newer drafts and control/scheduling literature.

3. **Can retained subscription state, relay cache policy, and Joining Fetch be combined to reduce activation delay without excessive upstream or stale-delivery cost?** Evidence: P4 Reactive variability, functional fan-out, the P4C policy block, and P5 native historical/live continuity. Unknown: the missing Dormant side, true inactive-demand prewarm, and controlled P5 results. A later stage must check newer draft semantics/extensions and prior cache/prefetch/late-join research.

Evidence inputs for these questions are the P1–P5 sections above; they are questions raised by this implementation's data, not protocol-gap conclusions.

## 16. What Remains Unmeasured

- P1 shaped Series B, additional repetitions, real coded media, decoder/keyframe behavior, and alternative relays.
- P2 update-rate, Track-count, and capacity sweeps; rapid/overlapping update behavior; relay-internal scheduler timestamps; generalization beyond two Tracks.
- A clean controlled P3A treatment and any native P3B execution.
- P4B Dormant repetitions and the requested Reactive-vs-Dormant comparison; viewport metric for Reactive; any genuine inactive-demand prewarm experiment or cache-hit measurement.
- Predictive relay policy: P4D demonstrates fan-out only.
- Controlled P5A/P5B namespace matrices, multi-Track catch-up, all configured offsets/repetitions, Absolute Joining FETCH, FETCH cancellation, and first-decodable-frame behavior.
- Cross-host behavior and synchronized-clock uncertainty.

Sources for configured-but-unrun work: `docs/TESTS.md`, `docs/P2_COMPLETION_AUDIT.md`, `docs/P3_COMPLETION_AUDIT.md`, `configs/p4.dormant.8track.draft18.yaml`, `docs/P5_RESULTS.md`, `configs/p5a.base.draft18.yaml`, and `configs/p5b.base.draft18.yaml`.

## 17. Reproduction Index

No reproduction command was run while producing this report.

| Test | Runner/config entry points | Validity authority |
|---|---|---|
| P1 | `scripts/run_sweep.sh`; `configs/p1.baseline.draft18.yaml`; `src/moq_360_protocol_lab/p1_matrix.py` | `src/moq_360_protocol_lab/p1_runner.py`; `src/moq_360_protocol_lab/p1_analysis.py` |
| P2 | `scripts/run_p2_experiment.sh`; `configs/p2.*.draft18.yaml` | `src/moq_360_protocol_lab/p2_runner.py`; `docs/P2_COMPLETION_AUDIT.md` |
| P3 | `scripts/run_p3_experiment.sh`; `configs/p3.*.draft18.yaml` | `src/moq_360_protocol_lab/p3_runner.py`; `docs/P3_COMPLETION_AUDIT.md` |
| P4 | `scripts/run_p4_experiment.sh`; `configs/p4.*.draft18.yaml` | `src/moq_360_protocol_lab/p4_runner.py`; `docs/P4_FORWARD_AUDIT.md` |
| P5 smoke | `scripts/run_p5_local_smoke.sh`; local smoke configs | `src/moq_360_protocol_lab/p5_runner.py`; `docs/P5_RESULTS.md` |
| P5 matrix | `scripts/run_p5_matrix.sh`; `configs/p5a.base.draft18.yaml`; `configs/p5b.base.draft18.yaml` | root-required; not run in retained evidence |
| Network | `scripts/setup_p2_netns.sh`; `scripts/apply_p2_shaping.sh`; `scripts/run_p2_network_sanity.sh` | saved run configs and sanity evidence |

## 18. Artifact Index

- **Machine-readable consolidation:** `results/experiment_summary.json`.
- **Compact cross-test index:** `results/experiment_master_table.csv`.
- **P1:** `results/P1/p1_summary.csv`, `results/P1/analysis/p1_summary.md`, and Series A run directories.
- **P2:** valid/invalid `results/P2/run_*/summary.json`, `request_updates.csv`, `subscriber.events.jsonl`, and audits in `docs/P2_*.md`.
- **P3:** both run directories, `docs/P3_TIMEOUT_AUDIT.md`, and `docs/P3_COMPLETION_AUDIT.md`.
- **P4:** `results/P4/p4_summary.csv`, clean/invalid/exploratory run directories, `summary_rows.csv`, `forward_updates.csv`, monitor/network artifacts, scoped relay logs, and `docs/P4_FORWARD_AUDIT.md`.
- **P5:** `results/P5/p5_summary.csv`, six run directories, `fetch_events.csv`, `subscribe_events.csv`, `object_transition.csv`, monitor/network artifacts, scoped relay logs, `docs/P5_FETCH_AUDIT.md`, and `docs/P5_RESULTS.md`.
- **Negotiation/provenance:** each finalized run's `protocol_negotiation*.json` and `provenance.json`.

### Publication metadata inconsistencies to resolve

1. Old P4 status prose says P4B is complete, but current results contain no Dormant run or aggregate row.
2. `run_20260916T031350Z_f799275a/summary.json` stores a `28.312430 ms` ACK “median”; the two raw ACK samples yield `26.970132 ms`.
3. CPU model, distribution release name, and physical NIC were not captured in historical provenance.
4. Five run directories lack `summary.json`; they are preserved but not finalized or counted as invalid summaries.
5. P1–P4 and P5 were captured at different dirty repository commits, so publication should bind the exact artifact tree rather than cite a single clean source commit.

Sources: the referenced P2 raw/summary artifacts, `results/P4/`, the per-run provenance files, and the run-directory inventory under `results/`.
