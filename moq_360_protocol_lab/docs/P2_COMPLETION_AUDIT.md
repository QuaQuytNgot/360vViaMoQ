# P2 completion audit

Audit date: 2026-09-16. Scope is the current checked-in strict
`draft-ietf-moq-transport-18` stack: `aiomoqt==0.10.6`,
`aiopquic==0.3.11`, and locally built OpenMOQ moqx commit
`502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6`. Only raw QUIC with ALPN
`moqt-18` is in scope.

## Final status

**P2_CORE = COMPLETE.**

The primary A→B dynamic configuration now has three independent valid
repetitions. No protocol-semantic problem was found. The P2 result set below
is frozen: future work must write new results rather than modify these raw
artifacts.

**P2_EXTENDED = INCOMPLETE.** Update-rate/track-count/capacity sweeps and
direct relay scheduler timestamps were not run. Those are secondary and do
not block P3 after P2_CORE repetition is complete.

| Requirement | Status | Evidence |
|---|---|---|
| moqt-18 native | PASS | Valid runs record publisher and subscriber `requested_draft=18`, `negotiated_draft=18`, `transport=raw_quic`, `alpn=moqt-18`: e.g. `results/P2/run_20260916T031350Z_f799275a` and `run_20260916T033223Z_006c52fa`. |
| traffic crossed selected relay / no fallback | PASS | Separate publisher (`10.253.1.1`) and subscriber (`10.253.2.1`) relay-facing endpoints in the namespace topology; saved relay logs contain relay Forward records. The client offers singleton draft 18 only; no d16/moq-lite path is present. |
| native REQUEST_UPDATE | PASS | `src/moq_360_protocol_lab/aiomoqt_d18_update.py` allocates fresh IDs, writes `RequestUpdate` on the original subscription bidi stream via `send_stream_message`, and does not call unsubscribe, reconnect, or subscribe. Unit regression: `tests/test_aiomoqt_d18_update.py`. |
| static priority works | PASS | A-high/B-low: A median/p95 56.5/98.9 ms vs B 4.61/6.03 s (`run_20260916T030458Z_e5f3236b`). B-high/A-low reverses it: B 56.8/106.4 ms vs A 4.63/6.08 s (`run_20260916T030726Z_4e3c836e`). |
| dynamic priority update | PASS | Valid A→B (`run_20260916T031350Z_f799275a`) and B→A (`run_20260916T033223Z_006c52fa`) both have 2 sent updates, 2 `REQUEST_OK`, and no reconnect/resubscribe operation. |
| contention present | PASS | Reused isolated publisher→relay→subscriber namespace path; relay downstream `p2rels0` shaped at 19.2 Mbps plus 5 ms, zero loss. Independent sanity: 19.594 Mbps, 5.095 ms average RTT, 0% loss. Workload offered 24 Mbps aggregate. |
| control latency measured | PASS | A→B ACK median recomputed from raw events: 26.97 ms; B→A: 20.89 ms. `request_updates.events.jsonl` contains generated/encode/sent timestamps and `REQUEST_OK` timestamps. |
| effective reaction measured | PASS | Receiver-observed proxy is deliberately separate from ACK. A→B first new-high B completion: 34.84 ms after send; B→A first new-high A: 8.37 ms. Conservative two-window dominance bound: 500 ms for both after the metric correction. |
| integrity pass | PASS | Every valid control/switch run: 480 expected/480 received; 0 duplicate, malformed, unexpected, or missing. Dynamic scheduling out-of-order counts (49/50) are retained as observations, not recast as corruption. |
| provenance complete | PASS | Each valid run has config, manifest, endpoint negotiation JSON, publisher/subscriber event logs/CSVs, relay log, summary and `provenance.json` with binding versions, relay commit, config/ledger hashes, clock and system data. |
| >=3 repetitions, primary dynamic configuration | PASS | Three valid A→B runs: `run_20260916T031350Z_f799275a`, `run_20260916T035447Z_942d19b5`, and `run_20260916T035558Z_6dc3294e`. Each has 2/2 REQUEST_OK, 480/480 integrity, strict negotiation evidence, relay log, and provenance. The valid B→A run is independent directional confirmation. Two earlier update runs remain explicitly `COMPLETED_INVALID` due the fixed response-registration race and do not count. |

## Implementation and evidence inventory

| Item | Status | Location |
|---|---|---|
| P2 adapter | PASS | `src/moq_360_protocol_lab/p2_adapter.py`, `p2_endpoint.py`, `p2_runner.py` |
| strict d18 update extension | PASS | `src/moq_360_protocol_lab/aiomoqt_d18_update.py` |
| isolated network tooling | PASS | `scripts/setup_p2_netns.sh`, `apply_p2_shaping.sh`, `run_p2_network_sanity.sh`, `run_p2_experiment.sh` |
| configs | PASS | `configs/p2.static.*.draft18.yaml`, `configs/p2.switch.*.draft18.yaml` |
| raw runs / update logs / relay logs | PASS | `results/P2/run_*/`; valid and invalid runs retained |
| direct relay scheduler instrumentation | NOT RUN | Relay source audit identifies interfaces/counters, but no event-level scheduler timestamp patch was made; receiver evidence is a data-plane proxy. |
| P2 aggregate summary / plots | PARTIAL | Per-run `summary.json` and authoritative CSV/events exist. No aggregate `p2_summary.csv` or plots have been produced yet. |

## Frozen primary evidence

The three A→B repetitions retain the same strict two-track configuration,
24 Mbps aggregate offer, 19.2 Mbps relay→subscriber TBF, 5 ms netem delay,
and 5-second switch anchor. Their first receiver-observed new-high completion
latencies are 34.84 ms, 36.28 ms, and 52.95 ms respectively. These are
implementation/workload observations, not a claim of general MOQT behavior.
