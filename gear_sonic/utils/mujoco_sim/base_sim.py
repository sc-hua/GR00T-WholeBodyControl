"""MuJoCo simulation environment and loop for the G1 (and H1) humanoid robots.

DefaultEnv owns the MuJoCo model/data, computes PD torques from Unitree SDK
commands, steps physics, and publishes observations back via the SDK bridge.
BaseSimulator wraps DefaultEnv with rate-limiting and viewer/image update loops.
"""

import os
import pathlib
from pathlib import Path
import pickle
import tempfile
from threading import Lock, Thread
import time
from typing import Dict
import xml.etree.ElementTree as ET

import mujoco
import mujoco.viewer
import numpy as np
from scipy.spatial.transform import Rotation
from unitree_sdk2py.core.channel import ChannelFactoryInitialize

from gear_sonic.utils.mujoco_sim.metric_utils import check_contact
from gear_sonic.utils.mujoco_sim.robot import Robot
from gear_sonic.utils.mujoco_sim.sim_utils import get_subtree_body_names
from gear_sonic.utils.mujoco_sim.unitree_sdk2py_bridge import ElasticBand, UnitreeSdk2Bridge

GEAR_SONIC_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class DefaultEnv:
    """Base environment class that handles simulation environment setup and step"""

    def __init__(
        self,
        config: Dict[str, any],
        env_name: str = "default",
        camera_configs: Dict[str, any] = {},
        onscreen: bool = False,
        offscreen: bool = False,
        enable_image_publish: bool = False,
    ):
        self.config = config
        self.env_name = env_name
        self.robot = Robot(self.config)
        self.num_body_dof = self.robot.NUM_JOINTS
        self.num_hand_dof = self.robot.NUM_HAND_JOINTS
        self.sim_dt = self.config["SIMULATE_DT"]
        self.obs = None
        self.torques = np.zeros(self.num_body_dof + self.num_hand_dof * 2)
        self.torque_limit = np.array(self.robot.MOTOR_EFFORT_LIMIT_LIST)
        self.camera_configs = camera_configs

        if not camera_configs and offscreen and enable_image_publish:
            self.camera_configs = {
                "ego_view": {"height": 480, "width": 640, "mjcf_name": "head_camera"},
            }

        self.reward_lock = Lock()
        self.unitree_bridge = None
        self.onscreen = onscreen
        self._reset_rng = np.random.default_rng(self.config.get("RESET_RANDOM_SEED"))
        self._viewer_reset_requested_at = None

        self.init_scene()
        self.last_reward = 0

        self._third_person_camera_id = mujoco.mj_name2id(
            self.mj_model,
            mujoco.mjtObj.mjOBJ_CAMERA,
            "third_person_camera",
        )
        self._third_person_camera_offset = None
        if self._third_person_camera_id != -1:
            # The scene camera uses targetbody mode, which keeps it pointed at
            # the pelvis but otherwise leaves its position fixed in the world.
            # Preserve its initial world-space offset so the published third-
            # person view follows the robot without inheriting pelvis rotation.
            self._third_person_camera_offset = (
                self.mj_model.cam_pos[self._third_person_camera_id].copy()
                - self.mj_data.xpos[self.root_body_id].copy()
            )

        if (
            not camera_configs
            and offscreen
            and enable_image_publish
            and self._third_person_camera_id != -1
        ):
            self.camera_configs["third_person_view"] = {
                "height": 480,
                "width": 640,
                "mjcf_name": "third_person_camera",
            }

        self.offscreen = offscreen
        if self.offscreen:
            self.init_renderers()
        self.image_dt = self.config.get("IMAGE_DT", 0.033333)
        self.image_publish_process = None

    def start_image_publish_subprocess(self, start_method: str = "spawn", camera_port: int = 5555):
        from gear_sonic.utils.mujoco_sim.image_publish_utils import ImagePublishProcess

        if len(self.camera_configs) == 0:
            print(
                "Warning: No camera configs provided, image publishing subprocess will not be started"
            )
            return
        start_method = self.config.get("MP_START_METHOD", "spawn")
        self.image_publish_process = ImagePublishProcess(
            camera_configs=self.camera_configs,
            image_dt=self.image_dt,
            zmq_port=camera_port,
            start_method=start_method,
            verbose=self.config.get("verbose", False),
        )
        self.image_publish_process.start_process()

    def _get_dof_indices_by_class(self):
        with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".xml") as f:
            mujoco.mj_saveLastXML(f.name, self.mj_model)
            temp_xml_path = f.name

        try:
            tree = ET.parse(temp_xml_path)
            root = tree.getroot()

            joint_class_map = {}
            for joint_element in root.findall(".//joint[@class]"):
                joint_name = joint_element.get("name")
                joint_class = joint_element.get("class")
                if joint_name and joint_class:
                    joint_id = mujoco.mj_name2id(
                        self.mj_model, mujoco.mjtObj.mjOBJ_JOINT, joint_name
                    )
                    if joint_id != -1:
                        dof_adr = self.mj_model.jnt_dofadr[joint_id]
                        if joint_class not in joint_class_map:
                            joint_class_map[joint_class] = []
                        joint_class_map[joint_class].append(dof_adr)
        finally:
            os.remove(temp_xml_path)

        return joint_class_map

    def _get_default_dof_properties(self):
        with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".xml") as f:
            mujoco.mj_saveLastXML(f.name, self.mj_model)
            temp_xml_path = f.name

        try:
            tree = ET.parse(temp_xml_path)
            root = tree.getroot()

            default_dof_properties = {}
            for default_element in root.findall(".//default/default[@class]"):
                class_name = default_element.get("class")
                joint_element = default_element.find("joint")
                if class_name and joint_element is not None:
                    properties = {}
                    if "damping" in joint_element.attrib:
                        properties["damping"] = float(joint_element.get("damping"))
                    if "armature" in joint_element.attrib:
                        properties["armature"] = float(joint_element.get("armature"))
                    if "frictionloss" in joint_element.attrib:
                        properties["frictionloss"] = float(joint_element.get("frictionloss"))

                    if properties:
                        default_dof_properties[class_name] = properties
        finally:
            os.remove(temp_xml_path)

        return default_dof_properties

    def init_scene(self):
        """Initialize the default robot scene"""
        xml_path = str(pathlib.Path(GEAR_SONIC_ROOT) / self.config["ROBOT_SCENE"])
        self.mj_model = mujoco.MjModel.from_xml_path(xml_path)
        self.mj_data = mujoco.MjData(self.mj_model)
        self.mj_model.opt.timestep = self.sim_dt
        self.torso_index = mujoco.mj_name2id(self.mj_model, mujoco.mjtObj.mjOBJ_BODY, "torso_link")
        self.root_body = "pelvis"
        self.root_body_id = self.mj_model.body(self.root_body).id

        self.joint_class_map = self._get_dof_indices_by_class()

        self.perform_sysid_search = self.config.get("perform_sysid_search", False)

        # Check for static root link (fixed base)
        self.use_floating_root_link = "floating_base_joint" in [
            self.mj_model.joint(i).name for i in range(self.mj_model.njnt)
        ]
        self.use_constrained_root_link = "constrained_base_joint" in [
            self.mj_model.joint(i).name for i in range(self.mj_model.njnt)
        ]

        # MuJoCo qpos/qvel arrays start with root DOFs before joint DOFs:
        # floating base has 7 qpos (pos + quat) and 6 qvel (lin + ang velocity)
        if self.use_floating_root_link:
            self.qpos_offset = 7
            self.qvel_offset = 6
        else:
            if self.use_constrained_root_link:
                self.qpos_offset = 1
                self.qvel_offset = 1
            else:
                raise ValueError(
                    "No root link found --"
                    "The absolute static root will make the simulation unstable."
                )

        # Enable the elastic band
        self.elastic_band = None
        if self.config["ENABLE_ELASTIC_BAND"] and self.use_floating_root_link:
            self.elastic_band = ElasticBand()
            if "g1" in self.config["ROBOT_TYPE"]:
                if self.config["enable_waist"]:
                    self.band_attached_link = self.mj_model.body("pelvis").id
                else:
                    self.band_attached_link = self.mj_model.body("torso_link").id
            elif "h1" in self.config["ROBOT_TYPE"]:
                self.band_attached_link = self.mj_model.body("torso_link").id
            else:
                self.band_attached_link = self.mj_model.body("base_link").id

            if self.onscreen:
                self.viewer = mujoco.viewer.launch_passive(
                    self.mj_model,
                    self.mj_data,
                    key_callback=self._mujoco_key_callback,
                    show_left_ui=False,
                    show_right_ui=False,
                )
            else:
                mujoco.mj_forward(self.mj_model, self.mj_data)
                self.viewer = None
        else:
            if self.onscreen:
                self.viewer = mujoco.viewer.launch_passive(
                    self.mj_model,
                    self.mj_data,
                    key_callback=self._mujoco_key_callback,
                    show_left_ui=False,
                    show_right_ui=False,
                )
            else:
                mujoco.mj_forward(self.mj_model, self.mj_data)
                self.viewer = None

        if self.viewer:
            self.viewer.cam.azimuth = 120
            self.viewer.cam.elevation = -30
            self.viewer.cam.distance = 2.0
            self.viewer.cam.lookat = np.array([0, 0, 0.5])
            self.viewer.cam.type = mujoco.mjtCamera.mjCAMERA_TRACKING
            self.viewer.cam.trackbodyid = self.mj_model.body("pelvis").id

        self.body_joint_index = []
        self.left_hand_index = []
        self.right_hand_index = []
        for i in range(self.mj_model.njnt):
            name = self.mj_model.joint(i).name
            if any(
                [
                    part_name in name
                    for part_name in ["hip", "knee", "ankle", "waist", "shoulder", "elbow", "wrist"]
                ]
            ):
                self.body_joint_index.append(i)
            elif "left_hand" in name:
                self.left_hand_index.append(i)
            elif "right_hand" in name:
                self.right_hand_index.append(i)

        assert len(self.body_joint_index) == self.robot.NUM_JOINTS
        assert len(self.left_hand_index) == self.robot.NUM_HAND_JOINTS
        assert len(self.right_hand_index) == self.robot.NUM_HAND_JOINTS

        self.body_joint_index = np.array(self.body_joint_index)
        self.left_hand_index = np.array(self.left_hand_index)
        self.right_hand_index = np.array(self.right_hand_index)

    def init_renderers(self):
        self.renderers = {}
        for camera_name, camera_config in self.camera_configs.items():
            renderer = mujoco.Renderer(
                self.mj_model, height=camera_config["height"], width=camera_config["width"]
            )
            self.renderers[camera_name] = renderer

    def compute_body_torques(self) -> np.ndarray:
        # PD control: tau = tau_ff + kp * (q_des - q) + kd * (dq_des - dq)
        body_torques = np.zeros(self.num_body_dof)
        if self.unitree_bridge is not None and self.unitree_bridge.low_cmd:
            for i in range(self.unitree_bridge.num_body_motor):
                if self.unitree_bridge.use_sensor:
                    body_torques[i] = (
                        self.unitree_bridge.low_cmd.motor_cmd[i].tau
                        + self.unitree_bridge.low_cmd.motor_cmd[i].kp
                        * (self.unitree_bridge.low_cmd.motor_cmd[i].q - self.mj_data.sensordata[i])
                        + self.unitree_bridge.low_cmd.motor_cmd[i].kd
                        * (
                            self.unitree_bridge.low_cmd.motor_cmd[i].dq
                            - self.mj_data.sensordata[i + self.unitree_bridge.num_body_motor]
                        )
                    )
                else:
                    body_torques[i] = (
                        self.unitree_bridge.low_cmd.motor_cmd[i].tau
                        + self.unitree_bridge.low_cmd.motor_cmd[i].kp
                        * (
                            self.unitree_bridge.low_cmd.motor_cmd[i].q
                            - self.mj_data.qpos[self.body_joint_index[i] + self.qpos_offset - 1]
                        )
                        + self.unitree_bridge.low_cmd.motor_cmd[i].kd
                        * (
                            self.unitree_bridge.low_cmd.motor_cmd[i].dq
                            - self.mj_data.qvel[self.body_joint_index[i] + self.qvel_offset - 1]
                        )
                    )
        return body_torques

    def get_head_pose(self) -> np.ndarray:
        root_pos = self.mj_data.body("torso_link").xpos.copy()
        # Reorder quaternion from MuJoCo [w,x,y,z] to scipy [x,y,z,w]
        root_quat = self.mj_data.body("torso_link").xquat.copy()[[1, 2, 3, 0]]
        head_pos = root_pos + Rotation.from_quat(root_quat).apply(np.array([0.0, 0.0, -0.044]))
        return np.concatenate((head_pos, root_quat))

    def get_root_vel(self) -> np.ndarray:
        return self.mj_data.qvel[:6]

    def compute_hand_torques(self) -> np.ndarray:
        left_hand_torques = np.zeros(self.num_hand_dof)
        right_hand_torques = np.zeros(self.num_hand_dof)
        if self.unitree_bridge is not None and self.unitree_bridge.low_cmd:
            for i in range(self.unitree_bridge.num_hand_motor):
                left_hand_torques[i] = (
                    self.unitree_bridge.left_hand_cmd.motor_cmd[i].tau
                    + self.unitree_bridge.left_hand_cmd.motor_cmd[i].kp
                    * (
                        self.unitree_bridge.left_hand_cmd.motor_cmd[i].q
                        - self.mj_data.qpos[self.left_hand_index[i] + self.qpos_offset - 1]
                    )
                    + self.unitree_bridge.left_hand_cmd.motor_cmd[i].kd
                    * (
                        self.unitree_bridge.left_hand_cmd.motor_cmd[i].dq
                        - self.mj_data.qvel[self.left_hand_index[i] + self.qvel_offset - 1]
                    )
                )
                right_hand_torques[i] = (
                    self.unitree_bridge.right_hand_cmd.motor_cmd[i].tau
                    + self.unitree_bridge.right_hand_cmd.motor_cmd[i].kp
                    * (
                        self.unitree_bridge.right_hand_cmd.motor_cmd[i].q
                        - self.mj_data.qpos[self.right_hand_index[i] + self.qpos_offset - 1]
                    )
                    + self.unitree_bridge.right_hand_cmd.motor_cmd[i].kd
                    * (
                        self.unitree_bridge.right_hand_cmd.motor_cmd[i].dq
                        - self.mj_data.qvel[self.right_hand_index[i] + self.qvel_offset - 1]
                    )
                )
        return np.concatenate((left_hand_torques, right_hand_torques))

    def compute_body_qpos(self) -> np.ndarray:
        body_qpos = np.zeros(self.num_body_dof)
        if self.unitree_bridge is not None and self.unitree_bridge.low_cmd:
            for i in range(self.unitree_bridge.num_body_motor):
                body_qpos[i] = self.unitree_bridge.low_cmd.motor_cmd[i].q
        return body_qpos

    def compute_hand_qpos(self) -> np.ndarray:
        hand_qpos = np.zeros(self.num_hand_dof * 2)
        if self.unitree_bridge is not None and self.unitree_bridge.low_cmd:
            for i in range(self.unitree_bridge.num_hand_motor):
                hand_qpos[i] = self.unitree_bridge.left_hand_cmd.motor_cmd[i].q
                hand_qpos[i + self.num_hand_dof] = self.unitree_bridge.right_hand_cmd.motor_cmd[i].q
        return hand_qpos

    def prepare_obs(self) -> Dict[str, any]:
        obs = {}
        if self.use_floating_root_link:
            obs["floating_base_pose"] = self.mj_data.qpos[:7]
            obs["floating_base_vel"] = self.mj_data.qvel[:6]
            obs["floating_base_acc"] = self.mj_data.qacc[:6]
        else:
            obs["floating_base_pose"] = np.zeros(7)
            obs["floating_base_vel"] = np.zeros(6)
            obs["floating_base_acc"] = np.zeros(6)

        obs["secondary_imu_quat"] = self.mj_data.xquat[self.torso_index]

        pose = np.zeros(13)
        torso_link = self.mj_model.body("torso_link").id
        # mj_objectVelocity returns [ang_vel, lin_vel]; swap to [lin_vel, ang_vel]
        mujoco.mj_objectVelocity(
            self.mj_model, self.mj_data, mujoco.mjtObj.mjOBJ_BODY, torso_link, pose[7:13], 1
        )
        pose[7:10], pose[10:13] = (
            pose[10:13],
            pose[7:10].copy(),
        )
        obs["secondary_imu_vel"] = pose[7:13]

        obs["body_q"] = self.mj_data.qpos[self.body_joint_index + 7 - 1]
        obs["body_dq"] = self.mj_data.qvel[self.body_joint_index + 6 - 1]
        obs["body_ddq"] = self.mj_data.qacc[self.body_joint_index + 6 - 1]
        obs["body_tau_est"] = self.mj_data.actuator_force[self.body_joint_index - 1]
        if self.num_hand_dof > 0:
            obs["left_hand_q"] = self.mj_data.qpos[self.left_hand_index + self.qpos_offset - 1]
            obs["left_hand_dq"] = self.mj_data.qvel[self.left_hand_index + self.qvel_offset - 1]
            obs["left_hand_ddq"] = self.mj_data.qacc[self.left_hand_index + self.qvel_offset - 1]
            obs["left_hand_tau_est"] = self.mj_data.actuator_force[self.left_hand_index - 1]
            obs["right_hand_q"] = self.mj_data.qpos[self.right_hand_index + self.qpos_offset - 1]
            obs["right_hand_dq"] = self.mj_data.qvel[self.right_hand_index + self.qvel_offset - 1]
            obs["right_hand_ddq"] = self.mj_data.qacc[self.right_hand_index + self.qvel_offset - 1]
            obs["right_hand_tau_est"] = self.mj_data.actuator_force[self.right_hand_index - 1]
        obs["time"] = self.mj_data.time
        return obs

    def sim_step(self):
        self._apply_pending_viewer_reset()
        self.obs = self.prepare_obs()
        self.unitree_bridge.PublishLowState(self.obs)
        if self.unitree_bridge.joystick:
            self.unitree_bridge.PublishWirelessController()
        if self.elastic_band:
            if self.elastic_band.enable and self.use_floating_root_link:
                pose = np.concatenate(
                    [
                        self.mj_data.xpos[self.band_attached_link],
                        self.mj_data.xquat[self.band_attached_link],
                        np.zeros(6),
                    ]
                )
                mujoco.mj_objectVelocity(
                    self.mj_model,
                    self.mj_data,
                    mujoco.mjtObj.mjOBJ_BODY,
                    self.band_attached_link,
                    pose[7:13],
                    0,
                )
                pose[7:10], pose[10:13] = pose[10:13], pose[7:10].copy()
                self.mj_data.xfrc_applied[self.band_attached_link] = self.elastic_band.Advance(pose)
            else:
                self.mj_data.xfrc_applied[self.band_attached_link] = np.zeros(6)
        body_torques = self.compute_body_torques()
        hand_torques = self.compute_hand_torques()
        # -1: actuator array is 0-based while joint indices from the model are 1-based
        self.torques[self.body_joint_index - 1] = body_torques
        if self.num_hand_dof > 0:
            self.torques[self.left_hand_index - 1] = hand_torques[: self.num_hand_dof]
            self.torques[self.right_hand_index - 1] = hand_torques[self.num_hand_dof :]

        self.torques = np.clip(self.torques, -self.torque_limit, self.torque_limit)

        if self.config["FREE_BASE"]:
            # Prepend 6 zeros for the floating-base root DOF actuators
            self.mj_data.ctrl = np.concatenate((np.zeros(6), self.torques))
        else:
            self.mj_data.ctrl = self.torques
        mujoco.mj_step(self.mj_model, self.mj_data)

        self.check_fall()

    def apply_perturbation(self, key):
        perturbation_x_body = 0.0
        perturbation_y_body = 0.0
        if key == "up":
            perturbation_x_body = 1.0
        elif key == "down":
            perturbation_x_body = -1.0
        elif key == "left":
            perturbation_y_body = 1.0
        elif key == "right":
            perturbation_y_body = -1.0

        vel_body = np.array([perturbation_x_body, perturbation_y_body, 0.0])
        vel_world = np.zeros(3)
        base_quat = self.mj_data.qpos[3:7]
        mujoco.mju_rotVecQuat(vel_world, vel_body, base_quat)

        self.mj_data.qvel[0] += vel_world[0]
        self.mj_data.qvel[1] += vel_world[1]
        mujoco.mj_forward(self.mj_model, self.mj_data)

    def update_viewer(self):
        if self.viewer is not None:
            self.viewer.sync()

    def update_viewer_camera(self):
        if self.viewer is not None:
            if self.viewer.cam.type == mujoco.mjtCamera.mjCAMERA_TRACKING:
                self.viewer.cam.type = mujoco.mjtCamera.mjCAMERA_FREE
            else:
                self.viewer.cam.type = mujoco.mjtCamera.mjCAMERA_TRACKING

    def update_reward(self):
        with self.reward_lock:
            self.last_reward = 0

    def get_reward(self):
        with self.reward_lock:
            return self.last_reward

    def set_unitree_bridge(self, unitree_bridge):
        self.unitree_bridge = unitree_bridge

    def get_privileged_obs(self):
        return {}

    def _update_third_person_camera(self):
        """Move the named chase camera with the pelvis before rendering."""
        if self._third_person_camera_offset is None:
            return

        self.mj_model.cam_pos[self._third_person_camera_id] = (
            self.mj_data.xpos[self.root_body_id] + self._third_person_camera_offset
        )
        # Refresh only derived camera/light transforms; do not advance or
        # recompute the simulation state.
        mujoco.mj_camlight(self.mj_model, self.mj_data)

    def update_render_caches(self):
        self._update_third_person_camera()
        render_caches = {}
        for camera_name, camera_config in self.camera_configs.items():
            renderer = self.renderers[camera_name]
            if "params" in camera_config:
                renderer.update_scene(self.mj_data, camera=camera_config["params"])
            elif "mjcf_name" in camera_config:
                renderer.update_scene(self.mj_data, camera=camera_config["mjcf_name"])
            else:
                renderer.update_scene(self.mj_data, camera=camera_name)
            render_caches[camera_name + "_image"] = renderer.render()

        if self.image_publish_process is not None:
            self.image_publish_process.update_shared_memory(render_caches)

        return render_caches

    def handle_keyboard_button(self, key):
        if self.elastic_band:
            self.elastic_band.handle_keyboard_button(key)

        if key == "backspace":
            self.reset(randomize_spawns=True)
        if key == "v":
            self.update_viewer_camera()
        if key in ["up", "down", "left", "right"]:
            self.apply_perturbation(key)

    def _mujoco_key_callback(self, key):
        """Forward raw viewer keys and defer data changes to the simulation thread."""
        import glfw

        if self.elastic_band:
            self.elastic_band.MujuocoKeyCallback(key)
        if key == glfw.KEY_BACKSPACE:
            self._viewer_reset_requested_at = time.monotonic()

    def _apply_pending_viewer_reset(self):
        requested_at = self._viewer_reset_requested_at
        if requested_at is None or time.monotonic() - requested_at < 0.02:
            return
        self._viewer_reset_requested_at = None
        self.reset(randomize_spawns=True)

    def check_fall(self):
        self.fall = False
        if self.mj_data.qpos[2] < 0.2:
            self.fall = True
            print(f"Warning: Robot has fallen, height: {self.mj_data.qpos[2]:.3f} m")

        if self.fall:
            self.reset()

    def check_self_collision(self):
        robot_bodies = get_subtree_body_names(self.mj_model, self.mj_model.body(self.root_body).id)
        self_collision, contact_bodies = check_contact(
            self.mj_model, self.mj_data, robot_bodies, robot_bodies, return_all_contact_bodies=True
        )
        if self_collision:
            print(f"Warning: Self-collision detected: {contact_bodies}")
        return self_collision

    def _randomize_reset_spawns(self):
        """Randomize free joints declared by scene custom numerics.

        ``random_spawn_<freejoint>`` samples an axis-aligned rectangle using
        ``x_min x_max y_min y_max z yaw_min yaw_max``.

        ``random_spawn_annulus_<freejoint>`` samples around a point using
        ``center_x center_y radius_min radius_max z yaw_min yaw_max``.  An
        optional ``spawn_avoid_aabb_<freejoint>`` numeric supplies
        ``x_min x_max y_min y_max clearance`` for a forbidden table/obstacle
        region.  ``random_spawn_annulus_body_<mocap body>`` uses the same
        annulus format for a kinematic body.  ``random_spawn_on_body_<freejoint>
        __<body>`` places an object relative to a randomized support body using
        ``dx_min dx_max dy_min dy_max z_offset yaw_min yaw_max``.  Angles are
        in radians. ``spawn_distance_body_<object>_<reference body>`` can
        additionally constrain an object's sampled x/y distance with
        ``min_distance max_distance``. ``random_presence_<freejoint>`` uses
        ``active_probability hidden_x hidden_y hidden_z`` to make an object
        optional on randomized resets.
        """
        rectangle_prefix = "random_spawn_"
        annulus_prefix = "random_spawn_annulus_"
        body_annulus_prefix = "random_spawn_annulus_body_"
        on_body_prefix = "random_spawn_on_body_"
        presence_prefix = "random_presence_"
        randomized = []

        self._randomize_mocap_reset_bodies(body_annulus_prefix, randomized)
        self._randomize_on_body_reset_joints(on_body_prefix, randomized)
        presence_states = self._randomize_reset_presence(presence_prefix)

        for numeric_id in range(self.mj_model.nnumeric):
            numeric_name = mujoco.mj_id2name(
                self.mj_model, mujoco.mjtObj.mjOBJ_NUMERIC, numeric_id
            )
            if not numeric_name:
                continue

            if numeric_name.startswith(body_annulus_prefix):
                continue
            if numeric_name.startswith(on_body_prefix):
                continue
            if numeric_name.startswith(annulus_prefix):
                spawn_mode = "annulus"
                joint_name = numeric_name[len(annulus_prefix) :]
            elif numeric_name.startswith(rectangle_prefix):
                spawn_mode = "rectangle"
                joint_name = numeric_name[len(rectangle_prefix) :]
            else:
                continue

            joint_id = mujoco.mj_name2id(
                self.mj_model, mujoco.mjtObj.mjOBJ_JOINT, joint_name
            )
            if joint_id == -1:
                print(
                    f"Warning: Reset randomization '{numeric_name}' refers to "
                    f"unknown joint '{joint_name}'"
                )
                continue
            if self.mj_model.jnt_type[joint_id] != mujoco.mjtJoint.mjJNT_FREE:
                print(f"Warning: Reset randomization joint '{joint_name}' is not a free joint")
                continue

            numeric_size = self.mj_model.numeric_size[numeric_id]
            numeric_adr = self.mj_model.numeric_adr[numeric_id]
            spawn_range = self.mj_model.numeric_data[numeric_adr : numeric_adr + numeric_size]
            if numeric_size != 7:
                print(
                    f"Warning: Reset randomization '{numeric_name}' needs 7 values, "
                    f"got {numeric_size}"
                )
                continue

            qpos_adr = self.mj_model.jnt_qposadr[joint_id]
            qvel_adr = self.mj_model.jnt_dofadr[joint_id]
            default_qpos = self.mj_data.qpos[qpos_adr : qpos_adr + 7].copy()
            default_qvel = self.mj_data.qvel[qvel_adr : qvel_adr + 6].copy()

            avoid_aabb = self._get_custom_numeric(f"spawn_avoid_aabb_{joint_name}")
            if avoid_aabb is not None and len(avoid_aabb) != 5:
                print(
                    f"Warning: 'spawn_avoid_aabb_{joint_name}' needs 5 values, "
                    f"got {len(avoid_aabb)}"
                )
                avoid_aabb = None

            sampled_pose = None
            for _ in range(100):
                if spawn_mode == "annulus":
                    center_x, center_y, radius_min, radius_max, z, yaw_min, yaw_max = (
                        spawn_range
                    )
                    if radius_min < 0 or radius_min > radius_max:
                        break
                    radius = np.sqrt(
                        self._reset_rng.uniform(radius_min**2, radius_max**2)
                    )
                    angle = self._reset_rng.uniform(-np.pi, np.pi)
                    x = center_x + radius * np.cos(angle)
                    y = center_y + radius * np.sin(angle)
                else:
                    x_min, x_max, y_min, y_max, z, yaw_min, yaw_max = spawn_range
                    if x_min > x_max or y_min > y_max:
                        break
                    x = self._reset_rng.uniform(x_min, x_max)
                    y = self._reset_rng.uniform(y_min, y_max)

                if yaw_min > yaw_max:
                    break
                yaw = self._reset_rng.uniform(yaw_min, yaw_max)
                if avoid_aabb is not None and self._point_is_near_aabb(x, y, avoid_aabb):
                    continue
                if self._point_is_near_avoided_body(joint_name, x, y):
                    continue
                if self._point_violates_body_distance(joint_name, x, y):
                    continue

                self.mj_data.qpos[qpos_adr : qpos_adr + 3] = [x, y, z]
                self.mj_data.qpos[qpos_adr + 3 : qpos_adr + 7] = [
                    np.cos(yaw / 2),
                    0.0,
                    0.0,
                    np.sin(yaw / 2),
                ]
                self.mj_data.qvel[qvel_adr : qvel_adr + 6] = 0.0
                mujoco.mj_forward(self.mj_model, self.mj_data)
                if not self._free_joint_has_external_contact(joint_id):
                    sampled_pose = (joint_name, x, y, yaw)
                    break

            if sampled_pose is None:
                self.mj_data.qpos[qpos_adr : qpos_adr + 7] = default_qpos
                self.mj_data.qvel[qvel_adr : qvel_adr + 6] = default_qvel
                print(f"Warning: Could not find a collision-free reset pose for '{joint_name}'")
            else:
                randomized.append(sampled_pose)

        if randomized or presence_states:
            mujoco.mj_forward(self.mj_model, self.mj_data)
        if randomized:
            for joint_name, x, y, yaw in randomized:
                print(
                    f"Randomized '{joint_name}' reset pose: "
                    f"x={x:.3f}, y={y:.3f}, yaw={np.degrees(yaw):.1f} deg"
                )
        for joint_name, active in presence_states:
            print(f"Randomized '{joint_name}' presence: {'active' if active else 'hidden'}")

    def _randomize_reset_presence(self, prefix):
        presence_states = []
        for numeric_id in range(self.mj_model.nnumeric):
            numeric_name = mujoco.mj_id2name(
                self.mj_model, mujoco.mjtObj.mjOBJ_NUMERIC, numeric_id
            )
            if not numeric_name or not numeric_name.startswith(prefix):
                continue

            joint_name = numeric_name[len(prefix) :]
            joint_id = mujoco.mj_name2id(
                self.mj_model, mujoco.mjtObj.mjOBJ_JOINT, joint_name
            )
            settings = self._get_custom_numeric(numeric_name)
            if (
                joint_id == -1
                or self.mj_model.jnt_type[joint_id] != mujoco.mjtJoint.mjJNT_FREE
                or settings is None
                or len(settings) != 4
            ):
                print(f"Warning: Invalid randomized-presence config '{numeric_name}'")
                continue

            active_probability, hidden_x, hidden_y, hidden_z = settings
            if not 0.0 <= active_probability <= 1.0:
                print(f"Warning: Presence probability must be in [0, 1] for '{joint_name}'")
                continue

            active = self._reset_rng.random() < active_probability
            if not active:
                qpos_adr = self.mj_model.jnt_qposadr[joint_id]
                qvel_adr = self.mj_model.jnt_dofadr[joint_id]
                self.mj_data.qpos[qpos_adr : qpos_adr + 3] = [
                    hidden_x,
                    hidden_y,
                    hidden_z,
                ]
                self.mj_data.qpos[qpos_adr + 3 : qpos_adr + 7] = [1.0, 0.0, 0.0, 0.0]
                self.mj_data.qvel[qvel_adr : qvel_adr + 6] = 0.0
            presence_states.append((joint_name, active))
        return presence_states

    def _randomize_mocap_reset_bodies(self, prefix, randomized):
        for numeric_id in range(self.mj_model.nnumeric):
            numeric_name = mujoco.mj_id2name(
                self.mj_model, mujoco.mjtObj.mjOBJ_NUMERIC, numeric_id
            )
            if not numeric_name or not numeric_name.startswith(prefix):
                continue

            body_name = numeric_name[len(prefix) :]
            body_id = mujoco.mj_name2id(self.mj_model, mujoco.mjtObj.mjOBJ_BODY, body_name)
            if body_id == -1:
                print(f"Warning: Reset randomization refers to unknown body '{body_name}'")
                continue
            mocap_id = self.mj_model.body_mocapid[body_id]
            if mocap_id == -1:
                print(f"Warning: Reset randomization body '{body_name}' is not a mocap body")
                continue

            spawn_range = self._get_custom_numeric(numeric_name)
            if spawn_range is None or len(spawn_range) != 7:
                print(f"Warning: Reset randomization '{numeric_name}' needs 7 values")
                continue
            center_x, center_y, radius_min, radius_max, z, yaw_min, yaw_max = spawn_range
            if radius_min < 0 or radius_min > radius_max or yaw_min > yaw_max:
                print(f"Warning: Reset randomization '{numeric_name}' has invalid bounds")
                continue

            default_pos = self.mj_data.mocap_pos[mocap_id].copy()
            default_quat = self.mj_data.mocap_quat[mocap_id].copy()
            sampled_pose = None
            for _ in range(100):
                radius = np.sqrt(self._reset_rng.uniform(radius_min**2, radius_max**2))
                angle = self._reset_rng.uniform(-np.pi, np.pi)
                yaw = self._reset_rng.uniform(yaw_min, yaw_max)
                x = center_x + radius * np.cos(angle)
                y = center_y + radius * np.sin(angle)
                if self._point_is_near_avoided_body(body_name, x, y):
                    continue
                if self._point_violates_body_distance(body_name, x, y):
                    continue
                self.mj_data.mocap_pos[mocap_id] = [x, y, z]
                self.mj_data.mocap_quat[mocap_id] = [
                    np.cos(yaw / 2),
                    0.0,
                    0.0,
                    np.sin(yaw / 2),
                ]
                mujoco.mj_forward(self.mj_model, self.mj_data)
                if not self._body_has_external_contact(body_id):
                    sampled_pose = (body_name, x, y, yaw)
                    break

            if sampled_pose is None:
                self.mj_data.mocap_pos[mocap_id] = default_pos
                self.mj_data.mocap_quat[mocap_id] = default_quat
                print(f"Warning: Could not find a collision-free reset pose for '{body_name}'")
            else:
                randomized.append(sampled_pose)

    def _randomize_on_body_reset_joints(self, prefix, randomized):
        for numeric_id in range(self.mj_model.nnumeric):
            numeric_name = mujoco.mj_id2name(
                self.mj_model, mujoco.mjtObj.mjOBJ_NUMERIC, numeric_id
            )
            if not numeric_name or not numeric_name.startswith(prefix):
                continue

            names = numeric_name[len(prefix) :].rsplit("__", 1)
            if len(names) != 2:
                print(f"Warning: Invalid on-body reset name '{numeric_name}'")
                continue
            joint_name, support_body_name = names
            joint_id = mujoco.mj_name2id(
                self.mj_model, mujoco.mjtObj.mjOBJ_JOINT, joint_name
            )
            support_body_id = mujoco.mj_name2id(
                self.mj_model, mujoco.mjtObj.mjOBJ_BODY, support_body_name
            )
            if joint_id == -1 or support_body_id == -1:
                print(f"Warning: Invalid on-body reset targets in '{numeric_name}'")
                continue
            if self.mj_model.jnt_type[joint_id] != mujoco.mjtJoint.mjJNT_FREE:
                print(f"Warning: On-body reset joint '{joint_name}' is not a free joint")
                continue

            spawn_range = self._get_custom_numeric(numeric_name)
            if spawn_range is None or len(spawn_range) != 7:
                print(f"Warning: On-body reset '{numeric_name}' needs 7 values")
                continue
            dx_min, dx_max, dy_min, dy_max, z_offset, yaw_min, yaw_max = spawn_range
            if dx_min > dx_max or dy_min > dy_max or yaw_min > yaw_max:
                print(f"Warning: On-body reset '{numeric_name}' has invalid bounds")
                continue

            qpos_adr = self.mj_model.jnt_qposadr[joint_id]
            qvel_adr = self.mj_model.jnt_dofadr[joint_id]
            default_qpos = self.mj_data.qpos[qpos_adr : qpos_adr + 7].copy()
            default_qvel = self.mj_data.qvel[qvel_adr : qvel_adr + 6].copy()
            support_pos = self.mj_data.xpos[support_body_id]
            support_quat = self.mj_data.xquat[support_body_id]
            support_yaw = 2 * np.arctan2(support_quat[3], support_quat[0])

            sampled_pose = None
            for _ in range(100):
                dx = self._reset_rng.uniform(dx_min, dx_max)
                dy = self._reset_rng.uniform(dy_min, dy_max)
                cos_yaw = np.cos(support_yaw)
                sin_yaw = np.sin(support_yaw)
                x = support_pos[0] + cos_yaw * dx - sin_yaw * dy
                y = support_pos[1] + sin_yaw * dx + cos_yaw * dy
                yaw = support_yaw + self._reset_rng.uniform(yaw_min, yaw_max)
                if self._point_is_near_avoided_body(joint_name, x, y):
                    continue
                if self._point_violates_body_distance(joint_name, x, y):
                    continue

                self.mj_data.qpos[qpos_adr : qpos_adr + 3] = [
                    x,
                    y,
                    support_pos[2] + z_offset,
                ]
                self.mj_data.qpos[qpos_adr + 3 : qpos_adr + 7] = [
                    np.cos(yaw / 2),
                    0.0,
                    0.0,
                    np.sin(yaw / 2),
                ]
                self.mj_data.qvel[qvel_adr : qvel_adr + 6] = 0.0
                mujoco.mj_forward(self.mj_model, self.mj_data)
                if not self._body_has_external_contact(
                    self.mj_model.jnt_bodyid[joint_id],
                    allowed_body_ids=(support_body_id,),
                ):
                    sampled_pose = (joint_name, x, y, yaw)
                    break

            if sampled_pose is None:
                self.mj_data.qpos[qpos_adr : qpos_adr + 7] = default_qpos
                self.mj_data.qvel[qvel_adr : qvel_adr + 6] = default_qvel
                print(f"Warning: Could not place '{joint_name}' on '{support_body_name}'")
            else:
                randomized.append(sampled_pose)

    def _get_custom_numeric(self, name):
        numeric_id = mujoco.mj_name2id(self.mj_model, mujoco.mjtObj.mjOBJ_NUMERIC, name)
        if numeric_id == -1:
            return None
        numeric_size = self.mj_model.numeric_size[numeric_id]
        numeric_adr = self.mj_model.numeric_adr[numeric_id]
        return self.mj_model.numeric_data[numeric_adr : numeric_adr + numeric_size]

    @staticmethod
    def _point_is_near_aabb(x, y, avoid_aabb):
        x_min, x_max, y_min, y_max, clearance = avoid_aabb
        dx = max(x_min - x, 0.0, x - x_max)
        dy = max(y_min - y, 0.0, y - y_max)
        return np.hypot(dx, dy) < clearance

    def _point_is_near_avoided_body(self, joint_name, x, y):
        prefix = f"spawn_avoid_body_{joint_name}_"
        for numeric_id in range(self.mj_model.nnumeric):
            numeric_name = mujoco.mj_id2name(
                self.mj_model, mujoco.mjtObj.mjOBJ_NUMERIC, numeric_id
            )
            if not numeric_name or not numeric_name.startswith(prefix):
                continue
            body_name = numeric_name[len(prefix) :]
            min_distance = self._get_custom_numeric(numeric_name)
            body_id = mujoco.mj_name2id(self.mj_model, mujoco.mjtObj.mjOBJ_BODY, body_name)
            if min_distance is None or len(min_distance) != 1 or body_id == -1:
                print(f"Warning: Invalid avoided-body config '{numeric_name}'")
                continue
            body_distance = np.hypot(
                x - self.mj_data.xpos[body_id, 0],
                y - self.mj_data.xpos[body_id, 1],
            )
            if body_distance < min_distance[0]:
                return True
        return False

    def _point_violates_body_distance(self, object_name, x, y):
        prefix = f"spawn_distance_body_{object_name}_"
        for numeric_id in range(self.mj_model.nnumeric):
            numeric_name = mujoco.mj_id2name(
                self.mj_model, mujoco.mjtObj.mjOBJ_NUMERIC, numeric_id
            )
            if not numeric_name or not numeric_name.startswith(prefix):
                continue
            body_name = numeric_name[len(prefix) :]
            distance_range = self._get_custom_numeric(numeric_name)
            body_id = mujoco.mj_name2id(self.mj_model, mujoco.mjtObj.mjOBJ_BODY, body_name)
            if distance_range is None or len(distance_range) != 2 or body_id == -1:
                print(f"Warning: Invalid body-distance config '{numeric_name}'")
                continue
            min_distance, max_distance = distance_range
            body_distance = np.hypot(
                x - self.mj_data.xpos[body_id, 0],
                y - self.mj_data.xpos[body_id, 1],
            )
            if min_distance > max_distance or not min_distance <= body_distance <= max_distance:
                return True
        return False

    def _body_has_external_contact(self, body_id, allowed_body_ids=()):
        allowed_body_ids = {0, body_id, *allowed_body_ids}
        for contact_id in range(self.mj_data.ncon):
            contact = self.mj_data.contact[contact_id]
            body_1 = self.mj_model.geom_bodyid[contact.geom1]
            body_2 = self.mj_model.geom_bodyid[contact.geom2]
            if body_1 == body_id and body_2 not in allowed_body_ids:
                return True
            if body_2 == body_id and body_1 not in allowed_body_ids:
                return True
        return False

    def _free_joint_has_external_contact(self, joint_id):
        body_id = self.mj_model.jnt_bodyid[joint_id]
        return self._body_has_external_contact(body_id)

    def reset(self, randomize_spawns=False):
        mujoco.mj_resetData(self.mj_model, self.mj_data)
        if randomize_spawns:
            self._randomize_reset_spawns()


class BaseSimulator:
    """Base simulator class that handles initialization and running of simulations"""

    def __init__(
        self, config: Dict[str, any], env_name: str = "default", redis_client=None, **kwargs
    ):
        self.config = config
        self.env_name = env_name
        self.redis_client = redis_client
        if self.redis_client is not None:
            self.redis_client.set("push_left_hand", "false")
            self.redis_client.set("push_right_hand", "false")
            self.redis_client.set("push_torso", "false")

        # Create rate objects
        self.sim_dt = self.config["SIMULATE_DT"]
        self.reward_dt = self.config.get("REWARD_DT", 0.02)
        self.image_dt = self.config.get("IMAGE_DT", 0.033333)
        self.viewer_dt = self.config.get("VIEWER_DT", 0.02)
        self._running = True

        self.robot = Robot(self.config)

        # Create the environment
        if env_name == "default":
            self.sim_env = DefaultEnv(config, env_name, **kwargs)
        else:
            raise ValueError(
                f"Invalid environment name: {env_name}. "
                f"Only 'default' is supported in this minimal build."
            )

        try:
            if self.config.get("INTERFACE", None):
                ChannelFactoryInitialize(self.config["DOMAIN_ID"], self.config["INTERFACE"])
            else:
                ChannelFactoryInitialize(self.config["DOMAIN_ID"])
        except Exception as e:
            print(f"Note: Channel factory initialization attempt: {e}")

        self.init_unitree_bridge()
        self.sim_env.set_unitree_bridge(self.unitree_bridge)

        self.init_subscriber()
        self.init_publisher()

        self.scene_reset_subscriber = None
        if self.config.get("ENABLE_PICO_SCENE_RESET", False):
            try:
                from gear_sonic.utils.mujoco_sim.scene_reset_subscriber import (
                    SceneResetSubscriber,
                )

                host = self.config.get("PICO_SCENE_RESET_HOST", "localhost")
                port = self.config.get("PICO_SCENE_RESET_PORT", 5556)
                self.scene_reset_subscriber = SceneResetSubscriber(host=host, port=port)
                print(f"PICO scene reset enabled on tcp://{host}:{port}")
            except Exception as exc:
                print(f"Warning: Failed to initialize PICO scene reset: {exc}")

        self.sim_thread = None

    def start_as_thread(self):
        self.sim_thread = Thread(target=self.start)
        self.sim_thread.start()

    def start_image_publish_subprocess(self, start_method: str = "spawn", camera_port: int = 5555):
        self.sim_env.start_image_publish_subprocess(start_method, camera_port)

    def init_subscriber(self):
        pass

    def init_publisher(self):
        pass

    def init_unitree_bridge(self):
        self.unitree_bridge = UnitreeSdk2Bridge(self.config)
        if self.config["USE_JOYSTICK"]:
            self.unitree_bridge.SetupJoystick(
                device_id=self.config["JOYSTICK_DEVICE"], js_type=self.config["JOYSTICK_TYPE"]
            )

    def start(self):
        """Main simulation loop"""
        sim_cnt = 0
        ts = time.time()

        try:
            while self._running and (
                (self.sim_env.viewer and self.sim_env.viewer.is_running())
                or (self.sim_env.viewer is None)
            ):
                step_start = time.monotonic()

                if self.scene_reset_subscriber is not None and self.scene_reset_subscriber.poll():
                    print("PICO requested MuJoCo scene reset/randomization")
                    self.sim_env.reset(randomize_spawns=True)
                self.sim_env.sim_step()
                now = time.time()
                if now - ts > 1 / 10.0 and self.redis_client is not None:
                    head_pose = self.sim_env.get_head_pose()
                    self.redis_client.set("head_pos", pickle.dumps(head_pose[:3]))
                    self.redis_client.set("head_quat", pickle.dumps(head_pose[3:]))
                    ts = now

                if sim_cnt % int(self.viewer_dt / self.sim_dt) == 0:
                    self.sim_env.update_viewer()

                if sim_cnt % int(self.reward_dt / self.sim_dt) == 0:
                    self.sim_env.update_reward()

                if sim_cnt % int(self.image_dt / self.sim_dt) == 0:
                    self.sim_env.update_render_caches()

                # Simple rate limiter (replaces ROS rate)
                elapsed = time.monotonic() - step_start
                sleep_time = self.sim_dt - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

                sim_cnt += 1
        except KeyboardInterrupt:
            print("Simulator interrupted by user.")
        finally:
            self.close()

    def __del__(self):
        self.close()

    def reset(self):
        self.sim_env.reset()

    def close(self):
        self._running = False
        try:
            if self.scene_reset_subscriber is not None:
                self.scene_reset_subscriber.close()
                self.scene_reset_subscriber = None
            if self.sim_env.image_publish_process is not None:
                self.sim_env.image_publish_process.stop()
            if self.sim_env.viewer is not None:
                self.sim_env.viewer.close()
        except Exception as e:
            print(f"Warning during close: {e}")

    def get_privileged_obs(self):
        return self.sim_env.get_privileged_obs()

    def handle_keyboard_button(self, key):
        self.sim_env.handle_keyboard_button(key)
