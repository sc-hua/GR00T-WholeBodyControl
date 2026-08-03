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
  --dataset-path outputs/pico_bottle_to_bin_merged_20260802
```

The browser opens at `http://127.0.0.1:3000`. Decisions are written atomically
to `<dataset>/meta/review.jsonl`; parquet and MP4 source files are never changed.

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
  --dataset-path outputs/pico_bottle_to_bin_merged_20260802 \
  --output-path outputs/pico_bottle_to_bin_reviewed \
  --review-file outputs/pico_bottle_to_bin_merged_20260802/meta/review.jsonl \
  --no-remove-stale-smpl
```

Unreviewed episodes are excluded by default when a review manifest is supplied.
Pass `--include-unreviewed` only when that behavior is intentional. The output
copies the audit trail to `meta/source_review.jsonl`.

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
