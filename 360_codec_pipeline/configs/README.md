# Configuration

Copy `pipeline.env.example` to a private `pipeline.env` and fill only the
experimental values you have selected. The pipeline has no experimental
defaults. Lists such as `H264_QPS` are whitespace-separated and dynamically
create `q0`, `q1`, and so on. `NUM_QUALITIES`, if set, is checked against each
configured list.
