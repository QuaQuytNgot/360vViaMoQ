"""Evidence-backed draft-18 capability gates.

The capability ledger records source/API audit results. A relay handshake or
media-path smoke test is separate evidence and cannot be inferred from a
serializer, a README, or an enabled configuration flag.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .backends import backend_for
from .models import CapabilityFinding, GateResult


LIVE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "P1": ("raw_quic", "publish", "subscribe", "subgroup_object", "priority", "relay_p1_smoke"),
    "P2": ("raw_quic", "request_update", "subscriber_priority", "relay_p2_smoke"),
    "P3": ("raw_quic", "object_delivery_timeout", "subgroup_delivery_timeout", "relay_p3_smoke"),
    "P4": ("raw_quic", "forward", "request_update", "relay_forward", "relay_cache", "relay_fanout", "relay_p4_smoke"),
    "P5": ("raw_quic", "fetch", "joining_fetch", "relay_p5_smoke"),
}


def load_capabilities(path: Path) -> tuple[dict[str, Any], dict[str, CapabilityFinding]]:
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    features = {
        name: CapabilityFinding(
            name=name,
            status=value["status"],
            evidence=tuple(value.get("evidence", [])),
            native_mapping=value.get("native_mapping"),
            notes=value.get("notes"),
        )
        for name, value in document.get("features", {}).items()
    }
    return document, features


def audit_backend(backend_name: str) -> dict[str, Any]:
    """Report public binding support only; this does not contact a relay."""
    backend = backend_for(backend_name)
    return {
        "backend": backend.name,
        "protocol_family": backend.protocol_family,
        "wire_protocol": backend.wire_protocol,
        "claim_scope": backend.claim_scope,
        "runtime": backend.runtime_info(),
        "capabilities": backend.capability_flags(),
    }


def capability_snapshot(path: Path, backend_name: str) -> dict[str, Any]:
    document, features = load_capabilities(path)
    return {
        "ledger": document,
        "local_backend": audit_backend(backend_name),
        "features": {name: asdict(finding) for name, finding in features.items()},
    }


def _expected_versions(document: Mapping[str, Any], backend_name: str) -> Mapping[str, Any]:
    backends = document.get("backends")
    if not isinstance(backends, Mapping):
        return {}
    record = backends.get(backend_name)
    return record if isinstance(record, Mapping) else {}


def gate_test(
    test_id: str,
    mode: str,
    ledger_path: Path,
    *,
    backend_name: str = "moqt18",
    probe_succeeded: bool = False,
) -> GateResult:
    if mode == "synthetic_plan":
        return GateResult(True, "COMPLETED_SYNTHETIC", ("Synthetic planning validates the harness only; it makes no protocol claim.",))
    if mode == "event_replay":
        return GateResult(True, "REPLAY_ONLY", ("Event replay does not create new protocol observations.",))
    if mode != "live":
        return GateResult(False, "SKIPPED_UNVERIFIED", (f"Unsupported runtime mode: {mode}",))

    document, features = load_capabilities(ledger_path)
    reasons: list[str] = []
    expected = _expected_versions(document, backend_name)
    local = audit_backend(backend_name)
    runtime = local["runtime"]
    if not runtime.get("module_imported"):
        reasons.append(f"{backend_name} is not installed in this environment")
    for version_key in ("aiomoqt_version", "aiopquic_version", "moq_rs_version", "moq_ffi_version"):
        expected_version = expected.get(version_key)
        actual_version = runtime.get(version_key)
        if expected_version is not None and actual_version != expected_version:
            reasons.append(f"{version_key} is {actual_version!r}; ledger requires {expected_version!r}")
    if not probe_succeeded:
        reasons.append("draft-18 raw-QUIC protocol probe did not succeed")

    required = LIVE_REQUIREMENTS.get(test_id)
    if required is None:
        reasons.append(f"unknown test ID: {test_id}")
        required = ()
    runtime_flags = local["capabilities"]
    for feature_name in required:
        if feature_name in runtime_flags:
            if not runtime_flags[feature_name]:
                reasons.append(f"{feature_name}: unavailable in the installed public backend API")
            continue
        finding = features.get(feature_name)
        if finding is None:
            reasons.append(f"no capability finding for {feature_name}")
        elif finding.status != "smoke_verified":
            reasons.append(f"{feature_name}: {finding.status}")
    return GateResult(not reasons, "READY" if not reasons else "SKIPPED_UNVERIFIED", tuple(reasons))
