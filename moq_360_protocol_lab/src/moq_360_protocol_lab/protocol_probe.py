"""Command-line entry point for an exact, non-fallback protocol probe."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Mapping

from .backends import BackendError, backend_for
from .config import ConfigurationError, load_yaml


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{name} must be a mapping")
    return value


async def probe_config(config: Mapping[str, Any]) -> dict[str, Any]:
    protocol = _mapping(config.get("protocol"), "protocol")
    relay = _mapping(config.get("relay"), "relay")
    backend_name = protocol.get("backend")
    if backend_name != "moqt18" or protocol.get("draft") != 18 or protocol.get("transport") != "raw_quic":
        raise ConfigurationError(
            "protocol probe requires protocol.backend=moqt18, protocol.draft=18, and protocol.transport=raw_quic"
        )
    backend = backend_for(backend_name)
    result = await backend.probe(relay)
    return result.as_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe one exact MoQT wire protocol; never accept a fallback.")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(probe_config(load_yaml(args.config)))
    except (BackendError, ConfigurationError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
