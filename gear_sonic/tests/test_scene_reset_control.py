import numpy as np

from gear_sonic.utils.mujoco_sim.scene_reset_subscriber import SceneResetSubscriber
from gear_sonic.utils.teleop.button_events import LongPressTrigger
from gear_sonic.utils.teleop.zmq.zmq_planner_sender import (
    pack_pose_message,
    unpack_pose_message,
)


def test_long_press_fires_once_until_button_is_released():
    trigger = LongPressTrigger(hold_seconds=1.0)

    assert not trigger.update(True, now=10.0)
    assert not trigger.update(True, now=10.99)
    assert trigger.update(True, now=11.0)
    assert not trigger.update(True, now=12.0)
    assert not trigger.update(False, now=12.1)
    assert not trigger.update(True, now=13.0)
    assert trigger.update(True, now=14.0)


def test_manager_state_scene_reset_request_round_trip():
    packed = pack_pose_message(
        {
            "stream_mode": np.array([2], dtype=np.int32),
            "scene_reset_request_id": np.array([3], dtype=np.int64),
        },
        topic="manager_state",
    )

    unpacked = unpack_pose_message(packed, topic="manager_state")

    assert int(unpacked["stream_mode"][0]) == 2
    assert int(unpacked["scene_reset_request_id"][0]) == 3


def test_scene_reset_subscriber_triggers_once_per_increasing_request_id():
    subscriber = SceneResetSubscriber.__new__(SceneResetSubscriber)
    subscriber._last_request_id = 0

    def message(request_id):
        return pack_pose_message(
            {"scene_reset_request_id": np.array([request_id], dtype=np.int64)},
            topic="manager_state",
        )

    assert subscriber._process_message(message(1))
    assert not subscriber._process_message(message(1))
    assert subscriber._process_message(message(2))
    # A restarted manager returns to zero without causing an unsolicited reset.
    assert not subscriber._process_message(message(0))
    assert subscriber._process_message(message(1))
