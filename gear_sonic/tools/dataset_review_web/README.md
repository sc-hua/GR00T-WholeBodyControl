# Episode Review Studio

Local web UI for reviewing SONIC LeRobot v2.1 datasets. It synchronizes the
episode video, the repository's real G1 43-DoF URDF/STL model, joint state/command
curves, stream modes, and frame-accurate review timelines. A 12-frame filmstrip
provides the whole-episode overview. Below the review controls, all 43 joint
tracks can be shown or hidden individually, selected together, or cleared.

Run it from the repository root through the Python launcher (do not start this
frontend directly for normal use):

```bash
source .venv_data_collection/bin/activate
python gear_sonic/scripts/run_dataset_review.py \
  --dataset-path outputs/pico_bottle_to_bin_pool
```

The browser opens at `http://127.0.0.1:3000`. Decisions are written atomically
to `<dataset>/meta/review.jsonl`; parquet and MP4 source files are never changed.

## Long-running collection pool

Use a stable dataset name and disable the launcher's timestamp suffix to append
new recording sessions to one review pool:

```bash
python gear_sonic/scripts/launch_data_collection.py \
  --dataset-name pico_bottle_to_bin_pool \
  --no-append-dataset-timestamp
```

Add the same robot, scene, camera, and teleoperation options used to create the
pool. Those settings, the FPS, and the feature schema must remain compatible.

Start this reviewer against `outputs/pico_bottle_to_bin_pool`. While collection
continues, click **刷新数据** to load episodes that the exporter has finished
saving. The pool should remain append-only so existing review episode indices
stay stable. Create reviewed training snapshots with `process_dataset.py`; do
not destructively clean or renumber the live pool. A `discard` decision is only
a label in `meta/review.jsonl`; the corresponding parquet and video remain in
the pool.

Keyboard shortcuts:

| Key | Action |
|---|---|
| `Space` | Play/pause |
| `K` | Keep the complete episode |
| `D` | Discard the complete episode |
| `I` | Set trim in-point |
| `O` | Set trim out-point |
| `Left` / `Right` | Seek 1 second |
| `Shift+Left` / `Shift+Right` | Seek 5 seconds |

After every episode has been reviewed, create a new training dataset:

```bash
python gear_sonic/scripts/process_dataset.py \
  --dataset-path outputs/pico_bottle_to_bin_pool \
  --output-path outputs/pico_bottle_to_bin_reviewed_snapshot \
  --review-file outputs/pico_bottle_to_bin_pool/meta/review.jsonl \
  --no-remove-stale-smpl
```

Unreviewed episodes are excluded by default when a review manifest is supplied.
Pass `--include-unreviewed` only when that behavior is intentional. The output
copies the audit trail to `meta/source_review.jsonl`. Processing also recomputes
the LeRobot v2.1 `meta/episodes_stats.jsonl` after trimming and reindexing.

The 3D viewer loads `g1_29dof_with_hand.urdf` and its original STL meshes, then
drives every joint by name from the 43 recorded joint angles. The dataset does
not currently expose floating-base position/orientation in `observation.state`,
so the pelvis stays at a fixed display origin; this is still not a full MuJoCo
physics replay.

## Development

```bash
npm install
npm run dev
npm run build
npm test
```

The frontend is a client-only Vite React app. Dataset access, review writes,
URDF/STL serving, and video streaming remain in the local Python launcher.
