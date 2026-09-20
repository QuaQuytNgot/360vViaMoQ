# P5 execution status and result guide

## Status

The native mechanism gate is complete, but the controlled characterization is
not complete in this session because the existing `moq-p2-*` network
namespaces require root and passwordless `sudo` is unavailable.

```text
P5_LIVE = NATIVE_SMOKE_COMPLETE / CONTROLLED_MATRIX_PENDING_ROOT
P5_FETCH = NATIVE_SMOKE_COMPLETE / CONTROLLED_MATRIX_PENDING_ROOT
P5_JOINING_FETCH = NATIVE_SMOKE_COMPLETE / CONTROLLED_MATRIX_PENDING_ROOT
P5_MULTITRACK = BLOCKED_BY_EXECUTION_PERMISSION
```

This is an execution-permission block, not a relay-retention or endpoint-API
block. No localhost data is relabelled as a controlled P5A/P5B result.

## Implemented experiment

P5 uses 1,000-ms Groups with 40 Objects at 25-ms cadence. Those values remain
configuration fields. Each source Object has a deterministic Track/Group/Object
identity and payload and is published only at its scheduled timestamp.

P5A is one Track, 1 Mbit/s aggregate, three native modes, offsets 100/300/500/
700/900 ms, and three repetitions. P5B is four Tracks sharing the same
1 Mbit/s aggregate timeline, offsets 100/500/900 ms, and three repetitions.
Both use the existing isolated publisher/relay/subscriber topology with 0%
loss, zero configured jitter, a 100-Mbit/s downstream limit, and a 40-ms RTT
target (20-ms one-way relay-downstream shaping).

Mode fields map directly to protocol operations:

- `live_only`: LATEST_OBJECT SUBSCRIBE, `Forward=1`.
- `standalone_fetch_live`: LATEST_OBJECT SUBSCRIBE remains live; a separate
  Standalone Fetch requests Object 0 through the subscription's reported
  Joining Location (exclusive end is `largest_object_id + 1`).
- `joining_fetch`: aiomoqt `join()` sends LATEST_OBJECT SUBSCRIBE followed by a
  Relative Joining Fetch referencing it, `Joining Start=0`.

The publisher always uses a forward publication so moqx's configured passive
cache consumer observes the paced live Track before subscriber demand.

## Metrics and validity

`T_first_object` and `T_first_complete_group` are relative to the deterministic
demand event. A complete synthetic Group contains every expected Object
identity; this does not imply codec decodability.

For a time `t`, the source live edge for Track `i` is the greatest logical
media timestamp whose `scheduled_publish_ts_ns <= t`. The subscriber edge is
the greatest logical media timestamp from that Track received by `t`. P5
reports the maximum across required Tracks of:

```text
max(0, source_live_edge_i(t) - subscriber_live_edge_i(t))
```

where `t` is the set first-common-complete-Group timestamp. A P5B set completion
exists only when every required Track has the same complete Group; skew is the
maximum minus minimum completion time for that common Group.

Every run retains negotiation evidence, raw events, `groups.csv`, transition
classification, monitoring, interface counters, scoped relay logs, provenance,
and `summary.json`. FETCH-required modes must contain one native request,
FETCH_OK, and data-stream FIN per Track. SUBSCRIBE-required modes must contain
one SUBSCRIBE and SUBSCRIBE_OK per Track. Known stream/resource signatures
invalidate and stop the matrix.

## Smoke results and monitoring

| Smoke | Run | Result | Native data | Continuity |
|-------|-----|--------|-------------|------------|
| Standalone Fetch | `run_20260918T144854Z_dc6eac7e` | valid | 40 fetched Objects, 125,000 bytes | complete historical Group, no missing/duplicate identity |
| Relative Joining Fetch | `run_20260918T145044Z_e32007ea` | valid | 21 fetched + 19 live Objects, 65,625 historical bytes | complete current Group, zero gaps, zero duplicates |

The Standalone smoke sampled maximum relay CPU/RSS of 2.5%/26,345,472 bytes,
publisher 16.0%/40,153,088 bytes, and subscriber 13.0%/40,218,624 bytes. The
Joining smoke maxima were 2.6%/26,124,288 bytes, 13.0%/40,173,568 bytes, and
14.0%/40,345,600 bytes respectively. Loopback deltas were 1,130,124 and
1,359,551 bytes; because all roles share loopback in these smoke runs, those
counters are diagnostic and are not independent link measurements.

There were no active-window protocol or resource errors. moqx emitted repeated
`requestUpdate failed: Session closed` messages only after intentional endpoint
close; these are retained separately as teardown warnings.

## Invalid run retained

`run_20260918T143738Z_97313525` is intentionally preserved as
`COMPLETED_INVALID`. Native FETCH itself succeeded (40/40 Objects), but the
first harness version counted an `END_OF_GROUP` Object Status as malformed and
started the monitor before its background process could observe endpoint PIDs.
The fixes recognize status-bearing empty Objects as protocol metadata and
start localhost monitoring after both endpoints. The succeeding valid run was
new; the invalid directory was not edited or promoted.

## P5A and P5B result tables

No controlled rows exist yet. `results/P5/p5_summary.csv` is append-only and
retains every repetition, including invalid runs. After root execution,
`p5_analysis` creates `results/P5/analysis/p5_valid_summary.csv` plus the seven
requested engineering SVGs. It never rewrites the raw ledger or hides invalid
runs.

## Reproduction

From the repository root:

```bash
# Environment and pinned relay (only needed if not already installed)
scripts/install.sh
scripts/install_relay.sh --apply

# Unit regression
PYTHONPATH=src .venv/bin/python -m pytest -q

# Native P1 regression on localhost
scripts/start_relay.sh \
  --pid-file .relay-src/moqx/p5-regression.pid \
  --log-file .relay-src/moqx/logs/p1-smoke-relay.log \
  --binary .relay-src/moqx/build/default/moqx \
  --config configs/relay.moqx.draft18.example.yaml
PYTHONPATH=src .venv/bin/python -m moq_360_protocol_lab.p1_runner \
  --config configs/p1.smoke.draft18.yaml
scripts/stop_relay.sh --pid-file .relay-src/moqx/p5-regression.pid

# Local native semantic smokes
scripts/run_p5_local_smoke.sh --config configs/p5.smoke.fetch.local.draft18.yaml
scripts/run_p5_local_smoke.sh --config configs/p5.smoke.join.local.draft18.yaml

# Controlled topology and 40-ms RTT target
sudo scripts/setup_p2_netns.sh
sudo scripts/apply_p2_shaping.sh --capacity-mbps 100 --delay-ms 20
sudo scripts/start_p2_relay.sh
```

Keep the relay command running in its terminal, then run:

```bash
# Minimal native P2 and P4 Forward regression smokes
sudo scripts/apply_p2_shaping.sh --capacity-mbps 19.2 --delay-ms 2
sudo scripts/run_p2_experiment.sh --config configs/p2.switch.a_to_b.draft18.yaml
sudo scripts/apply_p2_shaping.sh --capacity-mbps 100 --delay-ms 20
sudo scripts/run_p4_experiment.sh --config configs/p4.forward-smoke.f1.draft18.yaml

# Controlled mechanism smokes
sudo scripts/run_p5_experiment.sh --config configs/p5.smoke.fetch.draft18.yaml
sudo scripts/run_p5_experiment.sh --config configs/p5.smoke.join.draft18.yaml

# Full requested matrices; each stops on the first failed/invalid run
sudo scripts/run_p5_matrix.sh --series P5A --repetitions 3
sudo scripts/run_p5_matrix.sh --series P5B --repetitions 3

# Analysis
PYTHONPATH=src .venv/bin/python -m moq_360_protocol_lab.p5_analysis
```

Repeat the P1/P2/P4 commands after P5B, without rerunning prior full matrices.

The post-change P1 native regression `run_20260918T144558Z_59eb51a4` passed
with exact draft-18 negotiation, 10/10 deterministic Objects, and zero
missing/duplicate/malformed/out-of-order Objects. P2 and P4 Forward must still
be rerun before and after the controlled matrix using their existing reviewed
configs and `run_p2_experiment.sh`/`run_p4_experiment.sh`; this session could
not enter the namespaces needed for those two checks.

Cleanup is explicit and limited to the P2/P5 test topology:

```bash
sudo scripts/reset_p2_shaping.sh
# Stop the foreground relay with Ctrl-C, then:
sudo scripts/destroy_p2_netns.sh
```

Stop after P5. Synthetic results support conclusions about first Object,
complete Group, history cost, continuity, and live-edge delay—not a first
decodable frame or codec/keyframe behavior.
