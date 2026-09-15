"""Explicit configuration loading and validation without research defaults."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


class ConfigurationError(ValueError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise ConfigurationError("PyYAML is required; run scripts/install.sh before loading YAML") from exc
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"cannot read configuration: {path}") from exc
    if not isinstance(content, dict):
        raise ConfigurationError("top-level YAML document must be a mapping")
    return content


def dump_yaml(path: Path, data: Mapping[str, Any]) -> None:
    try:
        import yaml
    except ImportError as exc:
        raise ConfigurationError("PyYAML is required; run scripts/install.sh before writing YAML") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(data), sort_keys=False), encoding="utf-8")


def nested_get(document: Mapping[str, Any], dotted_key: str) -> Any:
    current: Any = document
    for key in dotted_key.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def validate_for_run(config: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if config.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    mode = nested_get(config, "runtime.mode")
    if mode not in {"synthetic_plan", "event_replay", "live"}:
        errors.append("runtime.mode must be synthetic_plan, event_replay, or live")
    protocol = config.get("protocol")
    if not isinstance(protocol, Mapping):
        errors.append("protocol must be a mapping")
    else:
        backend = protocol.get("backend")
        draft = protocol.get("draft")
        transport = protocol.get("transport")
        if backend not in {"moqt18", "moq_lite"}:
            errors.append("protocol.backend must be moqt18 or moq_lite")
        elif backend == "moqt18":
            if draft != 18:
                errors.append("protocol.draft must be exactly 18 for the moqt18 backend")
            if transport != "raw_quic":
                errors.append("protocol.transport must be raw_quic for the moqt18 backend")
        elif backend == "moq_lite":
            if draft != 5:
                errors.append("protocol.draft must be exactly 5 for the moq_lite backend")
            if transport != "raw_quic":
                errors.append("protocol.transport must be raw_quic for the moq_lite backend")
    required = ["experiment.test_id", "experiment.name", "experiment.duration_s"]
    test_id = nested_get(config, "experiment.test_id")
    if test_id not in {f"P{number}" for number in range(1, 6)} and test_id not in {None, ""}:
        errors.append("experiment.test_id must be P1 through P5")
    if mode in {"synthetic_plan", "live"}:
        required.append("workload.media_mode")
        media_mode = nested_get(config, "workload.media_mode")
        if media_mode == "synthetic":
            required += [
                "workload.track_count", "workload.group_duration_ms", "workload.group_count",
                "workload.objects_per_group", "workload.seed", "workload.track_ids",
                "workload.tile_ids", "workload.per_track_bitrate_bps",
            ]
        elif media_mode == "media":
            required.append("workload.media_manifest")
        elif media_mode not in {None, ""}:
            errors.append("workload.media_mode must be synthetic or media")
    for key in required:
        value = nested_get(config, key)
        if value is None or value == "" or value == []:
            errors.append(f"Missing {key}; configure it explicitly")
    if mode == "live":
        relay = config.get("relay")
        if not isinstance(relay, Mapping):
            errors.append("relay must be a mapping for live runs")
        else:
            for key in ("implementation", "version", "commit", "address", "port"):
                value = relay.get(key)
                if value is None or value == "":
                    errors.append(f"Missing relay.{key}; configure it explicitly for a live run")
            if relay.get("port") is not None and (not isinstance(relay.get("port"), int) or not 1 <= relay["port"] <= 65535):
                errors.append("relay.port must be an integer in 1..65535")
    return errors


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
