import struct

import numpy as np

from gear_sonic.utils.teleop.xrobotoolkit_video_sender import (
    LengthPrefixedStream,
    draw_recording_status,
    frame_video_packet,
    make_multiview_stereo_frame,
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


def test_dashboard_duplicates_main_ego_and_wrist_tiles_for_both_eyes():
    images = {
        "third_person_view": np.full((72, 96, 3), (10, 20, 200), dtype=np.uint8),
        "ego_view": np.full((24, 32, 3), (20, 200, 10), dtype=np.uint8),
        "left_wrist": np.full((24, 32, 3), (200, 10, 20), dtype=np.uint8),
    }

    stereo = make_multiview_stereo_frame(
        images, width=512, height=144, layout="dashboard"
    )

    assert stereo.shape == (144, 512, 3)
    np.testing.assert_array_equal(stereo[:, :256], stereo[:, 256:])
    # Main camera occupies the large left region; right wrist is a gray placeholder.
    assert np.array_equal(stereo[100, 100], np.array((10, 20, 200)))
    placeholder_pixel = stereo[125, 220]
    assert placeholder_pixel[0] == placeholder_pixel[1] == placeholder_pixel[2]
    assert placeholder_pixel[0] > 0


def test_dual_view_places_ego_inset_over_main_view():
    images = {
        "third_person_view": np.full((72, 128, 3), 25, dtype=np.uint8),
        "ego_view": np.full((48, 64, 3), 180, dtype=np.uint8),
    }

    stereo = make_multiview_stereo_frame(
        images, width=512, height=144, layout="dual_view"
    )

    np.testing.assert_array_equal(stereo[:, :256], stereo[:, 256:])
    assert np.all(stereo[120, 100] == 25)
    assert np.all(stereo[45, 220] == 180)


def test_multiview_waits_until_main_camera_is_available():
    assert (
        make_multiview_stereo_frame(
            {"ego_view": np.zeros((4, 4, 3), dtype=np.uint8)},
            width=16,
            height=4,
            layout="dashboard",
        )
        is None
    )


def test_recording_status_overlay_is_visible_and_identical_in_both_eyes():
    stereo = np.zeros((144, 512, 3), dtype=np.uint8)

    result = draw_recording_status(
        stereo,
        {"state": "recording", "elapsed_seconds": 65.0, "episode_index": 3},
    )

    np.testing.assert_array_equal(result[:, :256], result[:, 256:])
    assert np.any(result[:, :256] != 0)
