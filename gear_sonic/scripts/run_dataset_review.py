"""Launch a local web app for reviewing and trimming LeRobot v2.1 episodes.

The review manifest is intentionally stored separately from the dataset's
training metadata.  Marking an episode in the UI is therefore reversible and
does not mutate parquet files or videos.
"""

from __future__ import annotations

import argparse
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from typing import Any
from urllib.parse import unquote, urlparse
import webbrowser

import numpy as np
import pandas as pd

REVIEW_STATUSES = {"unreviewed", "keep", "discard", "trim"}
DEFAULT_DATASET = "outputs/pico_bottle_to_bin_merged_20260802"
G1_MODEL_DIR = (
    Path(__file__).resolve().parents[2]
    / "decoupled_wbc"
    / "control"
    / "robot_model"
    / "model_data"
    / "g1"
)


class DatasetReviewStore:
    def __init__(self, dataset_path: Path, review_file: Path | None = None):
        self.dataset_path = dataset_path.resolve()
        self.info = self._read_json(self.dataset_path / "meta" / "info.json")
        self.episodes = self._read_jsonl(self.dataset_path / "meta" / "episodes.jsonl")
        self.tasks = self._read_jsonl(self.dataset_path / "meta" / "tasks.jsonl")
        self.review_file = (
            review_file.resolve()
            if review_file is not None
            else self.dataset_path / "meta" / "review.jsonl"
        )
        self._lock = threading.Lock()
        self.reviews = self._load_reviews()

        if not self.episodes:
            raise ValueError(f"Dataset has no episodes: {self.dataset_path}")
        if "data_path" not in self.info or "video_path" not in self.info:
            raise ValueError("Dataset info.json is missing data_path or video_path")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]

    def _load_reviews(self) -> dict[int, dict[str, Any]]:
        reviews: dict[int, dict[str, Any]] = {}
        for item in self._read_jsonl(self.review_file):
            episode_index = int(item["episode_index"])
            reviews[episode_index] = item
        return reviews

    @property
    def fps(self) -> int:
        return int(self.info.get("fps", 50))

    def episode(self, episode_index: int) -> dict[str, Any]:
        if episode_index < 0 or episode_index >= len(self.episodes):
            raise IndexError(f"Unknown episode: {episode_index}")
        return self.episodes[episode_index]

    def parquet_path(self, episode_index: int) -> Path:
        chunks_size = int(self.info.get("chunks_size", 1000))
        return self.dataset_path / self.info["data_path"].format(
            episode_chunk=episode_index // chunks_size,
            episode_index=episode_index,
        )

    def video_path(self, episode_index: int) -> Path:
        video_keys = self.info.get("video_keys") or [
            key
            for key, value in self.info.get("features", {}).items()
            if value.get("dtype") == "video"
        ]
        if not video_keys:
            raise FileNotFoundError("Dataset has no video feature")
        preferred = "observation.images.ego_view"
        video_key = preferred if preferred in video_keys else video_keys[0]
        chunks_size = int(self.info.get("chunks_size", 1000))
        return self.dataset_path / self.info["video_path"].format(
            episode_chunk=episode_index // chunks_size,
            episode_index=episode_index,
            video_key=video_key,
        )

    def robot_asset_path(self, relative_path: str) -> Path:
        """Resolve a G1 URDF asset without allowing traversal outside its model directory."""
        model_dir = G1_MODEL_DIR.resolve()
        path = (model_dir / relative_path).resolve()
        if not path.is_relative_to(model_dir):
            raise ValueError("Invalid G1 model asset path")
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def summary(self) -> dict[str, Any]:
        items = []
        counts = {status: 0 for status in REVIEW_STATUSES}
        for episode in self.episodes:
            index = int(episode["episode_index"])
            review = self.reviews.get(index, {"status": "unreviewed"})
            status = review.get("status", "unreviewed")
            counts[status] = counts.get(status, 0) + 1
            items.append(
                {
                    "episode_index": index,
                    "length": int(episode["length"]),
                    "duration": round(int(episode["length"]) / self.fps, 3),
                    "tasks": episode.get("tasks", []),
                    "status": status,
                    "trim_start": review.get("trim_start"),
                    "trim_end": review.get("trim_end"),
                    "notes": review.get("notes", ""),
                    "updated_at": review.get("updated_at"),
                }
            )
        return {
            "dataset_name": self.dataset_path.name,
            "dataset_path": str(self.dataset_path),
            "review_file": str(self.review_file),
            "fps": self.fps,
            "total_episodes": len(items),
            "total_frames": int(self.info.get("total_frames", 0)),
            "counts": counts,
            "episodes": items,
        }

    @lru_cache(maxsize=4)
    def motion(self, episode_index: int) -> dict[str, Any]:
        episode = self.episode(episode_index)
        frame = pd.read_parquet(self.parquet_path(episode_index))
        required = ["observation.state", "action.wbc", "action.motion_token"]
        missing = [key for key in required if key not in frame.columns]
        if missing:
            raise ValueError(f"Episode is missing motion columns: {missing}")

        state = np.stack(frame["observation.state"].to_numpy()).astype(np.float32)
        command = np.stack(frame["action.wbc"].to_numpy()).astype(np.float32)
        token = np.stack(frame["action.motion_token"].to_numpy()).astype(np.float32)
        timestamps = (
            frame["timestamp"].to_numpy(dtype=np.float32)
            if "timestamp" in frame
            else np.arange(len(frame), dtype=np.float32) / self.fps
        )
        stream_mode = (
            frame["teleop.stream_mode"].to_numpy(dtype=np.int32)
            if "teleop.stream_mode" in frame
            else np.zeros(len(frame), dtype=np.int32)
        )
        token_norm = np.linalg.norm(token, axis=1)

        joint_names = self.info["features"]["observation.state"].get("names", [])
        return {
            "episode_index": episode_index,
            "length": min(int(episode["length"]), len(frame)),
            "fps": self.fps,
            "joint_names": joint_names,
            "timestamps": np.round(timestamps, 4).tolist(),
            "state": np.round(state, 5).tolist(),
            "command": np.round(command, 5).tolist(),
            "stream_mode": stream_mode.tolist(),
            "token_norm": np.round(token_norm, 5).tolist(),
        }

    def save_review(self, episode_index: int, payload: dict[str, Any]) -> dict[str, Any]:
        episode = self.episode(episode_index)
        duration = int(episode["length"]) / self.fps
        status = str(payload.get("status", "unreviewed"))
        if status not in REVIEW_STATUSES:
            raise ValueError(f"Invalid review status: {status}")

        def optional_time(key: str) -> float | None:
            value = payload.get(key)
            if value is None or value == "":
                return None
            parsed = round(float(value), 4)
            if parsed < 0 or parsed > duration:
                raise ValueError(f"{key} must be inside [0, {duration:.3f}]")
            return parsed

        trim_start = optional_time("trim_start")
        trim_end = optional_time("trim_end")
        if trim_start is not None and trim_end is not None and trim_start >= trim_end:
            raise ValueError("trim_start must be earlier than trim_end")
        if status == "trim" and (trim_start is None or trim_end is None):
            raise ValueError("A trimmed episode needs both trim_start and trim_end")

        review = {
            "episode_index": episode_index,
            "status": status,
            "trim_start": trim_start,
            "trim_end": trim_end,
            "notes": str(payload.get("notes", "")).strip(),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        with self._lock:
            self.reviews[episode_index] = review
            self.review_file.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self.review_file.name}.",
                suffix=".tmp",
                dir=self.review_file.parent,
                text=True,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    for index in sorted(self.reviews):
                        stream.write(json.dumps(self.reviews[index], ensure_ascii=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp_name, self.review_file)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        return review


class ReviewRequestHandler(BaseHTTPRequestHandler):
    store: DatasetReviewStore

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[review-api] {self.address_string()} {format_string % args}")

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        try:
            path = unquote(urlparse(self.path).path)
            if path == "/api/health":
                self._json({"ok": True})
                return
            if path == "/api/dataset":
                self._json(self.store.summary())
                return
            if path.startswith("/api/g1-model/"):
                relative_path = path.removeprefix("/api/g1-model/")
                self._serve_file(self.store.robot_asset_path(relative_path))
                return
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "episodes"]:
                episode_index = int(parts[2])
                if parts[3] == "motion":
                    self._json(self.store.motion(episode_index))
                    return
                if parts[3] == "video":
                    self._serve_file(self.store.video_path(episode_index))
                    return
            self._error(HTTPStatus.NOT_FOUND, "Route not found")
        except (BrokenPipeError, ConnectionResetError):
            # 客户端在响应发送过程中断开连接(视频 seek / 切换 episode 时常见),无需上报。
            return
        except (IndexError, FileNotFoundError) as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
        except (ValueError, KeyError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        try:
            path = unquote(urlparse(self.path).path)
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "episodes"] or parts[3] != "review":
                self._error(HTTPStatus.NOT_FOUND, "Route not found")
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("Invalid request body")
            payload = json.loads(self.rfile.read(length))
            review = self.store.save_review(int(parts[2]), payload)
            self._json(review)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端提前断开连接,忽略即可。
            return
        except (IndexError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _serve_file(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(path)
        file_size = path.stat().st_size
        start, end = 0, file_size - 1
        range_header = self.headers.get("Range")
        status = HTTPStatus.OK
        if range_header:
            units, _, requested = range_header.partition("=")
            if units != "bytes" or "," in requested:
                self._error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "Unsupported range")
                return
            first, _, last = requested.partition("-")
            if first:
                start = int(first)
                end = min(int(last), end) if last else end
            elif last:
                start = max(0, file_size - int(last))
            if start < 0 or end < start or start >= file_size:
                self._error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "Invalid range")
                return
            status = HTTPStatus.PARTIAL_CONTENT

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self._cors_headers()
        self.end_headers()
        with open(path, "rb") as stream:
            stream.seek(start)
            remaining = length
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    # 浏览器 <video> 在流式传输中途中止请求属正常行为,停止写入即可。
                    return
                remaining -= len(chunk)


def _wait_for_url(url: str, timeout: float = 30.0) -> bool:
    from urllib.request import urlopen

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.25)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-path", default=DEFAULT_DATASET)
    parser.add_argument("--review-file")
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=8765)
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument("--no-frontend", action="store_true")
    parser.add_argument("--no-open-browser", action="store_true")
    args = parser.parse_args()

    dataset_path = Path(args.dataset_path)
    review_file = Path(args.review_file) if args.review_file else None
    store = DatasetReviewStore(dataset_path, review_file)
    ReviewRequestHandler.store = store
    server = ThreadingHTTPServer((args.api_host, args.api_port), ReviewRequestHandler)
    api_thread = threading.Thread(target=server.serve_forever, name="dataset-review-api")
    api_thread.start()

    frontend: subprocess.Popen | None = None
    site_dir = Path(__file__).resolve().parents[1] / "tools" / "dataset_review_web"
    frontend_url = f"http://127.0.0.1:{args.frontend_port}"
    if not args.no_frontend:
        env = os.environ.copy()
        env["VITE_REVIEW_API_URL"] = f"http://127.0.0.1:{args.api_port}"
        frontend = subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", str(args.frontend_port)],
            cwd=site_dir,
            env=env,
        )

    print(f"Dataset:    {store.dataset_path}")
    print(f"Review log: {store.review_file}")
    print(f"Review API: http://{args.api_host}:{args.api_port}")
    if frontend is not None:
        print(f"Web UI:     {frontend_url}")
        if _wait_for_url(frontend_url) and not args.no_open_browser:
            webbrowser.open(frontend_url)

    try:
        while True:
            if frontend is not None and frontend.poll() is not None:
                raise RuntimeError(f"Review web UI exited with code {frontend.returncode}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping dataset reviewer...")
    finally:
        server.shutdown()
        server.server_close()
        api_thread.join(timeout=5)
        if frontend is not None:
            frontend.terminate()
            try:
                frontend.wait(timeout=5)
            except subprocess.TimeoutExpired:
                frontend.kill()


if __name__ == "__main__":
    main()
