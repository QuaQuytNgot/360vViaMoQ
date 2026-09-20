"""Narrow aiomoqt 0.10.6 adapter for draft-18 FETCH data streams.

aiomoqt's control-plane ``fetch()``/``join()`` methods already emit draft-18
FETCH requests correctly.  Its fetch *data* decoder, however, still uses the
draft-16 QUIC-varint and absolute-location implementation.  Draft-18 uses
vi64 throughout and delta-codes locations after the first Fetch Object.

This module changes only ``FetchHeader`` and ``FetchObject`` serialization /
parsing.  The experiment applies it explicitly in P5 endpoint processes; it
does not add a cache, replay Objects, or synthesize joining behavior.
"""

from __future__ import annotations

from typing import Any


_APPLIED = False


def apply_aiomoqt_d18_fetch_patch() -> None:
    """Install the minimal draft-18 FETCH data-codec correction once."""

    global _APPLIED
    if _APPLIED:
        return

    from aiomoqt.messages.base import BUF_SIZE
    from aiomoqt.messages.track import (
        FETCH_FLAGS_END_NON_EXISTENT,
        FETCH_FLAGS_END_UNKNOWN,
        FETCH_FLAG_DATAGRAM,
        FETCH_FLAG_EXTENSIONS_PRESENT,
        FETCH_FLAG_GROUP_ID_PRESENT,
        FETCH_FLAG_OBJECT_ID_PRESENT,
        FETCH_FLAG_PRIORITY_PRESENT,
        FETCH_FLAG_SG_PRESENT,
        FETCH_FLAG_SG_PRIOR,
        FETCH_FLAG_SG_PRIOR_PLUS,
        FETCH_FLAG_SG_ZERO,
        FETCH_FLAG_SUBGROUP_MASK,
        FetchHeader,
        FetchObject,
    )
    from aiomoqt.types import DataStreamType, MOQTDraft, MOQT_DEFAULT_PRIORITY, ObjectStatus
    from aiomoqt.utils.buffer import Buffer

    original_header_serialize = FetchHeader.serialize
    original_header_deserialize = FetchHeader.deserialize.__func__
    original_object_serialize = FetchObject.serialize
    original_object_deserialize = FetchObject.deserialize.__func__

    def header_serialize(self: Any, *, draft: int = 18) -> bytes:
        if draft != MOQTDraft.DRAFT_18:
            return original_header_serialize(self)
        buf = Buffer(capacity=BUF_SIZE, vi64=True)
        buf.push_vint(DataStreamType.FETCH_HEADER)
        buf.push_vint(self.request_id)
        return buf.data_slice(0, buf.tell())

    def header_deserialize(cls: type, buf: Buffer, *, draft: int = 18) -> Any:
        if draft != MOQTDraft.DRAFT_18:
            return original_header_deserialize(cls, buf)
        buf.vi64 = True
        return cls(request_id=buf.pull_vint())

    def encode_properties(buf: Buffer, properties: dict[int, Any] | None) -> None:
        if not properties:
            buf.push_vint(0)
            return
        block = Buffer(capacity=BUF_SIZE, vi64=True)
        for key, value in properties.items():
            block.push_vint(key)
            if key % 2 == 0:
                block.push_vint(int(value))
            else:
                raw = value.encode() if isinstance(value, str) else bytes(value)
                block.push_vint(len(raw))
                block.push_bytes(raw)
        raw = block.data_slice(0, block.tell())
        buf.push_vint(len(raw))
        buf.push_bytes(raw)

    def decode_properties(buf: Buffer) -> dict[int, Any] | None:
        length = buf.pull_vint()
        if length == 0:
            return None
        end = buf.tell() + length
        properties: dict[int, Any] = {}
        while buf.tell() < end:
            key = buf.pull_vint()
            if key % 2 == 0:
                properties[key] = buf.pull_vint()
            else:
                value_length = buf.pull_vint()
                properties[key] = buf.pull_bytes(value_length)
        if buf.tell() != end:
            raise ValueError("draft-18 FETCH Object Properties overrun")
        return properties

    def object_serialize(self: Any, *, prof: Any) -> Buffer:
        if prof.draft != MOQTDraft.DRAFT_18:
            return original_object_serialize(self, prof=prof)
        buf = Buffer(capacity=BUF_SIZE + len(self.payload), vi64=True)
        if self.end_of_range is not None:
            buf.push_vint(self.end_of_range)
            buf.push_vint(self.group_id)
            buf.push_vint(self.object_id)
            return buf
        flags = (
            FETCH_FLAG_SG_PRESENT
            | FETCH_FLAG_OBJECT_ID_PRESENT
            | FETCH_FLAG_GROUP_ID_PRESENT
            | FETCH_FLAG_PRIORITY_PRESENT
        )
        if self.extensions:
            flags |= FETCH_FLAG_EXTENSIONS_PRESENT
        buf.push_vint(flags)
        # Fully explicit is valid for the first Object.  This sender helper is
        # used only by tests; production P5 receives native moqx data.
        buf.push_vint(self.group_id)
        buf.push_vint(self.subgroup_id)
        buf.push_vint(self.object_id)
        buf.push_uint8(self.publisher_priority)
        if self.extensions:
            encode_properties(buf, self.extensions)
        buf.push_vint(len(self.payload))
        if self.payload:
            buf.push_bytes(self.payload)
        elif self.status != ObjectStatus.NORMAL:
            # Retain aiomoqt's empty/status convention for its loopback API.
            buf.push_vint(int(self.status))
        return buf

    def object_deserialize(
        cls: type,
        buf: Buffer,
        prior: Any = None,
        *,
        prof: Any,
        group_order_ascending: bool = True,
    ) -> Any:
        if prof.draft != MOQTDraft.DRAFT_18:
            return original_object_deserialize(cls, buf, prior=prior, prof=prof)

        buf.vi64 = True
        flags = buf.pull_vint()
        if flags in (FETCH_FLAGS_END_NON_EXISTENT, FETCH_FLAGS_END_UNKNOWN):
            return cls(group_id=buf.pull_vint(), object_id=buf.pull_vint(),
                       end_of_range=flags, payload=b"")
        if flags >= 0x80:
            raise ValueError(f"FetchObject: invalid serialization flags 0x{flags:x}")
        group_present = bool(flags & FETCH_FLAG_GROUP_ID_PRESENT)
        object_present = bool(flags & FETCH_FLAG_OBJECT_ID_PRESENT)
        if prior is None and (not group_present or not object_present):
            raise ValueError("FetchObject: first object must carry Group/Object ID")

        if prior is None:
            group_id = buf.pull_vint()
        elif group_present:
            delta = buf.pull_vint()
            group_id = (prior.group_id + delta + 1 if group_order_ascending
                        else prior.group_id - (delta + 1))
            if group_id < 0:
                raise ValueError("FetchObject: descending Group ID underflow")
        else:
            group_id = prior.group_id

        subgroup_mode = flags & FETCH_FLAG_SUBGROUP_MASK
        if flags & FETCH_FLAG_DATAGRAM:
            subgroup_id = 0
        elif subgroup_mode == FETCH_FLAG_SG_ZERO:
            subgroup_id = 0
        elif subgroup_mode == FETCH_FLAG_SG_PRIOR:
            if prior is None:
                raise ValueError("FetchObject: missing prior Subgroup ID")
            subgroup_id = prior.subgroup_id
        elif subgroup_mode == FETCH_FLAG_SG_PRIOR_PLUS:
            if prior is None:
                raise ValueError("FetchObject: missing prior Subgroup ID")
            subgroup_id = prior.subgroup_id + 1
        else:
            subgroup_id = buf.pull_vint()

        if object_present:
            object_delta = buf.pull_vint()
            object_id = object_delta if prior is None or group_present else prior.object_id + object_delta
        else:
            if prior is None:
                raise ValueError("FetchObject: missing prior Object ID")
            object_id = prior.object_id + 1

        publisher_priority = (
            buf.pull_uint8() if flags & FETCH_FLAG_PRIORITY_PRESENT
            else prior.publisher_priority if prior is not None else MOQT_DEFAULT_PRIORITY
        )
        properties = decode_properties(buf) if flags & FETCH_FLAG_EXTENSIONS_PRESENT else None
        payload_length = buf.pull_vint()
        if payload_length:
            status, payload = ObjectStatus.NORMAL, buf.pull_bytes(payload_length)
        else:
            # aiomoqt represents status-bearing empty objects this way.  moqx
            # FETCHes in P5 contain non-empty deterministic payload Objects.
            status, payload = ObjectStatus(buf.pull_vint()), b""
        return cls(group_id=group_id, subgroup_id=subgroup_id, object_id=object_id,
                   publisher_priority=publisher_priority, extensions=properties,
                   status=status, payload=payload)

    FetchHeader.serialize = header_serialize
    FetchHeader.deserialize = classmethod(header_deserialize)
    FetchObject.serialize = object_serialize
    FetchObject.deserialize = classmethod(object_deserialize)
    _APPLIED = True

