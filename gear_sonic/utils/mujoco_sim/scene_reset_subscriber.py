"""Receive reliable scene-randomization requests from the PICO manager."""

import zmq

from gear_sonic.utils.teleop.zmq.zmq_planner_sender import unpack_pose_message


class SceneResetSubscriber:
    """Watch the manager-state topic for increasing scene-reset request IDs."""

    TOPIC = "manager_state"

    def __init__(self, host: str = "localhost", port: int = 5556):
        self._last_request_id = 0
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.SUB)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.CONFLATE, 1)
        self._socket.setsockopt_string(zmq.SUBSCRIBE, self.TOPIC)
        self._socket.connect(f"tcp://{host}:{port}")

    def poll(self) -> bool:
        """Return True when a new reset request has arrived."""
        if not self._socket.poll(timeout=0):
            return False

        try:
            return self._process_message(self._socket.recv(zmq.NOBLOCK))
        except zmq.ZMQError:
            return False

    def _process_message(self, raw: bytes) -> bool:
        try:
            data = unpack_pose_message(raw, topic=self.TOPIC)
            request = data.get("scene_reset_request_id")
            if request is None or request.size == 0:
                return False
            request_id = int(request.flat[0])
        except (ValueError, KeyError):
            return False

        triggered = request_id > self._last_request_id
        self._last_request_id = request_id
        return triggered

    def close(self):
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._context is not None:
            self._context.term()
            self._context = None
