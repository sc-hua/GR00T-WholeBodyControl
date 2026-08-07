import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

from gear_sonic.scripts.process_dataset import process_single_dataset, write_output_dataset
from gear_sonic.scripts.run_dataset_review import G1_MODEL_DIR, DatasetReviewStore


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _make_dataset(root: Path) -> Path:
    meta = root / "meta"
    data = root / "data" / "chunk-000"
    video = root / "videos" / "chunk-000" / "observation.images.ego_view"
    meta.mkdir(parents=True)
    data.mkdir(parents=True)
    video.mkdir(parents=True)
    info = {
        "fps": 50,
        "total_frames": 3,
        "chunks_size": 1000,
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "features": {
            "observation.images.ego_view": {"dtype": "video", "shape": [4, 4, 3]},
            "observation.state": {
                "dtype": "float32",
                "shape": [43],
                "names": [f"joint_{i}" for i in range(43)],
            },
            "task_index": {"dtype": "int64", "shape": [1]},
        },
    }
    (meta / "info.json").write_text(json.dumps(info), encoding="utf-8")
    _write_jsonl(meta / "episodes.jsonl", [{"episode_index": 0, "length": 3, "tasks": ["demo"]}])
    _write_jsonl(meta / "tasks.jsonl", [{"task_index": 0, "task": "demo"}])
    values = [np.full(43, i, dtype=np.float32) for i in range(3)]
    pd.DataFrame(
        {
            "observation.state": values,
            "action.wbc": values,
            "action.motion_token": [np.full(64, i, dtype=np.float32) for i in range(3)],
            "teleop.stream_mode": np.array([1, 2, 2], dtype=np.int32),
            "timestamp": np.array([0.0, 0.02, 0.04], dtype=np.float32),
            "task_index": np.zeros(3, dtype=np.int64),
        }
    ).to_parquet(data / "episode_000000.parquet")
    (video / "episode_000000.mp4").write_bytes(b"fake-video")
    return root


def test_review_store_loads_motion_and_persists_review(tmp_path):
    store = DatasetReviewStore(_make_dataset(tmp_path / "dataset"))

    summary = store.summary()
    assert summary["counts"]["unreviewed"] == 1
    assert summary["episodes"][0]["duration"] == 0.06

    motion = store.motion(0)
    assert motion["length"] == 3
    assert motion["joint_names"][0] == "joint_0"
    assert motion["state"][2][0] == 2.0
    assert motion["stream_mode"] == [1, 2, 2]

    review = store.save_review(
        0,
        {"status": "trim", "trim_start": 0.01, "trim_end": 0.05, "notes": "keep middle"},
    )
    assert review["status"] == "trim"
    assert store.summary()["counts"]["trim"] == 1

    persisted = json.loads((tmp_path / "dataset/meta/review.jsonl").read_text().strip())
    assert persisted["notes"] == "keep middle"


def test_review_store_rejects_invalid_trim(tmp_path):
    store = DatasetReviewStore(_make_dataset(tmp_path / "dataset"))

    try:
        store.save_review(0, {"status": "trim", "trim_start": 0.05, "trim_end": 0.01})
    except ValueError as exc:
        assert "trim_start" in str(exc)
    else:
        raise AssertionError("Expected invalid trim to fail")


def test_review_store_surfaces_collection_discard_as_discarded(tmp_path):
    dataset = _make_dataset(tmp_path / "dataset")
    info_path = dataset / "meta/info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["discarded_episode_indices"] = [0]
    info_path.write_text(json.dumps(info), encoding="utf-8")

    summary = DatasetReviewStore(dataset).summary()

    assert summary["counts"]["discard"] == 1
    assert summary["counts"]["unreviewed"] == 0
    assert summary["episodes"][0]["status"] == "discard"
    assert summary["episodes"][0]["collection_discarded"] is True
    assert summary["episodes"][0]["notes"] == "采集时标记丢弃"


def test_review_store_refreshes_appended_episodes_and_keeps_reviews(tmp_path):
    dataset = _make_dataset(tmp_path / "dataset")
    store = DatasetReviewStore(dataset)
    store.save_review(0, {"status": "keep", "notes": "checked"})

    episodes_path = dataset / "meta" / "episodes.jsonl"
    _write_jsonl(
        episodes_path,
        [
            {"episode_index": 0, "length": 3, "tasks": ["demo"]},
            {"episode_index": 4, "length": 10, "tasks": ["new"]},
        ],
    )
    info_path = dataset / "meta" / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["total_frames"] = 13
    info_path.write_text(json.dumps(info), encoding="utf-8")

    summary = store.refresh()

    assert summary["total_episodes"] == 2
    assert summary["total_frames"] == 13
    assert summary["counts"] == {"unreviewed": 1, "keep": 1, "discard": 0, "trim": 0}
    assert store.episode(4)["tasks"] == ["new"]
    assert store.episode(0)["episode_index"] == 0


def test_g1_urdf_and_mesh_assets_are_available(tmp_path):
    store = DatasetReviewStore(_make_dataset(tmp_path / "dataset"))
    urdf = store.robot_asset_path("g1_29dof_with_hand.urdf")
    mesh_paths = set(re.findall(r'filename="([^"]+)"', urdf.read_text(encoding="utf-8")))

    assert urdf == G1_MODEL_DIR / "g1_29dof_with_hand.urdf"
    assert len(mesh_paths) == 50
    assert all(store.robot_asset_path(path).is_file() for path in mesh_paths)

    try:
        store.robot_asset_path("../../meta/info.json")
    except ValueError as exc:
        assert "Invalid G1 model asset path" in str(exc)
    else:
        raise AssertionError("Expected G1 asset path traversal to fail")


def test_process_dataset_applies_manual_review_frames(tmp_path):
    dataset = _make_dataset(tmp_path / "dataset")
    reviews = {
        0: {
            "episode_index": 0,
            "status": "trim",
            "trim_start": 0.02,
            "trim_end": 0.06,
        }
    }

    stats, episodes, _ = process_single_dataset(
        dataset,
        remove_stale_smpl=False,
        reviews=reviews,
    )

    assert stats["episodes_trimmed"] == 1
    assert stats["review_frames_removed"] == 1
    assert len(episodes) == 1
    assert episodes[0]["valid_indices"].tolist() == [1, 2]
    np.testing.assert_allclose(episodes[0]["df"]["timestamp"].to_numpy(), [0.0, 0.02])


def test_write_output_dataset_recomputes_episode_stats(tmp_path):
    dataset = _make_dataset(tmp_path / "dataset")
    _, episodes, info = process_single_dataset(dataset, remove_stale_smpl=False)
    output = tmp_path / "output"

    write_output_dataset(output, episodes, info, [], info.get("script_config"))

    stats_rows = [
        json.loads(line)
        for line in (output / "meta/episodes_stats.jsonl").read_text().splitlines()
    ]
    state_stats = stats_rows[0]["stats"]["observation.state"]
    assert len(stats_rows) == 1
    assert state_stats["count"] == [3]
    assert state_stats["min"][0] == 0.0
    assert state_stats["max"][0] == 2.0


def test_write_output_dataset_remaps_source_task_indices(tmp_path):
    first = _make_dataset(tmp_path / "first")
    second = _make_dataset(tmp_path / "second")
    _write_jsonl(
        second / "meta/tasks.jsonl",
        [{"task_index": 0, "task": "second task"}],
    )
    _write_jsonl(
        second / "meta/episodes.jsonl",
        [{"episode_index": 0, "length": 3, "tasks": ["second task"]}],
    )
    _, first_episodes, info = process_single_dataset(first, remove_stale_smpl=False)
    _, second_episodes, _ = process_single_dataset(second, remove_stale_smpl=False)
    output = tmp_path / "output"
    tasks = [
        {"task_index": 0, "task": "demo"},
        {"task_index": 1, "task": "second task"},
    ]

    write_output_dataset(
        output,
        first_episodes + second_episodes,
        info,
        tasks,
        info.get("script_config"),
    )

    second_frame = pd.read_parquet(output / "data/chunk-000/episode_000001.parquet")
    assert second_frame["task_index"].unique().tolist() == [1]
    assert [json.loads(line) for line in (output / "meta/tasks.jsonl").read_text().splitlines()] == tasks
