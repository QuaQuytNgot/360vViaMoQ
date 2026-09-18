# P3 completion audit

Audit date: 2026-09-16. Scope is strict `draft-ietf-moq-transport-18` over
raw QUIC (`moqt-18`), `aiomoqt==0.10.6`, and OpenMOQ moqx commit
`502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6`. This closes the existing P3
evidence without changing its immutable raw artifacts.

## Final status

| Item | Final status |
|---|---|
| P3A_OBJECT_TIMEOUT | `NATIVE_PATH_CONFIRMED_BUT_MEASUREMENT_BLOCKED_BY_RELAY_RESOURCE_ERROR` |
| P3B_SUBGROUP_TIMEOUT | `BLOCKED_UNIMPLEMENTED_NATIVE_BEHAVIOR` |
| P3_OVERALL | `CHARACTERIZATION_CONCLUDED_PARTIAL` |

## Stored evidence verified

The valid control is
`results/P3/run_20260916T042601Z_03a5a93d`. Its endpoint negotiation records
both show draft 18, raw QUIC, and ALPN `moqt-18`; its summary records
1,280/1,280 received Objects, zero native stream resets, `COMPLETED`, and
`valid_for_protocol_claim=true`.

The retained 50 ms Object Delivery Timeout treatment is
`results/P3/run_20260916T042629Z_6b52ee91`. It has the same strict
negotiation and records two native `RESET_STREAM` events with error `0x02`,
1,277/1,280 Objects, and three missing Objects. That confirms invocation of
the native Object-timeout path. It is nevertheless correctly finalised as
`COMPLETED_INVALID` with `valid_for_protocol_claim=false`: its copied relay
log contains both `Failed to create uni stream` and `CrossExecFilter
beginSubgroup failed`. Those forwarding-resource failures independently
confound attribution of the missing Objects. The raw run remains preserved as
implementation evidence and is not discarded.

P3B remains blocked. `docs/P3_TIMEOUT_AUDIT.md` records that the pinned
moxygen source declares and validates `SUBGROUP_DELIVERY_TIMEOUT` key `0x06`,
but `MoQSession.cpp:getDeliveryTimeoutIfPresent()` reads only the Object
timeout key `0x02`; no subgroup timer/reset path exists. A wire encoding would
not constitute a native subgroup-timeout experiment.

## Interpretation boundary

This is not a completed performance experiment, a demonstrated MOQT timeout
bottleneck, or proof of a protocol limitation. It is a partial native
characterization: Object timeout action was observed, but the one treatment
with loss is invalid for causal measurement; subgroup timeout is unavailable
in the pinned relay implementation.
