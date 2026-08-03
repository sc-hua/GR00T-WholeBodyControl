# Web Robot Viewer Tool Selection

Use the smallest maintained stack that satisfies the interaction model.

## Recommended baseline

### Three.js + urdf-loader + camera-controls

Choose this for a focused embedded viewer, recorded joint playback, or a local operator tool.

- [`three`](https://github.com/mrdoob/three.js): renderer, scene graph, bounds, raycasting, lights, and materials.
- [`urdf-loader`](https://github.com/gkjohnson/urdf-loaders): URDF kinematic tree, mesh loading, named joints, limits, and optional drag helpers.
- [`camera-controls`](https://github.com/yomotsu/camera-controls): orbit/dolly/pan plus `fitToBox`, `fitToSphere`, `setLookAt`, clipping, transitions, and reset.

This combination keeps direct control of the scene and integrates cleanly with non-3D product UI.

## React-heavy scene composition

### React Three Fiber + Drei

Choose when the application will contain multiple declarative 3D layers, annotations, reusable scene components, or extensive React-driven interaction.

- [`@react-three/fiber`](https://github.com/pmndrs/react-three-fiber) renders Three.js objects through React.
- [`@react-three/drei`](https://github.com/pmndrs/drei) provides `Bounds`, controls, labels, gizmos, loaders, and performance helpers.
- Use `<primitive object={robot} />` for an externally loaded URDF object.
- Use `Bounds` or `useBounds().refresh(robot).fit().clip()` after geometry and pose are ready.

Do not adopt this stack only to replace one small imperative viewer; it adds abstraction and migration cost.

## Ready-made URDF web component

`urdf-loader/src/urdf-viewer-element.js` demonstrates a complete custom element with loading events, lights, shadows, resize, controls, material updates, and resource disposal.

Use it for a standalone viewer or prototype. For an embedded product, prefer borrowing its lifecycle patterns because the component owns its renderer, DOM, camera, and private recenter behavior, which can make custom synchronization harder.

## ROS and robotics observability products

Use Foxglove or a similar robotics visualization platform when the primary need is ROS/MCAP/TF inspection rather than embedding a tailored viewer in an existing workflow. Avoid embedding a full observability product merely to display one robot.

## Specialized additions

- Use `URDFDragControls` or the manipulator element for direct joint rotation.
- Add a maintained IK solver only for end-effector target editing.
- Use CSS/DOM overlays for product controls and accessible data; reserve 3D labels for spatial information.
- Keep custom Canvas/WebGL code for highly specialized timelines or overlays that general charting libraries cannot express efficiently.
