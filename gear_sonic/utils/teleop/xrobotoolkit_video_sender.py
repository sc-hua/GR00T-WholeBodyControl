"""XRoboToolkit Remote Vision bridge for SONIC camera streams.

The PICO client connects to the control server on TCP port 13579 and sends an
``OPEN_CAMERA`` request.  This bridge then subscribes to the SONIC ZMQ camera
stream, encodes ``ego_view`` as low-latency H.264, and connects back to the
PICO decoder on TCP port 12345.

The framing implemented here mirrors XR-Robotics/XRoboToolkit-Orin-Video-Sender:

* control packets: big-endian uint32 length + NetworkDataProtocol body;
* video packets: big-endian uint32 length + one Annex-B H.264 access unit.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from fractions import Fraction
import logging
import socket
import struct
import threading
from typing import Any

LOGGER = logging.getLogger(__name__)
MAX_CONTROL_PACKET_SIZE = 1024 * 1024


@dataclass(frozen=True)
class CameraRequest:
    """Camera settings sent by the XRoboToolkit PICO client."""

    width: int
    height: int
    fps: int
    bitrate: int
    enable_mv_hevc: int
    render_mode: int
    port: int
    camera: str
    ip: str


class LengthPrefixedStream:
    """Incrementally decode big-endian uint32 length-prefixed packets."""

    def __init__(self, max_packet_size: int = MAX_CONTROL_PACKET_SIZE):
        self._buffer = bytearray()
        self._max_packet_size = max_packet_size

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        packets: list[bytes] = []
        while len(self._buffer) >= 4:
            packet_size = struct.unpack_from(">I", self._buffer)[0]
            if packet_size > self._max_packet_size:
                raise ValueError(f"packet is too large: {packet_size} bytes")
            if len(self._buffer) < 4 + packet_size:
                break
            packets.append(bytes(self._buffer[4 : 4 + packet_size]))
            del self._buffer[: 4 + packet_size]
        return packets


def _read_7bit_length(data: bytes, offset: int) -> tuple[int, int]:
    """Read the compact string length used by .NET BinaryWriter."""

    result = 0
    shift = 0
    for _ in range(5):
        if offset >= len(data):
            raise ValueError("truncated compact string length")
        value = data[offset]
        offset += 1
        result |= (value & 0x7F) << shift
        if not value & 0x80:
            return result, offset
        shift += 7
    raise ValueError("invalid compact string length")


def _read_compact_string(data: bytes, offset: int) -> tuple[str, int]:
    length, offset = _read_7bit_length(data, offset)
    end = offset + length
    if end > len(data):
        raise ValueError("truncated compact string")
    return data[offset:end].decode("utf-8"), end


def parse_control_message(body: bytes) -> tuple[str, bytes]:
    """Parse an XRoboToolkit NetworkDataProtocol body."""

    if len(body) < 8:
        raise ValueError("control message is too short")
    command_length = struct.unpack_from("<i", body)[0]
    if command_length < 0 or 4 + command_length + 4 > len(body):
        raise ValueError("invalid command length")
    offset = 4
    command = body[offset : offset + command_length].rstrip(b"\0").decode("utf-8")
    offset += command_length
    data_length = struct.unpack_from("<i", body, offset)[0]
    offset += 4
    if data_length < 0 or offset + data_length != len(body):
        raise ValueError("invalid control data length")
    return command, body[offset:]


def parse_camera_request(data: bytes) -> CameraRequest:
    """Parse the CAFE/version-1 payload carried by ``OPEN_CAMERA``."""

    if len(data) < 31 or data[:2] != b"\xca\xfe":
        raise ValueError("invalid camera request magic")
    if data[2] != 1:
        raise ValueError(f"unsupported camera request version: {data[2]}")
    fields = struct.unpack_from("<7i", data, 3)
    offset = 31
    camera, offset = _read_compact_string(data, offset)
    ip, offset = _read_compact_string(data, offset)
    if offset != len(data):
        raise ValueError("unexpected bytes after camera request")
    return CameraRequest(*fields, camera=camera, ip=ip)


def frame_video_packet(payload: bytes) -> bytes:
    """Add the framing expected by XRoboToolkit's Android MediaDecoder."""

    return struct.pack(">I", len(payload)) + payload


def make_stereo_frame(image: Any, width: int, height: int) -> Any:
    """Letterbox one BGR image into each half of a side-by-side frame."""

    import cv2
    import numpy as np

    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("camera image must have shape HxWx3")
    if width <= 0 or height <= 0 or width % 2:
        raise ValueError("stereo output width must be positive and even")

    eye_width = width // 2
    scale = min(eye_width / image.shape[1], height / image.shape[0])
    scaled_width = max(2, int(image.shape[1] * scale) // 2 * 2)
    scaled_height = max(2, int(image.shape[0] * scale) // 2 * 2)
    resized = cv2.resize(image, (scaled_width, scaled_height), interpolation=cv2.INTER_LINEAR)
    eye = np.zeros((height, eye_width, 3), dtype=np.uint8)
    x = (eye_width - scaled_width) // 2
    y = (height - scaled_height) // 2
    eye[y : y + scaled_height, x : x + scaled_width] = resized
    return np.concatenate((eye, eye), axis=1)


class SonicZmqFrameSource:
    """Read the latest named JPEG/ndarray image from a SONIC ZMQ publisher."""

    def __init__(self, host: str, port: int, image_key: str):
        import zmq

        self._image_key = image_key
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.SUB)
        self._socket.setsockopt_string(zmq.SUBSCRIBE, "")
        self._socket.setsockopt(zmq.CONFLATE, True)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.connect(f"tcp://{host}:{port}")
        LOGGER.info("Subscribed to SONIC camera at tcp://%s:%d (%s)", host, port, image_key)

    def receive(self, timeout_ms: int = 500) -> Any | None:
        import msgpack
        import msgpack_numpy

        if not self._socket.poll(timeout_ms):
            return None
        message = msgpack.unpackb(
            self._socket.recv(), object_hook=msgpack_numpy.decode, raw=False
        )
        images = message.get("images", {})
        encoded = images.get(self._image_key, message.get(self._image_key))
        return self._decode_image(encoded)

    @staticmethod
    def _decode_image(encoded: Any) -> Any | None:
        import cv2
        import numpy as np

        if isinstance(encoded, np.ndarray):
            return encoded
        if isinstance(encoded, str):
            encoded = base64.b64decode(encoded)
        if isinstance(encoded, (bytes, bytearray)):
            return cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        return None

    def close(self) -> None:
        self._socket.close()
        self._context.term()


class H264Encoder:
    """Low-latency PyAV encoder with NVENC-to-libx264 fallback."""

    def __init__(self, width: int, height: int, fps: int, bitrate: int, encoder: str):
        import av

        candidates = [encoder] if encoder != "auto" else ["h264_nvenc", "libx264"]
        errors = []
        self._codec = None
        for candidate in candidates:
            try:
                codec = av.CodecContext.create(candidate, "w")
                codec.width = width
                codec.height = height
                codec.pix_fmt = "yuv420p"
                codec.time_base = Fraction(1, fps)
                codec.framerate = Fraction(fps, 1)
                codec.bit_rate = bitrate
                codec.gop_size = min(15, fps)
                codec.max_b_frames = 0
                if candidate == "h264_nvenc":
                    codec.options = {
                        "preset": "p1",
                        "tune": "ull",
                        "rc": "cbr",
                        "zerolatency": "1",
                        "delay": "0",
                        "forced-idr": "1",
                        "repeat-headers": "1",
                    }
                else:
                    codec.options = {
                        "preset": "ultrafast",
                        "tune": "zerolatency",
                        "profile": "baseline",
                        "x264-params": (
                            f"keyint={min(15, fps)}:min-keyint={min(15, fps)}:"
                            "scenecut=0:repeat-headers=1"
                        ),
                    }
                codec.open()
                self._codec = codec
                self.name = candidate
                break
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
        if self._codec is None:
            raise RuntimeError("no H.264 encoder could be opened (" + "; ".join(errors) + ")")
        self._time_base = Fraction(1, fps)
        self._pts = 0
        LOGGER.info("Using H.264 encoder: %s", self.name)

    def encode(self, bgr_image: Any) -> list[bytes]:
        import av

        frame = av.VideoFrame.from_ndarray(bgr_image, format="bgr24")
        frame.pts = self._pts
        frame.time_base = self._time_base
        self._pts += 1
        return [bytes(packet) for packet in self._codec.encode(frame)]


class XRoboToolkitVideoServer:
    """Serve XRoboToolkit commands and stream a SONIC camera to PICO."""

    def __init__(
        self,
        control_host: str = "0.0.0.0",
        control_port: int = 13579,
        camera_host: str = "localhost",
        camera_port: int = 5555,
        image_key: str = "ego_view",
        encoder: str = "auto",
    ):
        self.control_host = control_host
        self.control_port = control_port
        self.camera_host = camera_host
        self.camera_port = camera_port
        self.image_key = image_key
        self.encoder = encoder
        self._shutdown = threading.Event()
        self._stream_stop: threading.Event | None = None
        self._stream_thread: threading.Thread | None = None

    def serve_forever(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.control_host, self.control_port))
            listener.listen(1)
            listener.settimeout(1.0)
            LOGGER.info("XRoboToolkit control server listening on %s:%d", *listener.getsockname())
            while not self._shutdown.is_set():
                try:
                    connection, peer = listener.accept()
                except socket.timeout:
                    continue
                LOGGER.info("PICO control connection from %s:%d", *peer)
                try:
                    self._handle_control_connection(connection)
                except (ConnectionError, OSError, ValueError, UnicodeError) as exc:
                    LOGGER.warning("PICO control connection ended: %s", exc)
                finally:
                    connection.close()
                    self._stop_stream()

    def stop(self) -> None:
        self._shutdown.set()
        self._stop_stream()

    def _handle_control_connection(self, connection: socket.socket) -> None:
        parser = LengthPrefixedStream()
        while not self._shutdown.is_set():
            chunk = connection.recv(64 * 1024)
            if not chunk:
                return
            for body in parser.feed(chunk):
                command, data = parse_control_message(body)
                LOGGER.info("Received XRoboToolkit command: %s", command)
                if command == "OPEN_CAMERA":
                    request = parse_camera_request(data)
                    self._start_stream(request)
                elif command == "CLOSE_CAMERA":
                    self._stop_stream()
                else:
                    LOGGER.warning("Ignoring unknown command: %s", command)

    def _start_stream(self, request: CameraRequest) -> None:
        if request.camera != "ZED":
            LOGGER.warning(
                "Ignoring camera type %r; select ZEDMINI in the PICO app", request.camera
            )
            return
        if request.enable_mv_hevc:
            LOGGER.warning("HEVC was requested, but this bridge currently sends H.264")
        if request.width <= 0 or request.height <= 0 or request.width % 2:
            raise ValueError(f"invalid requested resolution: {request.width}x{request.height}")
        if (
            not 1 <= request.fps <= 240
            or request.bitrate <= 0
            or not 1 <= request.port <= 65535
        ):
            raise ValueError("invalid FPS, bitrate, or PICO video port")
        self._stop_stream()
        stream_stop = threading.Event()
        self._stream_stop = stream_stop
        self._stream_thread = threading.Thread(
            target=self._stream,
            args=(request, stream_stop),
            name="xrobotoolkit-video",
            daemon=True,
        )
        self._stream_thread.start()

    def _stop_stream(self) -> None:
        stream_stop = self._stream_stop
        if stream_stop is not None:
            stream_stop.set()
        thread = self._stream_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._stream_thread = None
        self._stream_stop = None

    def _stream(self, request: CameraRequest, stream_stop: threading.Event) -> None:
        source = SonicZmqFrameSource(self.camera_host, self.camera_port, self.image_key)
        video_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        video_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        video_socket.settimeout(10.0)
        try:
            LOGGER.info(
                "Opening PICO video stream %s:%d at %dx%d@%d, bitrate=%d",
                request.ip,
                request.port,
                request.width,
                request.height,
                request.fps,
                request.bitrate,
            )
            video_socket.connect((request.ip, request.port))
            video_socket.settimeout(None)
            encoder = H264Encoder(
                request.width,
                request.height,
                request.fps,
                request.bitrate,
                self.encoder,
            )
            LOGGER.info("Video connected; waiting for %s frames", self.image_key)
            frame_count = 0
            while not stream_stop.is_set() and not self._shutdown.is_set():
                image = source.receive()
                if image is None:
                    continue
                stereo = make_stereo_frame(image, request.width, request.height)
                for payload in encoder.encode(stereo):
                    video_socket.sendall(frame_video_packet(payload))
                frame_count += 1
                if frame_count == 1:
                    LOGGER.info("First H.264 frame sent to PICO")
        except Exception:
            LOGGER.exception("XRoboToolkit video stream failed")
        finally:
            source.close()
            video_socket.close()
            LOGGER.info("XRoboToolkit video stream stopped")
