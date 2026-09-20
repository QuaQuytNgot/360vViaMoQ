from aiomoqt.context import profile_for
from aiomoqt.messages.track import FetchHeader, FetchObject
from aiomoqt.types import MOQTDraft, ObjectStatus
from aiomoqt.utils.buffer import Buffer

from moq_360_protocol_lab.aiomoqt_d18_fetch import apply_aiomoqt_d18_fetch_patch


def test_d18_fetch_header_uses_vi64_for_large_request_id():
    apply_aiomoqt_d18_fetch_patch()
    header = FetchHeader(request_id=300)
    encoded = header.serialize(draft=18)
    buf = Buffer(data=encoded, vi64=True)
    assert buf.pull_vint() == 5
    assert FetchHeader.deserialize(buf, draft=18).request_id == 300


def test_d18_fetch_object_decodes_vi64_and_location_deltas():
    apply_aiomoqt_d18_fetch_patch()
    prof = profile_for(MOQTDraft.DRAFT_18)

    first_wire = Buffer(capacity=1024, vi64=True)
    first_wire.push_vint(0x1F)  # subgroup, object, group, priority present
    first_wire.push_vint(70)    # first Group ID is absolute
    first_wire.push_vint(3)
    first_wire.push_vint(80)    # first Object ID is absolute
    first_wire.push_uint8(128)
    first_wire.push_vint(300)
    first_wire.push_bytes(b"x" * 300)
    first = FetchObject.deserialize(Buffer(data=first_wire.data, vi64=True), prof=prof)
    assert (first.group_id, first.subgroup_id, first.object_id) == (70, 3, 80)
    assert first.payload == b"x" * 300

    next_wire = Buffer(capacity=1024, vi64=True)
    next_wire.push_vint(0x1F)
    next_wire.push_vint(1)      # next Group = 70 + 1 + 1 = 72
    next_wire.push_vint(4)
    next_wire.push_vint(7)      # new Group: Object ID is absolute
    next_wire.push_uint8(127)
    next_wire.push_vint(257)
    next_wire.push_bytes(b"y" * 257)
    second = FetchObject.deserialize(Buffer(data=next_wire.data, vi64=True), prior=first, prof=prof)
    assert (second.group_id, second.subgroup_id, second.object_id) == (72, 4, 7)
    assert second.payload == b"y" * 257
    assert second.status == ObjectStatus.NORMAL


def test_d18_fetch_object_same_group_object_delta():
    apply_aiomoqt_d18_fetch_patch()
    prof = profile_for(MOQTDraft.DRAFT_18)
    prior = FetchObject(group_id=8, subgroup_id=2, object_id=9,
                        publisher_priority=128, payload=b"prior")
    wire = Buffer(capacity=128, vi64=True)
    wire.push_vint(0x15)  # prior subgroup, object delta, priority; same group
    wire.push_vint(3)     # Object ID = 9 + 3
    wire.push_uint8(128)
    wire.push_vint(70)
    wire.push_bytes(b"z" * 70)
    obj = FetchObject.deserialize(Buffer(data=wire.data, vi64=True), prior=prior, prof=prof)
    assert (obj.group_id, obj.subgroup_id, obj.object_id) == (8, 2, 12)
    assert obj.payload == b"z" * 70
