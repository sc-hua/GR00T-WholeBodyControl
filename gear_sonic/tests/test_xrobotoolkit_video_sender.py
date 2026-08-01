import struct

import numpy as np

from gear_sonic.utils.teleop.xrobotoolkit_video_sender import (
    LengthPrefixedStream,
    frame_video_packet,
    make_stereo_frame,
    parse_camera_request,
    parse_control_message,
)


def _compact_string(value: str) -> bytes:
    encoded = value.encode()
    assert len(encoded) < 128
    return bytes((len(encoded),)) + encoded


def _open_camera_packet() -> tuple[bytes, bytes]:
    camera_data = (
        b"\xca\xfe\x01"
        + struct.pack("<7i", 2560, 720, 60, 4_000_000, 0, 2, 12345)
        + _compact_string("ZED")
        + _compact_string("10.10.4.36")
    )
    command = b"OPEN_CAMERA"
    body = struct.pack("<i", len(command)) + command + struct.pack("<i", len(camera_data)) + camera_data
    return struct.pack(">I", len(body)) + body, camera_data


def test_control_stream_handles_fragmented_and_coalesced_packets():
    packet, camera_data = _open_camera_packet()
    parser = LengthPrefixedStream()

    assert parser.feed(packet[:3]) == []
    messages = parser.feed(packet[3:] + packet)

    assert len(messages) == 2
    command, data = parse_control_message(messages[0])
    assert command == "OPEN_CAMERA"
    assert data == camera_data


def test_parse_camera_request():
    _, camera_data = _open_camera_packet()

    request = parse_camera_request(camera_data)

    assert request.width == 2560
    assert request.height == 720
    assert request.fps == 60
    assert request.bitrate == 4_000_000
    assert request.camera == "ZED"
    assert request.ip == "10.10.4.36"
    assert request.port == 12345


def test_video_packet_uses_big_endian_length():
    payload = b"\x00\x00\x00\x01\x67"
    assert frame_video_packet(payload) == b"\x00\x00\x00\x05" + payload


def test_stereo_frame_duplicates_letterboxed_source():
    source = np.full((4, 4, 3), 127, dtype=np.uint8)

    stereo = make_stereo_frame(source, width=16, height=4)

    assert stereo.shape == (4, 16, 3)
    np.testing.assert_array_equal(stereo[:, :8], stereo[:, 8:])
    assert np.all(stereo[:, :2] == 0)
    assert np.all(stereo[:, 2:6] == 127)
    assert np.all(stereo[:, 6:8] == 0)
