#!/usr/bin/env python3
"""Stream the SONIC ego camera through XRoboToolkit Remote Vision."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys


def _bootstrap_venv() -> None:
    try:
        import av  # noqa: F401
        import msgpack_numpy  # noqa: F401
        import zmq  # noqa: F401

        return
    except ImportError:
        pass

    repo_root = Path(__file__).resolve().parent.parent.parent
    venv_python = repo_root / ".venv_data_collection" / "bin" / "python"
    if not venv_python.exists():
        raise SystemExit(
            "PyAV/ZMQ dependencies are missing. Run: "
            "bash install_scripts/install_data_collection.sh"
        )
    print(f"Re-launching with {venv_python} ...")
    import os

    os.execv(str(venv_python), [str(venv_python), *sys.argv])


def main() -> None:
    _bootstrap_venv()

    from gear_sonic.utils.teleop.xrobotoolkit_video_sender import XRoboToolkitVideoServer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-host", default="0.0.0.0")
    parser.add_argument("--control-port", type=int, default=13579)
    parser.add_argument("--camera-host", default="localhost")
    parser.add_argument("--camera-port", type=int, default=5555)
    parser.add_argument("--image-key", default="ego_view")
    parser.add_argument(
        "--encoder",
        choices=("auto", "h264_nvenc", "libx264"),
        default="auto",
        help="auto tries NVIDIA NVENC first and falls back to libx264",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = XRoboToolkitVideoServer(
        control_host=args.control_host,
        control_port=args.control_port,
        camera_host=args.camera_host,
        camera_port=args.camera_port,
        image_key=args.image_key,
        encoder=args.encoder,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Stopping XRoboToolkit video sender")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
