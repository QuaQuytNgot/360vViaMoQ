# P4 Forward / relay fan-out audit

Audit date: 2026-09-16. Baseline: `draft-ietf-moq-transport-18`, installed
`aiomoqt==0.10.6`, and OpenMOQ moqx
`502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6`. This is a source audit, not an
assertion that a path has already been measured end-to-end.

Draft-18 defines `FORWARD` (`0x10`) as uint8 0 or 1; it may occur in
`SUBSCRIBE` and subscription `REQUEST_UPDATE`. `REQUEST_UPDATE` uses the
same bidi stream as the request and receives exactly one response. Draft-18
also discusses relay upstream forwarding as an implementation policy; it does
not equate Forward=0 with caching. See sections 5.1, 9.2, 9.4, 10.2.12, and
10.9 of the [draft-18 text](https://datatracker.ietf.org/doc/html/draft-ietf-moq-transport-18).

| Capability | Draft-18 | aiomoqt wire | Python API | moqx | Observable | Runnable |
|---|---|---|---|---|---|---|
| Forward initial state | `0x10`, `SUBSCRIBE` | `types.py:ParamType.FORWARD`; `context.py:PROFILES[18].uint8_params` | `MOQTSessionQuic.subscribe(..., forward=...)` | `MoqxRelay.cpp:subscribe*`, `MoQForwarder::addSubscriber` retain per-sub state | subscriber Objects and SUBSCRIBE response | Yes |
| Forward update | `REQUEST_UPDATE` on original bidi stream | `messages/request.py:RequestUpdate` serializes d18 | no stock sender; project `aiomoqt_d18_update.send_forward_update` reuses P2 stream sender | `MoqxRelay.cpp:forwardChangedImpl`, `doSubscribeUpdate` | update JSONL/CSV and `REQUEST_OK` | Yes |
| downstream Forward state | subscription state | codec accepts 0/1 fixed uint8 | initial and update adapter paths | `numForwardingSubscribers()` controls channel forwarding | received Objects and update response; no per-sub relay timestamp | Yes |
| upstream propagation | relay policy, not a cache guarantee | n/a | n/a | `makeUpstreamSubReq`; `ChannelForwarderCallback::forwardChanged` invokes `doSubscribeUpdate` | relay debug log only; default stats have aggregate update counters | Yes, indirect |
| subscription aggregation | relay may share upstream work | n/a | independent client sessions supported | `SubscriptionRegistry::getOrCreateFromSubscribe` joins same FTN; `joinOrPrepareUpstreamSubscription` has first/subsequent paths | upstream payload/counters; registry not exposed per run | Yes, payload evidence |
| relay cache | cache is implementation-specific | n/a | n/a | `MoqxCache::getSubscribeWriteback`, `fetch`, `makePassiveConsumer` | cache internals/admin, no P4 cache-hit event schema | Exists, but not a Forward result |
| cached Object reuse | not implied by Forward | n/a | no P4 API | cache reuse is in `MoqxCache::fetchImpl`/FETCH path | no direct Forward-path hit signal | Not established for P4 |
| pre-warm while downstream inactive | not implied | n/a | no policy API | `MoqxRelay.cpp` comments/logic set upstream Forward only with forwarding subscribers; no intentional inactive pre-warm hook found | no pre-demand cache telemetry | **Blocked** |
| multi-user fan-out | relays forward matching subscriptions | independent `MOQTClient` sessions | P4 creates independent user sessions | shared FTN forwarder/registry and per-subscriber channel subscribers | user-specific receiver logs; aggregate byte totals | Yes |

## Interpretation

The pinned moqx source supports a genuine Forward propagation path and direct
track subscription aggregation. It also contains a cache, but source shows
that cache’s writeback/FETCH features; it does **not** prove that an inactive
downstream Forward subscription keeps upstream media flowing into that cache.
Accordingly `P4C_PREWARM=BLOCKED_BY_CURRENT_RELAY`. P4 must not call dormant
Forward state “pre-warming,” and it must not report a cache-hit rate.

## Harness mapping

`p4_adapter.py` keeps the publisher wall-clock paced from the manifest and
starts it independently of demand. It sends initial Forward in `SUBSCRIBE`;
`send_forward_update()` is the P2 request-update mechanism generalized only
to parameter `FORWARD`, preserving the original subscription bidi stream and
single in-flight response rule. `p4_runner.py` writes demand/update/receive
timestamps and labels its byte totals `*_payload_bytes`; they are not
unverified QUIC wire-byte counters.

### Measurement validity instrumentation

The P4A rerun harness writes lifecycle phases to `run_phases.events.jsonl`,
including session establishment, measurement boundaries, demand/update/ACK,
and endpoint teardown. `run_p4_experiment.sh` starts a 0.5-second lightweight
`monitor.csv` before endpoint launch and stops it after endpoint teardown. It
samples relay/publisher/subscriber liveness, CPU/RSS, and the publisher plus
relay-downstream interface counters.

The relay remains one long-lived process; the script records the byte offset
of `/tmp/moq-p2-relay.log` at run start and extracts only bytes appended up to
run end into that run's `relay.log`, with offsets in `relay_log_scope.json`.
The finalizer classifies resource signatures as fatal, non-resource request or
protocol errors as active-window errors, and `requestUpdate failed: Session
closed` as a retained teardown warning only when a teardown phase exists.

The initial four P4A directories remain immutable exploratory evidence:
`run_20260916T061926Z_d12e8bb6`, `run_20260916T061942Z_2a73d61a`,
`run_20260916T061959Z_e04fd06e`, and `run_20260916T062016Z_7a83ae3a`.
They are excluded from the clean rerun aggregate as `validation_incomplete`;
the harness always creates a fresh run ID and never overwrites raw evidence.

## Reproduction

The native topology requires root because it uses the already-established,
isolated `moq-p2-pub` / `moq-p2-relay` / `moq-p2-sub` netns-veth topology.
Run the following in separate terminals (do not shape a host interface):

```bash
sudo scripts/setup_p2_netns.sh
sudo scripts/start_p2_relay.sh
sudo scripts/run_p4_experiment.sh --config configs/p4.forward-smoke.f1.draft18.yaml
sudo scripts/run_p4_experiment.sh --config configs/p4.forward-smoke.f0.draft18.yaml
sudo scripts/run_p4_experiment.sh --config configs/p4.forward-smoke.0-to-1.draft18.yaml
sudo scripts/run_p4_experiment.sh --config configs/p4.forward-smoke.1-to-0.draft18.yaml
sudo scripts/run_p4_experiment.sh --config configs/p4.reactive.8track.draft18.yaml
sudo scripts/run_p4_experiment.sh --config configs/p4.dormant.8track.draft18.yaml
sudo scripts/run_p4_experiment.sh --config configs/p4.fanout.2user.50overlap.draft18.yaml
PYTHONPATH=src .venv/bin/python -m moq_360_protocol_lab.p4_analysis --results results/P4
sudo scripts/destroy_p2_netns.sh
```

Use distinct invocations for repetitions; `p4_runner` creates unique run IDs
and refuses to overwrite raw evidence. Run the existing P1 and P2 smoke
scripts before and after a measurement campaign. If relay logs contain the
P3 resource-failure markers, preserve the run and classify it invalid rather
than changing limits in place.
