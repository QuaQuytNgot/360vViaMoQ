"""Run provenance collection.  Missing tools are recorded, never fabricated."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from .backends import backend_for


def _command_output(args: list[str], cwd: Path | None = None) -> str | None:
    try:
        return subprocess.check_output(args, cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture_provenance(
    repository_root: Path,
    config_path: Path,
    capability_path: Path,
    protocol: Mapping[str, Any],
    relay: Mapping[str, Any] | None,
    claim_scope: str,
) -> dict[str, Any]:
    memory_total_kib = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                memory_total_kib = int(line.split()[1])
                break
    except (OSError, ValueError, IndexError):
        pass
    backend_name = protocol.get("backend")
    if not isinstance(backend_name, str):
        backend_name = "unknown"
    try:
        backend = backend_for(backend_name)
        binding = backend.runtime_info()
        protocol_record = {
            "evaluated_protocol": "draft-ietf-moq-transport-18" if backend.name == "moqt18" else "moq-lite-05",
            "protocol_family": backend.protocol_family,
            "wire_protocol": backend.wire_protocol,
            "protocol_draft": "draft-ietf-moq-transport-18" if backend.name == "moqt18" else None,
            "implementation": "aiomoqt" if backend.name == "moqt18" else "moq-rs",
            "implementation_version": binding.get("aiomoqt_version") if backend.name == "moqt18" else binding.get("moq_rs_version"),
            "aiomoqt_version": binding.get("aiomoqt_version"),
            "aiopquic_version": binding.get("aiopquic_version"),
            "selected_draft": protocol.get("draft"),
            "transport_mode": protocol.get("transport"),
            "claim_scope": claim_scope,
        }
    except Exception as exc:
        binding = {"error": f"{type(exc).__name__}: {exc}"}
        protocol_record = {
            "evaluated_protocol": None,
            "protocol_family": None,
            "wire_protocol": None,
            "protocol_draft": None,
            "implementation": None,
            "implementation_version": None,
            "selected_draft": protocol.get("draft"),
            "transport_mode": protocol.get("transport"),
            "claim_scope": claim_scope,
        }
    relay = relay or {}
    return {
        "python": {"version": sys.version, "executable": sys.executable},
        "system": {
            "platform": platform.platform(), "kernel": platform.release(), "machine": platform.machine(),
            "hostname": platform.node(), "processor": platform.processor() or None,
            "memory_total_kib": memory_total_kib,
            "nvidia": _command_output(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"]),
        },
        "git": {
            "commit": _command_output(["git", "rev-parse", "HEAD"], repository_root),
            "dirty": bool(_command_output(["git", "status", "--porcelain"], repository_root)),
        },
        "binding": binding,
        "protocol": protocol_record,
        "relay": {
            "relay_implementation": relay.get("implementation") or os.environ.get("MOQ_RELAY_IMPLEMENTATION") or None,
            "relay_version": relay.get("version") or os.environ.get("MOQ_RELAY_VERSION") or None,
            "relay_commit": relay.get("commit") or os.environ.get("MOQ_RELAY_COMMIT") or None,
            "transport_mode": protocol.get("transport"),
            "address": relay.get("address") or None,
            "port": relay.get("port") or None,
            "build_flags": relay.get("build_flags") or [],
        },
        "config_sha256": sha256_file(config_path),
        "capability_ledger_sha256": sha256_file(capability_path),
        "clock": {"name": "time.monotonic_ns", "cross_host_note": "Do not subtract independent host monotonic clocks without captured synchronization uncertainty."},
    }
