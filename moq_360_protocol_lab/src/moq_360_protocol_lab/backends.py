"""Protocol backends and strict draft-selection probes.

The harness deliberately keeps experiment logic out of a protocol binding.  A
backend states what it can prove locally; a live run separately proves the
negotiated wire protocol before it is allowed to make a protocol claim.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import inspect
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Mapping


class BackendError(RuntimeError):
    """A backend cannot establish the explicitly requested protocol."""


def distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


@dataclass(frozen=True)
class ProtocolProbe:
    requested_draft: int | None
    negotiated_draft: int | None
    transport: str
    alpn: str | None
    relay: Mapping[str, Any]
    success: bool
    reason: str | None = None
    backend: str = ""

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["relay"] = dict(self.relay)
        return value


class ProtocolBackend(ABC):
    """Narrow common surface for the two non-interchangeable backends."""

    name: str
    protocol_family: str
    wire_protocol: str
    claim_scope: str

    @abstractmethod
    def runtime_info(self) -> dict[str, Any]:
        """Return installed versions and public API observations only."""

    @abstractmethod
    def capability_flags(self) -> dict[str, bool]:
        """Return capabilities that are real in this binding, not plans."""

    @abstractmethod
    async def probe(self, relay: Mapping[str, Any]) -> ProtocolProbe:
        """Attempt an exact wire-protocol handshake or raise BackendError."""


class Moqt18Backend(ProtocolBackend):
    """`aiomoqt` backend pinned to raw-QUIC draft-18 only.

    This object uses the public ``MOQTClient(..., supported_drafts=18)`` API.
    A singleton offer is intentional: offering a draft list would permit a
    lower-draft fallback and invalidate the experiment.
    """

    name = "moqt18"
    protocol_family = "moqt"
    wire_protocol = "moqt-18"
    claim_scope = "draft18_native"
    requested_draft = 18
    expected_alpn = "moqt-18"

    def runtime_info(self) -> dict[str, Any]:
        module_imported = False
        symbols: dict[str, bool] = {}
        try:
            client = importlib.import_module("aiomoqt.client")
            protocol = importlib.import_module("aiomoqt.protocol")
            module_imported = True
            for name in ("MOQTClient",):
                symbols[name] = hasattr(client, name)
            # These public methods are the source-audited P1/P5 surface.
            session = getattr(protocol, "MOQTSessionQuic", None)
            for name in ("subscribe", "publish", "fetch", "join", "subgroup_header"):
                symbols[f"session.{name}"] = bool(session and hasattr(session, name))
            if session is not None:
                for method_name, parameter_name in (
                    ("subscribe", "priority"),
                    ("subscribe", "forward"),
                    ("publish", "forward"),
                ):
                    method = getattr(session, method_name, None)
                    try:
                        symbols[f"session.{method_name}.{parameter_name}"] = (
                            method is not None and parameter_name in inspect.signature(method).parameters
                        )
                    except (TypeError, ValueError):
                        symbols[f"session.{method_name}.{parameter_name}"] = False
        except ImportError:
            pass
        return {
            "aiomoqt_version": distribution_version("aiomoqt"),
            "aiopquic_version": distribution_version("aiopquic"),
            "module_imported": module_imported,
            "symbols": symbols,
        }

    def capability_flags(self) -> dict[str, bool]:
        runtime = self.runtime_info()
        public = runtime["symbols"]
        available = bool(runtime["module_imported"])
        try:
            from .aiomoqt_d18_update import send_subscriber_priority_update
            local_d18_update = callable(send_subscriber_priority_update)
        except ImportError:
            local_d18_update = False
        return {
            "raw_quic": available and bool(public.get("MOQTClient")),
            "subscribe": available and bool(public.get("session.subscribe")),
            "publish": available and bool(public.get("session.publish")),
            "subgroup_object": available and bool(public.get("session.subgroup_header")),
            "priority": available and bool(public.get("session.subscribe.priority")),
            # aiomoqt 0.10.6 lacks a public sender.  The project-local,
            # version-pinned wrapper is deliberately audited and smoke-tested
            # separately; it uses the existing request bidi stream rather
            # than inventing a new control-plane API.
            "request_update": available and local_d18_update,
            "subscriber_priority": available and local_d18_update,
            # It serializes object timeout parameter 0x02 but exposes neither
            # native expiry action nor subgroup timeout property 0x06.
            "object_delivery_timeout": False,
            "subgroup_delivery_timeout": False,
            # Initial Forward is public on PUBLISH/SUBSCRIBE. Dynamic changes
            # require REQUEST_UPDATE, so P4 remains separately blocked.
            "forward": available and bool(
                public.get("session.subscribe.forward") and public.get("session.publish.forward")
            ),
            "fetch": available and bool(public.get("session.fetch")),
            "joining_fetch": available and bool(public.get("session.join")),
            "relay_cache": False,
        }

    async def probe(self, relay: Mapping[str, Any]) -> ProtocolProbe:
        host = relay.get("address")
        port = relay.get("port")
        path = relay.get("path", "")
        verify_tls = relay.get("verify_tls", True)
        timeout_s = relay.get("probe_timeout_s", 10)
        if not isinstance(host, str) or not host:
            raise BackendError("relay.address is required for a live draft-18 probe")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise BackendError("relay.port must be an integer in 1..65535 for a live draft-18 probe")
        if not isinstance(path, str):
            raise BackendError("relay.path must be a string")
        if not isinstance(verify_tls, bool):
            raise BackendError("relay.verify_tls must be boolean")
        if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise BackendError("relay.probe_timeout_s must be positive")
        try:
            from aiomoqt.client import MOQTClient
        except ImportError as exc:
            raise BackendError("aiomoqt is not installed; run scripts/install.sh") from exc

        relay_identity = {
            key: relay.get(key)
            for key in ("implementation", "version", "commit", "address", "port", "path")
        }
        try:
            client = MOQTClient(
                host,
                port,
                path=path,
                use_quic=True,
                verify_tls=verify_tls,
                supported_drafts=18,
            )
            async with asyncio.timeout(float(timeout_s)):
                async with client.connect() as session:
                    await session.client_session_init(timeout=max(1, int(timeout_s)))
                    negotiated_draft = getattr(session, "negotiated_draft", None)
                    quic = getattr(session, "_quic", None)
                    alpn_reader = getattr(quic, "_negotiated_alpn", None)
                    alpn = alpn_reader(None) if callable(alpn_reader) else None
                    # aiomoqt derives ``negotiated_draft`` from the raw-QUIC
                    # ProtocolNegotiated event. aiopquic 0.3.11 exposes the
                    # corresponding ALPN query on the live connection. Keep
                    # an unavailable private observation explicit; never fill
                    # it with the expected value.
                    success = negotiated_draft == self.requested_draft and alpn == self.expected_alpn
                    reason = None if success else (
                        f"required draft/alpn is 18/{self.expected_alpn}; "
                        f"observed draft/alpn is {negotiated_draft!r}/{alpn!r}"
                    )
                    return ProtocolProbe(
                        requested_draft=self.requested_draft,
                        negotiated_draft=negotiated_draft if isinstance(negotiated_draft, int) else None,
                        transport="raw_quic",
                        alpn=alpn if isinstance(alpn, str) else None,
                        relay=relay_identity,
                        success=success,
                        reason=reason,
                        backend=self.name,
                    )
        except Exception as exc:
            return ProtocolProbe(
                requested_draft=self.requested_draft,
                negotiated_draft=None,
                transport="raw_quic",
                alpn=None,
                relay=relay_identity,
                success=False,
                reason=f"draft-18 probe failed: {type(exc).__name__}: {exc}",
                backend=self.name,
            )


class MoqLiteBackend(ProtocolBackend):
    """Historical comparison backend. It is intentionally not draft-18."""

    name = "moq_lite"
    protocol_family = "moq-lite"
    wire_protocol = "moq-lite-05"
    claim_scope = "moq_lite_only"

    def runtime_info(self) -> dict[str, Any]:
        try:
            module = importlib.import_module("moq")
        except ImportError:
            module = None
        return {
            "moq_rs_version": distribution_version("moq-rs"),
            "moq_ffi_version": distribution_version("moq-ffi"),
            "module_imported": module is not None,
        }

    def capability_flags(self) -> dict[str, bool]:
        installed = bool(self.runtime_info()["module_imported"])
        return {
            "raw_quic": installed,
            "subscribe": installed,
            "publish": installed,
            "subgroup_object": installed,
            "priority": installed,
            "request_update": False,
            "subscriber_priority": False,
            "object_delivery_timeout": False,
            "subgroup_delivery_timeout": False,
            "forward": False,
            "fetch": False,
            "joining_fetch": False,
            "relay_cache": False,
        }

    async def probe(self, relay: Mapping[str, Any]) -> ProtocolProbe:
        raise BackendError(
            "moq_lite is a historical comparison backend and cannot satisfy a draft-18 probe"
        )


def backend_for(name: str) -> ProtocolBackend:
    if name == "moqt18":
        return Moqt18Backend()
    if name == "moq_lite":
        return MoqLiteBackend()
    raise BackendError(f"unknown protocol backend: {name!r}")
