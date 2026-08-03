---
name: integrate-web-robot-viewer
description: Design, implement, refactor, or debug browser-based robot visualization and interaction using URDF/STL/DAE/mesh assets, Three.js, React, joint-state streams, camera controls, picking, manipulation, and surrounding operator UI. Use when adding a robot viewer to a web app, driving a robot model from recorded or live joint data, building reset/fit/fullscreen/selection controls, or fixing model loading, framing, coordinate, performance, and lifecycle problems.
---

# Integrate Web Robot Viewer

Build a robot viewer as an isolated system with explicit model, scene, camera, interaction, and UI boundaries. Preserve the host product's workflow; the 3D view supports the task rather than becoming the entire interface.

## Establish requirements

Determine these from code and assets before choosing an implementation:

- Model source: URDF plus STL/DAE meshes, glTF, or a runtime ROS description.
- Asset resolution: HTTP URLs, `package://` mapping, local API, or bundled files.
- Coordinate convention and up axis; units and expected robot scale.
- Pose source: static values, recorded frames, `JointState`, TF, WebSocket, or user manipulation.
- Required interaction: orbit, dolly, pan, reset, link selection, joint dragging, IK, collision display, or annotations.
- Host constraints: React or vanilla DOM, offline/local operation, screen density, expected update rate, and browser support.

Read [references/tool-selection.md](references/tool-selection.md) before adding or replacing a 3D dependency.

## Separate responsibilities

Keep these layers distinct even when they live in one small component:

1. **Asset adapter** — resolve URLs and load the robot plus all referenced geometry.
2. **Robot model** — expose joints by stable name and apply values in radians.
3. **Scene renderer** — own scene, lights, grid, camera, renderer, and resize behavior.
4. **Camera controller** — own fit, reset, orbit/dolly/pan, bounds, and clipping planes.
5. **Interaction adapter** — raycast links, highlight selection, drag joints, or expose IK.
6. **Product UI** — show loading/error state and only the controls needed by the operator.

In React, keep the loaded robot and Three.js objects in refs. Do not recreate the renderer or reload geometry on every pose update.

## Load URDF geometry reliably

- Use one `THREE.LoadingManager` for the URDF and all referenced mesh loaders.
- Treat the URDF parser callback as “kinematic tree created,” not “all geometry ready.”
- Perform initial framing, shadow setup, material traversal, and model reveal only after the manager reports all geometry loaded.
- In React StrictMode, duplicate loader instances can share an identical in-flight `FileLoader` request. The later `LoadingManager` may then miss mesh `itemStart`/`itemEnd` events. Wrap `loadMeshCb` and count its completion callbacks directly, or otherwise deduplicate the whole robot load outside the component; never trust a manager event that can belong only to one mount.
- Resolve `package://` paths explicitly at the asset/API boundary.
- Report missing meshes as an actionable error; do not silently frame a partial robot.
- Apply the initial joint pose before computing bounds, then call `updateWorldMatrix(true, true)`.
- Compute bounds from visual geometry unless collision display is intentionally active.

## Drive the robot pose

- Map values by URDF joint name, never by assumed object traversal order.
- Confirm radians versus degrees and clamp only when the product should respect URDF limits.
- Keep missing joints unchanged or apply a documented neutral value; never shift indices to compensate.
- Batch one pose update per animation frame for live streams.
- Interpolate recorded states only when the host timeline requires it.
- Keep floating-base translation/orientation separate from articulated joint values.
- Update bounds on reset when extreme poses can materially change the silhouette.

## Fit and control the camera

- Prefer a maintained controller with `fitToSphere` or `fitToBox`, `setLookAt`, clipping, and reset support over handwritten perspective math.
- Use a bounding sphere for view-direction-independent full-body framing; account for both horizontal and vertical field of view when implementing custom math.
- Choose a product-specific default direction, commonly a slightly elevated diagonal view.
- Include deliberate margin around hands, feet, tools, and annotations.
- Make “reset view” recompute from the complete current robot. Do not restore a bad snapshot captured during partial loading.
- Recompute camera aspect and projection on container resize. Avoid resetting a user's manual view during ordinary responsive layout changes.
- Keep near/far planes proportional to the fitted bounds to prevent clipping and depth precision loss.

## Add operator interaction deliberately

- Start with orbit, wheel dolly, right-button pan, reset, and loading status.
- Add raycast selection only when another UI surface consumes the selected link or joint.
- Highlight with reversible material state; do not permanently replace URDF materials.
- For direct joint manipulation, enforce joint type, axis, and limits and provide a clear way to return to recorded pose.
- Use IK only for an explicit end-effector editing workflow; do not add it for passive playback.
- Keep 3D gestures from stealing keyboard shortcuts or pointer actions belonging to the host review/editor UI.

## Build the surrounding UI

Useful controls include reset, fullscreen, visual/collision toggle, grid toggle, camera preset, selected link/joint, and playback status. Add only controls the workflow needs.

- Overlay controls with readable contrast and stable hit targets.
- Disable actions until geometry is ready.
- Expose loading and mesh errors in text, not only through an empty canvas.
- Provide accessible button names and keyboard focus.
- Keep detailed joint tables or graphs outside the canvas so they remain searchable and testable DOM.

## Protect performance and lifecycle

- Cap device pixel ratio, normally at 2.
- Render continuously only for animation, damping, or live pose streams; otherwise render on change.
- Cache robot geometry across state updates and avoid cloning materials per frame.
- Dispose animation frames, observers, controls, renderer, geometry, textures, and owned materials on unmount.
- Do not dispose shared cached assets still used by another viewer.
- Test slow and failed mesh loading, resize, repeated mount/unmount, and rapid joint updates.

## Validate

Run the host project's lint, type check, production build, and UI tests. Also verify in a real browser with the actual robot assets:

1. Load repeatedly with cold and warm caches; framing must be identical.
2. Confirm the complete robot remains visible in wide and narrow viewer containers.
3. Rotate, dolly, and pan, then reset to a full-body view.
4. Seek or stream joint values and verify joints by name.
5. Confirm loading/error states and missing geometry behavior.
6. Mount and unmount repeatedly without duplicate canvases, listeners, or WebGL leaks.

Keep the viewer local when the host application handles private robot or dataset assets. Do not introduce deployment, authentication, or telemetry unless explicitly required.
