# Draft-18 experiment mapping

This lab intentionally evaluates **`draft-ietf-moq-transport-18`**, published
12 May 2026. It does not call this baseline “latest MOQT”. The normative
reference is the [versioned IETF draft](https://www.ietf.org/archive/id/draft-ietf-moq-transport-18.html).

| Experiment | Draft-18 mechanism | Primary measurements | Preconditions |
|---|---|---|---|
| P1 | Tracks/Groups (§§2.3–2.4), subscriptions (§5.1), priorities (§7), `SUBSCRIBE` (§10.7), subgroup streams (§11.4) | aggregate/per-track/weakest goodput, object and Group completion, cross-tile skew, p50/p95/p99 completion latency, deadlines | draft-18 raw-QUIC probe plus deterministic 10-Group relay pub/sub smoke |
| P2 | `REQUEST_UPDATE` (§10.9), `REQUEST_OK`/`REQUEST_ERROR` (§§10.5–10.6), `SUBSCRIBER_PRIORITY` (§10.2.7) | generated/sent/response/effective/new-priority-object timestamps | conformant update sender, relay response, and effective scheduling evidence |
| P3 | delivery-timeout behavior (§8), `SUBGROUP_DELIVERY_TIMEOUT` (§§10.2.3, 12.1), `OBJECT_DELIVERY_TIMEOUT` (§§10.2.4, 12.2) | completed/expired/reset/useful/stale bytes, skew, media deadline misses | native timeout configuration and native reset/expiry observations; playback deadline remains independent |
| P4 | Forward State (§5.1), cache and Forward relay behavior (§§9.1–9.2, 9.5), `FORWARD` (§10.2.12) | first useful Object/Group, pre-demand bytes, cache reuse, publisher→relay/relay→subscriber bytes, duplicate upstream bytes, fan-out reuse | verified relay Forward propagation, cache/reuse telemetry, multi-user fan-out, and dynamic update support |
| P5 | joining a track (§5.1.3), `FETCH` and Joining Fetch (§10.12) | first Object/useful Group, pre-live-edge bytes, catch-up and live-edge latency | raw-QUIC draft-18 FETCH/joining-FETCH relay smoke |

## Interpretation rules

- Lower numerical priority is higher priority. Equal-priority scheduling across
  independently delivered Tracks is a property to measure, not a promised
  fairness rule.
- P2 distinguishes a control response from effective scheduling reaction. A
  `REQUEST_OK` never proves that subsequent media was scheduled differently.
- P3 reports protocol delivery lifetime and application playback deadline as
  separate fields. Neither substitutes for the other.
- P4 does not synthesize cache hits, pre-warming, or upstream reuse in Python.
  Missing relay evidence blocks P4.
- P5 uses the exact draft-18 term **Joining Fetch**. No later-draft fill or
  new-group mechanism is imported into this baseline.
