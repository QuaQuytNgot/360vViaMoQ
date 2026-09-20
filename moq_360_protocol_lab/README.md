# MoQ 360 Protocol Lab

An evidence-gated Python research scaffold for tiled immersive-media
experiments over **`draft-ietf-moq-transport-18`**. The primary binding is
`aiomoqt` over raw QUIC, with one exact offer and no accepted fallback. The
lab is intentionally synthetic-first: it plans and paces Track/Group/Object
payloads, but does not implement video decoding, viewport prediction, ABR, or
a custom scheduler.

Every experiment must label its protocol as
`evaluated_protocol = draft-ietf-moq-transport-18`. This is a deliberately
versioned baseline, not “latest MOQT”.

## Current research state

`aiomoqt==0.10.6` and `aiopquic==0.3.11` are the primary pinned stack. The
selected `moqx` relay is fixed at
`502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6`. P1, P2, and the supported P4
cases have native evidence. P3 is a documented partial characterization and
P4C is blocked by the current relay. P5 Standalone and Relative Joining FETCH
semantic smokes now pass; controlled P5A/P5B execution is pending root access
to the existing network-namespace topology.

- [Architecture and claim rules](docs/DESIGN.md)
- [Draft-18 mechanism mapping](docs/DRAFT_MAPPING.md)
- [aiomoqt source/API audit](docs/AIOMOQT_API_AUDIT.md)
- [Relay candidate audit](docs/RELAY_AUDIT.md)
- [P5 FETCH/Joining FETCH audit](docs/P5_FETCH_AUDIT.md)
- [P5 execution status and results](docs/P5_RESULTS.md)
- [P1–P5 gates and smoke procedure](docs/TESTS.md)

## Layout

```text
src/moq_360_protocol_lab/
  backends.py          # ProtocolBackend, Moqt18Backend, MoqLiteBackend
  experiment.py        # planning, strict probe, evidence gates, result layout
  protocol_probe.py    # standalone exact draft-18/raw-QUIC probe
  workload.py, pacing.py, manifest.py, metrics.py, provenance.py
  protocol/             # semantic intents and observation helpers; no guessed encoding
configs/
  experiment.yaml
  capabilities.example.json
  relay.moqx.draft18.example.yaml  # qualified local P1 relay configuration
scripts/
  install.sh, install_relay.sh, start_relay.sh, stop_relay.sh, probe_relay.sh
  run_test.sh, run_sweep.sh, netem.sh, reset_netem.sh
results/
```

## Install and validate the Python environment

Python 3.12 or later is required. Install only the pinned primary stack:

```bash
cd /home/fil/Hoang/moq_360_protocol_lab
scripts/install.sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

The old `moq-rs` integration is retained only for a historical comparison:

```bash
scripts/install.sh --with-moq-lite
```

It is surfaced as `MoqLiteBackend`, records `wire_protocol = moq-lite-05`,
and can never produce a draft-18 result.

## Safe starting workflow

1. Copy `configs/experiment.yaml`, choose every research parameter explicitly,
   and retain `protocol.backend: moqt18`, `draft: 18`, and
   `transport: raw_quic`.
2. Use `synthetic_plan` first. It validates the deterministic workload/result
   plumbing and is labelled `harness_validation_not_moq`.
3. Before any live experiment, build a pinned real relay and use
   `scripts/probe_relay.sh --config YOUR_CONFIG.yaml`. It succeeds only when
   the observed protocol is draft 18, raw QUIC, and ALPN `moqt-18`.
4. Save the required P1 relay smoke (one publisher, one subscriber, one Track,
   ten deterministic Groups with payload/sequence checks) before changing the
   P1 ledger status. P2–P5 have stricter individual gates.

The committed example has intentionally blank research and relay values, so it
does not accidentally start a live experiment. A failed probe is retained in
`results/<test>/.../protocol_negotiation.json` and ends the run before any
experiment traffic begins.

For a later media-mode **plan**, index existing fragments without altering
them:

```bash
PYTHONPATH=src .venv/bin/python -m moq_360_protocol_lab.media_manifest \
  --fragment-root ../360_codec_pipeline/media/fragmented/h264 \
  --group-duration-ms YOUR_CONFIGURED_DURATION \
  --output your_media_manifest.jsonl
```

The primary implementation work remains deterministic synthetic payloads.
Media delivery is not enabled until a native draft-18 adapter and relay smoke
are independently verified.

## Relay and network safety

`scripts/install_relay.sh` is dry-run by default and pins the audited `moqx`
source commit. `start_relay.sh` accepts only an explicit binary and YAML
configuration. The YAML constrains the local listener to `moqt_versions: [18]`.

`netem.sh` is dry-run by default; `--apply` changes a named interface. It
models outbound one-way delay unless both directions are shaped and recorded.

## Native P1 execution (after relay qualification)

`Moqt18P1Adapter` is the P1-only native path.  It creates independent
aiomoqt publisher and subscriber sessions, offers only draft 18 over raw QUIC,
and sends deterministic identity-carrying Objects as one subgroup stream per
Group.  It does not use a Python queue as a data path.

The two live templates are deliberately not runnable until the relay is
built and started.  First run the 10-Group smoke:

```bash
PYTHONPATH=src .venv/bin/python -m moq_360_protocol_lab.p1_runner \
  --config configs/p1.smoke.draft18.yaml
```

It saves publisher/subscriber negotiation JSON separately, event logs,
publisher/subscriber CSV, `groups.csv`, a relay-log copy, provenance, and a
summary under `results/P1/run_*`.  The capability ledger remains unchanged by
this command; promote `relay_p1_smoke` only after reviewing a valid smoke
result.  Then generate reviewed, constant-aggregate cases and run each one:

```bash
PYTHONPATH=src .venv/bin/python -m moq_360_protocol_lab.p1_matrix \
  --baseline configs/p1.baseline.draft18.yaml --output-dir configs/generated/P1 \
  --track-counts 1 2 4 8 16 24
PYTHONPATH=src .venv/bin/python -m moq_360_protocol_lab.p1_analysis
```

The matrix reads `workload.total_offered_bitrate_mbps` from the reviewed
baseline and distributes it with integer remainder accounting; it contains no
embedded offered-rate research constant.  `p1_analysis` produces a compact
table and four intentionally simple SVG plots from valid `p1_measurement`
rows only; smoke rows remain in the raw CSV evidence.

## Result validity

Each run saves the effective config, capability ledger and local backend audit,
provenance, result tables, `protocol_negotiation.json`, and `summary.json`.
`valid_for_protocol_claim` stays false unless exact negotiation, native
mechanism support, and saved relay evidence all exist. Missing observations are
represented as missing values, never as zero.

## Native P5 late join/FETCH

P5 keeps the source genuinely live and paced, and compares only native
draft-18 operations: LATEST_OBJECT live SUBSCRIBE, Standalone Fetch plus live
SUBSCRIBE, and Relative Joining Fetch. The project-local compatibility adapter
only corrects aiomoqt 0.10.6's draft-18 FETCH data-stream `vi64`/location-delta
codec; it does not cache or replay media.

Run the two localhost semantics smokes with:

```bash
scripts/run_p5_local_smoke.sh --config configs/p5.smoke.fetch.local.draft18.yaml
scripts/run_p5_local_smoke.sh --config configs/p5.smoke.join.local.draft18.yaml
```

Controlled P5A/P5B commands, metric definitions, monitoring, and the current
permission block are recorded in [docs/P5_RESULTS.md](docs/P5_RESULTS.md).
