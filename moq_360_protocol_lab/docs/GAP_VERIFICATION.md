# MOQT Immersive Gap Verification

## 1. Questions

G1 asks whether eight correlated spatial Tracks miss a common viewport deadline or develop material completion skew under native independent Track scheduling. G2 asks whether a batch of native per-Track `REQUEST_UPDATE` messages creates a measurable interval during which the relay schedules with mixed old and new viewport priorities. Neither question assumes a protocol defect.

**Current status: not verified.** The 54 planned repetitions in `results/GAP_VERIFY/gap_verify_summary.csv` are `NOT_RUN`. The current session could list the existing namespaces only through escalation and could not enter them: `sudo -n ip netns exec moq-p2-relay true` returned `sudo: a password is required`. No new controlled traffic was generated. Null fields in the summary mean unmeasured, never zero. The pinned relay binary also lacks the relay scheduler trace needed for G2.

## 2. Existing Evidence from P1/P2

The immutable P1 Series A evidence reports aggregate goodput of 22.625–22.658 Mbit/s across 1–24 Tracks, complete Groups, and P95 cross-Track Group skew of 34.967 ms at 24 Tracks. This is one repetition per setting on unconstrained loopback, with one Object per Group; it cannot establish G1 under congestion. P2's valid two-Track priority-switch experiments measured request acknowledgements and receiver-side reaction separately. Its ACK medians were 18.578–27.458 ms, while the first new-high completion ranged 8.370–52.950 ms. Those values do not timestamp relay state application or scheduler selection. Sources: `docs/EXPERIMENT_SUMMARY.md`, `results/experiment_summary.json`, and `results/experiment_master_table.csv`. These source artifacts were not changed.

## 3. New Instrumentation

`src/moq_360_protocol_lab/gap_verify.py` records local monotonic timestamps for scripted demand, each per-Track update send, each `REQUEST_OK` or error, each received Object, and complete viewport Groups. The workload uses native draft-18 `SUBSCRIBE` and the repository's existing same-stream `REQUEST_UPDATE` sender. It sends no set identity, deadline, viewport epoch, or custom wire message to the relay.

The pinned moqx build does **not** expose the requested `UPDATE_RECEIVED`, `UPDATE_APPLIED_TO_REQUEST_STATE`, `REQUEST_OK_SENT`, `FIRST_SCHEDULER_DECISION_NEW_STATE`, or `OBJECT_SUBMITTED` events. Its moxygen `MoQSession::handleSubscribeRequestUpdate` applies subscriber priority before invoking the relay's subscription handle; the actual stream scheduler is in the separately built moxygen dependency. Instrumenting a later moqx handle callback would mislabel the priority-application time, and subscriber arrivals cannot reconstruct scheduler decisions. The run script writes an empty, explicitly marked `relay_scheduler_events.csv` and `instrumentation_status.json`; G2 runs fail the instrumentation validity gate until real relay events exist. The same gate currently invalidates any run lacking scheduler instrumentation, including G1, because the requested run artifact is mandatory. No scheduler behavior was patched.

The per-Track update table reserves null fields for `T_relay_apply`, `T_scheduler_effect`, and old-state delivery after scheduler effect. `first_receiver_object_ms` is an arrival observation, **not** `T_receiver_effect`: receiving an Object after the switch does not prove that its scheduling used the new priority. `mixed_epoch_window` remains null until per-Track first new-state scheduler decisions are directly captured.

## 4. Workload and Viewport Model

One deterministic `tile_0`–`tile_47` Track represents each tile in a logical 6×8 panorama. All active media Tracks use the same Group timeline and 50 ms Object cadence. All 48 Tracks are announced; only the required or transition candidate Tracks produce media, limiting inactive traffic. Payloads use the existing identity and integrity check. The active Tracks have fixed 0.60–1.40 weight multipliers by tile index, normalized to 8 Mbit/s total offered media load in each condition. No codec or prediction is present. A Group duration is a **Group duration / GOP proxy**, not a measured codec GOP.

G1's primary required viewport A is `{8,9,10,11,16,17,18,19}`. Each run lasts at least 16 Groups of 500 ms with all eight Tracks at equal high priority. G1-B is only a conditional confirmation: required-set sizes 4, 8, and 12 at 80% capacity, three repetitions each, and must be launched only if G1-A shows a meaningful effect.

G2 uses A followed by `{28,29,30,31,36,37,38,39}` for 0% overlap, or `{8,9,10,11,28,29,30,31}` for 50% overlap. The scripted switch is at 2000 ms. Leaving Tracks change high→low and entering Tracks low→high. Distinct Track updates are emitted back to back, with responses awaited concurrently. The endpoint wrapper allows one outstanding update per Track; repeated demand may cause an explicit `update_overlap_rejected` event rather than guessed coalescing. Frequency conditions use the same A→B→C→A→B→C→A sequence at 2, 5, and 10 Hz, three repetitions each. A 10 Hz condition with overlap or errors is to stop the series.

The application baselines use an adjacent A→B→C→A trace at 2375, 4375, and 6375 ms, so guard neighbors can actually become required mid-Group. They compare 1000, 500, and 250 ms Groups, plus 1000 ms with guard and 500 ms with guard. A guard is the immediate horizontal neighbor on each side of each required tile in its row, with wraparound. Guard tiles are given high priority before demand; all transition candidate Tracks remain subscribed at low priority so the update mechanism is the same across baselines.

## 5. Network Environment

The controlled runner targets the existing `moq-p2-pub`, `moq-p2-relay`, and `moq-p2-sub` namespaces. It shapes only relay egress `p2rels0` with the repository's `apply_p2_shaping.sh`: capacity is 100%, 80%, or 60% of the 8 Mbit/s active offer for G1, and 80% for G2 and baselines. It adds 30 ms fixed egress delay, targeting roughly 30 ms path RTT, with 0% injected loss and no jitter. The script never touches the host physical interface. Actual RTT and rate must be independently verified per series before results are called controlled.

The runner checks the 64 bidirectional stream configuration, a 1024 FD floor, 1 GiB available memory, namespaces, veth interface, and relay listener. It records run-scoped relay log, monitor CSV/JSONL, CPU/RSS/liveness, and veth counters. It preserves any resource-fault run and marks it invalid. The relay must already be started with the pinned draft-18 configuration; the script does not silently alter limits.

## 6. G1 Results — Correlated Track Set Completion

| Capacity / offer | Planned reps | Valid reps | Viewport latency | Miss ratio | Completion skew |
|---:|---:|---:|---|---|---|
| 100% | 5 | 0 | unmeasured | unmeasured | unmeasured |
| 80% | 5 | 0 | unmeasured | unmeasured | unmeasured |
| 60% | 5 | 0 | unmeasured | unmeasured | unmeasured |

For each required Track/Group, the analyzer records first Object, last Object, and completion. Complete viewport time is the maximum required-Track completion. Completion latency is relative to the Group's source start. The deadline is source Group start plus Group duration. An incomplete set is a deadline miss; completed-set skew is max minus min Track completion. P50/P95 are over Groups within a run; across 3–5 repetitions the report will emphasize every run and median/range. Aggregate and weakest-Track goodput are secondary metrics.

## 7. G2 Results — Multi-Track Demand Update

No controlled transition exists. Control ACK, relay apply, scheduler effect, receiver effect, mixed epoch window, old-viewport bytes, and first complete new viewport are unmeasured. The analysis preserves per-Track sends and ACKs when runs exist. Its `old_state_*` counters are explicitly for Tracks leaving the viewport (old high→new low), counted until the next scripted switch; they do not label such bytes “stale.” Old-viewport bytes after switch and after all ACKs are receiver observations. Old-state delivery after scheduler effect cannot be calculated without scheduler effect timestamps.

`viewport_transition_latency` is the first complete common new-viewport Group after demand minus the demand time. This is synthetic Group completion, not a first decodable video frame.

## 8. Update-Frequency Results

| Frequency | Planned reps | Valid reps | Mixed epoch | Old-state bytes | Overlap/coalescing |
|---:|---:|---:|---|---|---|
| 2 Hz | 3 | 0 | unmeasured | unmeasured | unmeasured |
| 5 Hz | 3 | 0 | unmeasured | unmeasured | unmeasured |
| 10 Hz | 3 | 0 | unmeasured | unmeasured | unmeasured |

## 9. Short-Group / Guard-Band Baselines

| Strategy | Planned reps | Valid reps | Deadline miss | Overfetch |
|---|---:|---:|---|---|
| Native, 1000 ms Group | 3 | 0 | unmeasured | unmeasured |
| 500 ms Group | 3 | 0 | unmeasured | unmeasured |
| 250 ms Group | 3 | 0 | unmeasured | unmeasured |
| 1000 ms Group + guard | 3 | 0 | unmeasured | unmeasured |
| 500 ms Group + guard | 3 | 0 | unmeasured | unmeasured |

`overfetch_ratio = non-required delivered media bytes / all delivered media bytes` at each Object's receiver time. `useful_required_bytes` count Objects on the then-required viewport. `guard_bytes` count Objects on then-neighbor Tracks in guard conditions. `guard_bytes_potentially_used_after_switch` count pre-switch guard Objects whose Track becomes required before that same Group ends, assuming the application can retain them. `guard_bytes_never_used` is the difference. This is an upper bound on useful prefetch, not proof of decoder use. Low-priority transition candidate Tracks may also create non-required bytes, so total overfetch can exceed guard bytes.

## 10. Resource / Implementation Checks

The current session's root-only namespace entry failed before any run; CPU, RSS, and stream errors for GAP_VERIFY are unmeasured. Historical P1 identified a default 16-bidi-stream configuration limit and tested 64 for a 24-Track run. Historical P3 resource failures are excluded from protocol evidence. `Failed to create uni stream` and `CrossExecFilter beginSubgroup failed` invalidate a GAP_VERIFY run and require stopping the series. Actual 48-Track publication and monitoring overhead still need qualification.

## 11. What Is Measured vs Inferred

The new code locally verifies deterministic configuration, manifest construction, set completion arithmetic, deadline classification, and guard mapping. It has not measured network behavior. After execution, subscriber Object arrival, payload integrity, ACK receive, and local demand timestamps are measured; source media time is scripted. Relay state application and scheduler selection remain unmeasured until directly instrumented. All three processes run on one host monotonic clock, but timestamps from a relay process must still be recorded at the relay point rather than inferred from arrival.

## 12. Gap Classification

**G1: unclassified.** There are no valid GAP_VERIFY controlled runs, so none of `G1_NOT_OBSERVED`, `G1_WEAK_SIGNAL`, or `G1_SUPPORTED_FOR_THIS_STACK` is justified.

**G2: unclassified.** `G2_CONTROL_DATA_SEPARATION_OBSERVED`, `G2_MULTI_TRACK_NONATOMICITY_OBSERVED`, and `G2_APPLICATION_IMPACT_OBSERVED` cannot be assigned yes/no from this experiment. P2 separately observed ACK/receiver separation in a two-Track workload; it cannot establish the multi-Track relay scheduler interval requested here.

## 13. Does the Evidence Justify a Protocol Extension?

Undetermined. No valid comparison yet answers whether shorter Groups or guard priority provide nearly the same viewport deadline behavior, what bandwidth they cost, or whether higher update frequency helps. No protocol extension, custom scheduler, predictor, or wire message was implemented.

## 14. What Remains Unknown

Controlled G1 skew/deadline behavior, the relay's per-Track update-apply and scheduler-effect times, mixed-state duration, update-rate overlap, guard effectiveness, CPU/RSS correlation, and cross-implementation generality remain unknown. The current artifacts intentionally contain no plots, because plotting `NOT_RUN` rows would create apparent measurements. Generate plots only after valid run summaries are present.

To run one condition when root access and relay instrumentation are available: `sudo scripts/run_gap_verify.sh --family G1 --name capacity_0.8 --rep 1`; then run `PYTHONPATH=src .venv/bin/python -m moq_360_protocol_lab.gap_verify aggregate --root results`. Preserve each run directory and stop a series at its first resource or instrumentation failure. G1-B must be launched only after examining G1-A.
