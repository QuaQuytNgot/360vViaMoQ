# Future browser player

This directory is intentionally a placeholder. The eventual interface is:

`MoQ or HTTP fMP4 source → demuxer → WebCodecs decoder → tile synchronizer → compositor → 360 renderer`

Preprocessing names tracks as `codec/tile_rR_cC/qN`, which can map directly to
future MoQ track names. No MoQ protocol or JavaScript player is implemented in
this phase.
