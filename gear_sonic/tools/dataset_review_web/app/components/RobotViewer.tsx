import CameraControls from "camera-controls";
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import URDFLoader, { URDFRobot } from "urdf-loader";

CameraControls.install({ THREE });

type RobotViewerProps = {
  joints: number[];
  jointNames: string[];
  modelUrl: string;
};

const VIEW_DIRECTION = new THREE.Vector3(1, 0.55, 1.15).normalize();
const VIEW_MARGIN = 1.8;

function disposeObject(root: THREE.Object3D) {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();

  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return;
    geometries.add(object.geometry);
    const meshMaterials = Array.isArray(object.material) ? object.material : [object.material];
    meshMaterials.forEach((material) => {
      materials.add(material);
      Object.values(material).forEach((value) => {
        if (value instanceof THREE.Texture) textures.add(value);
      });
    });
  });

  textures.forEach((texture) => texture.dispose());
  materials.forEach((material) => material.dispose());
  geometries.forEach((geometry) => geometry.dispose());
}

export default function RobotViewer({ joints, jointNames, modelUrl }: RobotViewerProps) {
  const mountRef = useRef<HTMLDivElement>(null);
  const robotRef = useRef<URDFRobot | null>(null);
  const poseRef = useRef({ joints, jointNames });
  const resetViewRef = useRef<() => void>(() => undefined);
  const [modelStatus, setModelStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    poseRef.current = { joints, jointNames };
  }, [jointNames, joints]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 30);
    camera.position.set(1.02, 0.98, 1.28);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    mount.appendChild(renderer.domElement);

    const controls = new CameraControls(camera, renderer.domElement);
    controls.smoothTime = 0.22;
    controls.draggingSmoothTime = 0.1;

    scene.add(new THREE.HemisphereLight(0xdaf2ee, 0x111918, 2.4));
    const key = new THREE.DirectionalLight(0xffead0, 4.2);
    key.position.set(2, 4, 3);
    key.castShadow = true;
    scene.add(key);
    const rim = new THREE.DirectionalLight(0x73d8c9, 2.1);
    rim.position.set(-3, 2, -2);
    scene.add(rim);

    const grid = new THREE.GridHelper(4, 16, 0x3a5a55, 0x233331);
    grid.position.y = 0;
    scene.add(grid);

    const manager = new THREE.LoadingManager();
    let disposed = false;
    let finalized = false;
    let urdfParsed = false;
    let meshRequests = 0;
    let meshesSettled = 0;
    let meshFailures = 0;
    let loadedRobot: URDFRobot | null = null;

    const frameRobot = (robot: URDFRobot) => {
      if (disposed) return false;
      robot.updateWorldMatrix(true, true);
      const bounds = new THREE.Box3().setFromObject(robot);
      const sphere = bounds.getBoundingSphere(new THREE.Sphere());
      if (!Number.isFinite(sphere.radius) || sphere.radius <= 0) return false;

      const framingSphere = sphere.clone();
      framingSphere.radius *= VIEW_MARGIN;
      const initialPosition = sphere.center.clone().addScaledVector(VIEW_DIRECTION, framingSphere.radius * 4);
      void controls.setLookAt(
        initialPosition.x,
        initialPosition.y,
        initialPosition.z,
        sphere.center.x,
        sphere.center.y,
        sphere.center.z,
        false,
      );
      void controls.fitToSphere(framingSphere, false);

      const defaultDistance = controls.distance;
      controls.minDistance = defaultDistance * 0.45;
      controls.maxDistance = defaultDistance * 2.5;
      camera.near = Math.max(0.01, defaultDistance / 100);
      camera.far = Math.max(30, defaultDistance * 10);
      camera.updateProjectionMatrix();
      controls.saveState();
      return true;
    };

    resetViewRef.current = () => {
      void controls.reset(true);
    };

    const finalizeRobot = () => {
      const robot = loadedRobot;
      if (!urdfParsed || meshesSettled !== meshRequests || !robot || finalized || disposed) return;

      if (meshFailures > 0) {
        setModelStatus("error");
        return;
      }

      robot.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.castShadow = true;
          object.receiveShadow = true;
        }
      });
      scene.add(robot);

      const pose = poseRef.current;
      pose.jointNames.forEach((name, index) => robot.setJointValue(name, pose.joints[index] ?? 0));
      if (!frameRobot(robot)) {
        setModelStatus("error");
        return;
      }

      finalized = true;
      setModelStatus("ready");
    };

    manager.onLoad = () => {
      if (disposed) {
        if (loadedRobot) disposeObject(loadedRobot);
      }
    };

    const loader = new URDFLoader(manager);
    loader.parseCollision = false;
    // Three.js coalesces identical in-flight FileLoader requests. In React StrictMode,
    // a remounted loader can therefore miss LoadingManager itemStart/itemEnd events.
    // Count URDF mesh callbacks directly so framing never runs against an empty robot.
    const loadMesh = loader.loadMeshCb;
    loader.loadMeshCb = (path, meshManager, material, done) => {
      meshRequests += 1;
      loadMesh(path, meshManager, material, (object, error) => {
        done(object, error);
        meshesSettled += 1;
        if (error) meshFailures += 1;
        requestAnimationFrame(finalizeRobot);
      });
    };
    loader.load(
      modelUrl,
      (robot) => {
        loadedRobot = robot;
        if (disposed) return;
        robot.rotation.x = -Math.PI / 2;
        robot.position.y = 0.82;
        robotRef.current = robot;
        urdfParsed = true;
        requestAnimationFrame(finalizeRobot);
      },
      undefined,
      () => {
        if (!disposed) setModelStatus("error");
      },
    );

    const resize = () => {
      const width = Math.max(1, mount.clientWidth);
      const height = Math.max(1, mount.clientHeight);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);
    resize();

    const clock = new THREE.Clock();
    let animation = 0;
    const render = () => {
      controls.update(clock.getDelta());
      renderer.render(scene, camera);
      animation = requestAnimationFrame(render);
    };
    render();

    return () => {
      disposed = true;
      cancelAnimationFrame(animation);
      observer.disconnect();
      controls.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      disposeObject(scene);
      if (loadedRobot && loadedRobot.parent !== scene) disposeObject(loadedRobot);
      if (robotRef.current === loadedRobot) robotRef.current = null;
      resetViewRef.current = () => undefined;
    };
  }, [modelUrl]);

  useEffect(() => {
    const robot = robotRef.current;
    if (!robot) return;
    jointNames.forEach((name, index) => robot.setJointValue(name, joints[index] ?? 0));
  }, [jointNames, joints]);

  return (
    <div ref={mountRef} className="robot-canvas" aria-label="G1 机器人真实模型姿态视图">
      <button
        type="button"
        className="robot-reset-view"
        disabled={modelStatus !== "ready"}
        onClick={() => resetViewRef.current()}
      >
        重置视角
      </button>
      <span className={`robot-model-status ${modelStatus}`}>
        {modelStatus === "loading" ? "正在加载 G1 网格…" : modelStatus === "error" ? "G1 模型加载失败" : "G1 43-DoF · URDF"}
      </span>
    </div>
  );
}
