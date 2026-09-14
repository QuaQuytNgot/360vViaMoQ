# 360 Codec Preprocessing Pipeline

Reusable Bash infrastructure for equirectangular-projection (ERP) 360 video:

`probe → regular-grid tiles → H.264/HEVC encode → fMP4 fragments → decode → ERP reconstruction → validation`

It deliberately contains no selected research parameters. Tile layout, quality
count/QPs or bitrates, GOP size, presets, rate control, B-frames, fragment
duration, input resolution, and latency settings are all **TO BE PROVIDED BY
USER**.

## Layout

- `input/`: source media (ignored by Git)
- `media/raw_tiles/`: lossless FFV1 CPU-cropped tiles (not experimental codec output)
- `media/encoded/{h264,hevc}/tile_rR_cC/qN/media.mp4`: future MoQ-track-ready
  encoded tracks and sidecar metadata
- `media/fragmented/`: `init.mp4`, `group_*.m4s`, and a VOD manifest per track
- `media/decoded/`: validation decodes grouped by codec and quality
- `media/reconstructed/`: generic NxM reconstructed ERPs
- `results/`: hardware reports, benchmark CSV, and future GPU logs

## Setup and configuration

Dependencies are Bash, FFmpeg/FFprobe; NVIDIA NVENC is the primary planned
encoder. `libx264` and `libx265` are optional CPU fallbacks. First run:

```bash
./scripts/inspect_hardware.sh
cp configs/pipeline.env.example configs/pipeline.env
```

The report is saved to `results/hardware_capabilities.txt`. Fill the copy only
when experimental decisions are available. Whitespace-separated `H264_QPS` or
`HEVC_QPS` lists dynamically create `q0…qN`; no source changes are needed for
any number of levels. Bitrate lists are supported as an alternate control.

Run the complete pipeline only after every required setting is set:

```bash
./scripts/prepare_all.sh --config configs/pipeline.env --input input/source.mp4
```

It stops clearly if settings such as `TILE_ROWS`, `TILE_COLS`, QP/bitrate lists,
GOP size, presets, rate control, B-frames, or fragment duration are absent.

## Script reference

- `inspect_hardware.sh`: records OS, CPU/RAM, NVIDIA driver/CUDA, FFmpeg build,
  acceleration methods, NVENC encoders, and hardware decoders.
- `probe_video.sh INPUT [--format text|shell|json] [--strict-erp]`: inspects
  source video and warns by output field when it is not approximately 2:1 ERP.
- `tile_erp.sh --input … --rows … --cols … --output …`: CPU fallback grid crop;
  rejects dimensions not divisible by the requested layout.
- `encode_h264.sh` / `encode_hevc.sh`: consistent NVENC (`nvenc`) and software
  (`cpu`) interfaces with explicit QP/bitrate, GOP, preset, RC, and B-frame args.
- `package_fragments.sh`: makes configurable-duration keyframe-bounded fMP4 HLS
  fragments (`init.mp4` + `group_*.m4s`) without committing to one-second groups.
- `decode_tiles.sh`: `auto`, GPU, or CPU validation decode.
- `reconstruct_erp.sh`: programmatically builds an FFmpeg stack graph for any
  regular NxM tile grid.
- `check_gop_alignment.sh`: compares keyframe timestamps across supplied tracks.
- `validate_pipeline.sh`: verifies configured outputs and prints `SKIPPED` where
  a parameter has not yet been supplied.
- `benchmark_codecs.sh --record …`: appends a completed encode's metadata and
  file metrics to `results/codec_benchmark.csv`; it never launches a large test.
- `prepare_all.sh`: explicit full orchestration.

All entry-point scripts accept `--help`, use `set -euo pipefail`, and use quoted
paths. No sample media is downloaded and no experiment is launched implicitly.

## Future MoQ mapping

The directory identifiers map naturally to future MoQ naming: codec + tile +
quality = one track, while each GOP-aligned fMP4 media segment is a candidate
group. MoQ itself is intentionally outside this implementation.
