# 当前工作状态：PICO 全身遥操作、XR 视觉与瓶子投桶数据采集

最后更新：2026-08-04（Asia/Shanghai）

这是一份面向后续开发者和新对话的接手文档。开始继续工作前，请先阅读本页，再运行
`git status --short` 核对工作区，因为文末记录的提交状态会随着后续开发变化。

## 1. 当前目标

当前要搭建的是一套在 MuJoCo 中使用 PICO 遥操作 G1 43 DoF（含 Dex3 双手）采集全身任务数据的流程。
当前任务是：

> G1 找到附近随机桌上的 1 个瓶子，将它拿起，走到旁边随机生成的垃圾桶并投入桶内。

相较此前的双手搬箱任务，瓶子更轻、更容易单手抓握；任务仍包含寻找目标、上肢抓取、行走、
转身和投放等全身动作。当前场景不会自动判断任务成功，是否完成仍由操作者决定。

## 2. 当前代码与 Git 状态快照

记录本页时：

- 仓库：`/data/pateo/proj/robot/GR00T-WholeBodyControl`
- 分支：`local`
- HEAD：`a50b46c`，提交信息为 `feat: add ego video sender`
- `a50b46c` 已包含 XRoboToolkit Remote Vision 视频桥接器、启动器集成和协议单测。
- 瓶子投桶场景、Backspace 随机复位和 XR 多视角 dashboard 仍在工作区，尚未进入上述提交。
- 根目录 `run.sh` 是已有的个人命令草稿，记录本页时处于 staged 状态；其中仍有旧的双方块场景命令，
  不应把它当作当前瓶子投桶任务的权威启动命令。

记录时相关 `git status --short` 为：

```text
AM docs/note/README.md
 M gear_sonic/scripts/launch_data_collection.py
 M gear_sonic/scripts/run_xrobotoolkit_video_sender.py
 M gear_sonic/tests/test_xrobotoolkit_video_sender.py
MM gear_sonic/utils/mujoco_sim/base_sim.py
?? gear_sonic/utils/mujoco_sim/scenes/scene_43dof_bottle_to_bin.xml
AM gear_sonic/utils/mujoco_sim/scenes/scene_43dof_tote_carry.xml
 M gear_sonic/utils/teleop/xrobotoolkit_video_sender.py
A  run.sh
```

继续开发时不要重置或覆盖这些改动，也不要把 `run.sh` 当成由本任务创建的文件。

## 3. 已完成的功能

### 3.1 XRoboToolkit 追踪链路

PICO 端 XRoboToolkit App 和 PC 端 XRoboToolkit PC Service 用于头显、手柄和 Motion Tracker 数据。
PICO 界面的 `Network -> Status: WORKING` 只表示这条追踪链路已经连通。

建议的 PICO Tracking 配置：

- `Head`：开启。
- `Controller`：开启。
- `Hand`：使用控制器/Dex3 映射时通常关闭；只有明确改用裸手追踪时才开启。
- `PICO Motion Tracker -> Mode`：选择 `Full-body`。
- `Num`：必须与实际绑定的 Motion Tracker 数量一致，常见配置是两只脚踝各一个，即 `2`；不要照抄示例图的数字。
- `Data & Control -> Send`：开启。
- `Switch w/ A Button`：与 SONIC 的 manager/录制按钮流程不是同一概念，不依赖它控制 LeRobot 数据保存。

PICO 与工作站应位于互通的局域网。PICO 的 `PC Service` 地址填写运行 XRoboToolkit PC Service 的
工作站 IP；出现 `WORKING` 后，SONIC 的 `pico_manager_thread_server.py` 才能读取追踪数据。

### 3.2 XRoboToolkit Remote Vision 视频桥接

仓库已经实现一个兼容 `XR-Robotics/XRoboToolkit-Orin-Video-Sender` 通信方式的桥接器，
不需要另外寻找或启动名为 `OrinVideoSender` 的二进制程序。

关键文件：

- `gear_sonic/scripts/run_xrobotoolkit_video_sender.py`：命令行入口。
- `gear_sonic/utils/teleop/xrobotoolkit_video_sender.py`：控制协议解析、SONIC 相机订阅、
  H.264 编码和 PICO 视频回传。
- `gear_sonic/tests/test_xrobotoolkit_video_sender.py`：协议和画面拼接单测。
- `gear_sonic/scripts/launch_data_collection.py`：通过 `--xr-camera-viewer` 自动创建
  `xr_camera` tmux window。

当前链路如下：

```text
MuJoCo head_camera / third_person_camera
    -> SONIC ZMQ camera publisher (TCP 5555)
    -> run_xrobotoolkit_video_sender.py
    -> 单画面或多相机 dashboard 合成
    -> H.264 side-by-side frame
    -> PICO XRoboToolkit decoder (TCP 12345)

PICO Listen
    -> OPEN_CAMERA command
    -> PC video sender control server (TCP 13579)
```

重要端口：

| 端口 | 方向 | 用途 |
|---|---|---|
| `5555` | MuJoCo/相机服务 -> 工作站进程 | SONIC JPEG/ndarray 相机 ZMQ 数据 |
| `13579` | PICO -> 工作站 | XRoboToolkit `OPEN_CAMERA`/`CLOSE_CAMERA` 控制连接 |
| `12345` | 工作站 -> PICO | H.264 视频回传到 PICO MediaDecoder |
| `5560` | data exporter -> XR sender | 录制状态和计时心跳 |

在 PICO 的 `Remote Vision` 中：

1. State 选择 `ZEDMINI`。当前桥接器只接受协议里的 `camera == "ZED"`；不要选择 `PICO4U`。
2. 点击 `Listen`。
3. 弹出 `Enter Camera Source IP` 时，填写运行
   `run_xrobotoolkit_video_sender.py` 的工作站局域网 IP，不是 PICO 自己的 IP。
4. 确保工作站允许 PICO 访问 TCP 13579，同时工作站能回连 PICO 的 TCP 12345。

例如之前截图中 PICO 是 `10.10.4.36`、PC Service 是 `10.10.4.85`，那么 Camera Source IP
应填写 `10.10.4.85`；这些只是当时网络里的示例，换网络后应重新查询。

`WORKING` 与 Remote Vision 是两条独立链路。因此即使显示 `WORKING`，仍可能看到：

```text
TCP connect error
TCP Client disconnected from server
```

这类报错通常表示 13579 上没有视频 sender、Camera Source IP 填错，或防火墙/网络阻止连接，
不能据此判断 PC Service 没有启动。

视频实现当前有这些边界：

- MuJoCo 同时发布 `ego_view`（头部第一人称）和 `third_person_view`（斜上方第三人称）。
- `--xr-camera-image-key` 决定主画面；当前瓶子投桶任务推荐使用 `third_person_view`。
- `--xr-camera-layout`/`--layout` 支持三种模式：
  - `single`：只显示主画面，兼容此前行为；
  - `dual_view`：主画面铺满视野，右上角叠加第一人称小窗；
  - `dashboard`：左侧为第三人称主画面，右侧依次显示第一人称、左腕和右腕画面。
- 多画面先合成为一个单目 dashboard，再复制给左右眼；它解决的是同时观察多个相机，
  不是把不同相机分别送给左右眼。
- 当前 MuJoCo 场景尚未配置腕部相机，因此 `dashboard` 中左右腕区域会显示灰色
  `NO SIGNAL` 占位。以后发布器提供 `left_wrist`、`right_wrist` 后会自动显示，无需修改 PICO。
- 当前场景第三人称相机固定在 `(1.8, -1.8, 1.7)`，持续朝向 G1 pelvis，垂直 FOV 为 `62°`。
- 选中的单目画面会复制到左右眼，属于单目 side-by-side，不是真正的双目深度画面。
- 当前发送 H.264；如果 PICO 请求 HEVC，桥接器会给出 warning，但仍按 H.264 实现工作。
- 编码器 `auto` 会先尝试 `h264_nvenc`，失败后回退 `libx264`。
- data exporter 会在 TCP `5560` 以 5 Hz 发布权威录制状态，XR sender 将状态叠加到每只眼睛的
  画面顶部中央：
  - 绿色 `READY EP n`：exporter 在线，尚未录制；
  - 红色闪烁 `REC mm:ss EP n`：正在录制，并显示本 episode 已录时长；
  - 橙色 `SAVING EPISODE...`：录制已停止，正在落盘；
  - 灰色 `RECORDER OFFLINE`：exporter 未启动、已退出，或超过 2 秒没有心跳。
- 该视觉提示不依赖语音，因此使用 `--no-text-to-speech` 时仍然有效；状态来自 exporter，
  不是简单跟随手柄按键，所以 exporter 启动失败时不会误显示 `REC`。
- 协议与画面布局单测已覆盖单画面、双画面、dashboard 和录制状态叠加。
- 尚未在文档记录一次“PICO 真机已经稳定看到画面”的最终验收结果；继续工作时应优先完成这项端到端确认。

单独启动视频桥接器的命令：

```bash
source .venv_data_collection/bin/activate
python gear_sonic/scripts/run_xrobotoolkit_video_sender.py \
  --camera-host localhost \
  --camera-port 5555 \
  --image-key third_person_view \
  --layout dashboard
```

通过统一启动器采集时，推荐使用：

```bash
python gear_sonic/scripts/launch_data_collection.py \
  --xr-camera-viewer \
  --xr-camera-backend xrobotoolkit \
  --xr-camera-image-key third_person_view \
  --xr-camera-layout dashboard
```

PICO 端仍然只需选择 `ZEDMINI` 并点击一次 `Listen`。这里传输的是一条已经在 PC 端
合成好的 H.264 视频流，所以不需要为四个相机分别建立四次 Remote Vision 连接。

预期关键日志：

```text
XRoboToolkit control server listening on 0.0.0.0:13579
PICO control connection from ...
Received XRoboToolkit command: OPEN_CAMERA
Opening PICO video stream ...:12345
Using H.264 encoder: ...
First H.264 frame sent to PICO
```

如果只有第一行而没有 `PICO control connection`，优先检查 PICO 填写的 Camera Source IP 和 13579。
如果收到 `OPEN_CAMERA`，但回连 12345 失败，则检查 PICO IP、PICO 是否仍停留在 Listen 状态和网络隔离策略。

### 3.3 桌面瓶子投入垃圾桶场景

场景文件：

```text
gear_sonic/utils/mujoco_sim/scenes/scene_43dof_bottle_to_bin.xml
```

场景内容：

- G1 43 DoF + Dex3 双手。
- 一张 `0.72 m` 高、桌面约 `0.68 x 0.56 m` 的木色桌子 `bottle_table`。
- 1 个蓝色轻量瓶子 `bottle_1`，约高 `0.21 m`、主体直径 `0.076 m`、质量 `0.12 kg`，
  使用 cylinder/capsule primitive 构成，适合 Dex3 单手抓握。
- 瓶子的全部碰撞面使用 `bottle_contact` 默认类模拟橡胶接触：
  `friction="2.5 0.08 0.005"`、`condim="6"`、`solref="0.025 2.0"`、
  `solimp="0.85 0.95 0.004 0.5 2"`。这不会让视觉 mesh 真正变形，但会提供更柔和的接触、
  更高的滑动摩擦，并启用扭转和滚动摩擦，降低从 Dex3 指间滑出的概率。
- 一个开放式方形垃圾桶 `trash_bin`，内部开口约 `0.40 x 0.40 m`、桶口高度约 `0.64 m`；
  桶底和四壁都有碰撞，瓶子可以真实落入并留在桶内。
- 垃圾桶使用绿色高亮桶沿，方便在头显和第三人称画面中快速识别投放目标。
- 桌子和垃圾桶是 `mocap` 运动学 body，复位时可随机移动，但不会被机器人撞翻；瓶子是正常
  free body，可以抓起、掉落和投入桶中。
- 原搬箱场景 `scene_43dof_tote_carry.xml` 仍保留，作为较高难度的历史任务，不再是当前推荐场景。

该场景已经验证：

- MuJoCo 可以成功加载，模型规模为 `nq=57, nv=55, nu=43`，初始状态无穿插接触。
- 初始 `head_camera` 能清楚看到瓶子，`third_person_camera` 能同时看到机器人、桌子和垃圾桶。
- 随机布局物理稳定性测试中，瓶子稳定留在桌面，没有滑落。
- 垃圾桶是开放碰撞结构，不是一个会挡住瓶子的实心 cylinder。
- 30 次从随机桶口内部上方释放瓶子的试验全部成功，瓶子最终都停留在桶内。

### 3.4 Backspace 随机复位

通用复位逻辑位于：

```text
gear_sonic/utils/mujoco_sim/base_sim.py
```

当前行为：

- MuJoCo viewer 获得焦点后按 `Backspace`，先执行 `mj_resetData()` 复位机器人和场景。
- viewer 的原始 GLFW 键盘回调会登记复位请求，由主仿真线程延迟约 20 ms 执行；这避免 MuJoCo
  内建的固定 Backspace 复位覆盖随机结果。弹力带原有的 `7/8/9` 键功能仍会被转发。
- 随后读取场景 XML 中名称为 `random_spawn_<freejoint name>`（矩形）或
  `random_spawn_annulus_<freejoint name>`（环形）的 custom numeric。
- `random_spawn_annulus_body_*` 随机运动学场景物体，`random_spawn_on_body_*` 再把动态物体
  放到指定支撑物体的局部坐标中。
- `spawn_avoid_body_*` 可设置最小安全距离；新增的 `spawn_distance_body_*` 同时设置最小和最大
  距离，避免两个任务物体既碰撞又离得过远。
- 对每个瓶子的 free joint 写入随机 x/y/yaw 和合法单位四元数，并清零 6 维速度。
- 最后调用 `mj_forward()` 更新运动学和接触数据。
- 如果一个场景没有声明 `random_spawn_*`，Backspace 就保持原来的固定复位行为。

当前瓶子投桶场景先随机垃圾桶和桌子，再将唯一的瓶子放到桌面可达区域：

```xml
<numeric name="random_spawn_annulus_body_trash_bin"
         data="0 0 0.90 1.15 0 -3.141593 3.141593"/>
<numeric name="random_spawn_annulus_body_bottle_table"
         data="0 0 0.65 0.85 0 -3.141593 3.141593"/>
<numeric name="spawn_distance_body_bottle_table_trash_bin" data="0.85 1.20"/>
<numeric name="random_spawn_on_body_bottle_1_free__bottle_table"
         data="-0.25 0.25 -0.19 0.19 0.728 -3.141593 3.141593"/>
```

环形配置的七个数字依次表示：

```text
center_x center_y radius_min radius_max z yaw_min yaw_max
```

因此当前范围为：

- 共同中心：机器人初始平面位置 `(0, 0)`，方位和物体 yaw 均覆盖 `360°`。
- 桌子到机器人中心：`0.65 ~ 0.85 m`。
- 垃圾桶到机器人中心：`0.90 ~ 1.15 m`。
- 桌子与垃圾桶中心距离：强制为 `0.85 ~ 1.20 m`，形成需要短距离行走但不会太远的任务。
- 每个瓶子相对桌面中心的局部范围：x=`-0.25~0.25 m`、y=`-0.19~0.19 m`。
- 瓶子始终只有一个，但每次 Backspace 都会重新采样它在桌面的局部 x/y 和 yaw。

桌子或垃圾桶若与机器人、彼此或其他外部物体接触，会拒绝该次采样并重试；瓶子允许接触支撑桌，
但与其他瓶子或外部物体接触时也会重采样。桌子和垃圾桶可能生成在机器人身后，操作者仍可能需要
转身寻找，但距离范围比原搬箱任务更紧凑。

注意以下语义区别：

- 键盘 Backspace 调用 `reset(randomize_spawns=True)`，会生成新任务位置。
- 跌倒检测触发的内部 `reset()` 仍是确定性复位，不会在 episode 中途悄悄更换任务布局。
- 仿真第一次启动仍使用 XML 固定位置，第一次按 Backspace 后才开始随机。
- 当前没有实现“episode 保存完成后自动按 Backspace”；仍需要操作者手动复位。

验证结果：

- 单瓶版本连续联合随机抽样 3000 次，没有采样失败。
- 桌子半径实测 `0.6500~0.8499 m`，垃圾桶半径实测 `0.9000~1.1500 m`。
- 桌桶中心距离实测 `0.8500~1.2000 m`。
- 瓶子相对桌面的局部位置实测：x=`-0.2499~0.2500 m`、y=`-0.1899~0.1899 m`。
- 20 组随机布局各步进 1000 次，瓶子掉落失败数为 0。
- 30 次随机桶位投放测试中，瓶子落入并留在桶内的失败数为 0。
- 橡胶接触参数又以 30 组随机布局各步进 1500 次验证：位置失败数为 0，最大残余速度为
  `0.014941`；20 次随机桶位投放失败数为 0。
- 初始第一人称和第三人称相机均可正常离屏渲染。
- 普通 `reset()` 仍能返回 XML 固定位置。

修改 Python 后必须重启 MuJoCo 进程，已经运行的进程不会热加载新逻辑。

## 4. 推荐的一键启动方式

从仓库根目录运行下面的命令。启动脚本会按需切换到各自的虚拟环境，并创建 tmux session
`sonic_data_collection`：

```bash
python gear_sonic/scripts/launch_data_collection.py \
  --sim \
  --sim-robot-scene gear_sonic/utils/mujoco_sim/scenes/scene_43dof_bottle_to_bin.xml \
  --task-prompt "pick up the bottle from the table and put it into the trash bin" \
  --dataset-name pico_bottle_to_bin \
  --pico-vis-vr3pt \
  --pico-vis-smpl \
  --xr-camera-viewer \
  --xr-camera-backend xrobotoolkit \
  --xr-camera-image-key third_person_view \
  --xr-camera-layout dashboard \
  --no-text-to-speech
```

启动器创建：

- `data_collection` window：C++ deploy、PICO teleop、data exporter、camera viewer 四个 pane。
- `sim` window：MuJoCo 仿真以及第一/第三人称相机的 ZMQ 发布。
- `xr_camera` window：XRoboToolkit Remote Vision 视频桥接器；上述命令以
  `third_person_view` 为主画面，并启用第一人称/双腕侧栏。删除 `--xr-camera-layout dashboard`
  会恢复默认单画面；当前没有腕部相机流时，双腕格显示 `NO SIGNAL` 属于预期行为。

启动后按终端提示切换到 C++ deploy pane 并按 Enter，等待部署完成。tmux 常用操作：

| 操作 | 按键/命令 |
|---|---|
| 鼠标选择 pane | 直接点击（启动器已打开 mouse support） |
| 切换 pane | `Ctrl+b`，再按方向键 |
| 切换 window | `Ctrl+b`，再按 `n`/`p` |
| detach | `Ctrl+b`，再按 `d` |
| reattach | `tmux attach -t sonic_data_collection` |
| 结束整个 session | `Ctrl+\` 或 `tmux kill-session -t sonic_data_collection` |

## 5. PICO 和录制操作顺序

完整操作应按以下顺序执行：

1. 确认 PICO XRoboToolkit `Network -> Status` 显示 `WORKING`。
2. 确认 tracker、头显和手柄追踪正常，人体可视化没有明显漂移或关节翻转。
3. 按 SONIC 文档要求完成 PICO 零位校准和 manager 模式切换：初次站在零位参考姿态，使用
   `A+B+X+Y` 初始化，再用 `A+X` 进入 POSE 模式。具体以
   `docs/source/tutorials/vr_wholebody_teleop.md` 的当前说明为准。
4. 如果使用 PICO Remote Vision，在 PICO 选择 `ZEDMINI`，点 `Listen`，填写工作站 IP。
5. 确认 MuJoCo viewer、PICO 人体映射和相机画面都正常。
6. `Left Grip + A`：开始录制一个 episode。
   PICO 画面应立即从绿色 `READY` 变为红色闪烁 `REC` 并开始计时；如果仍显示
   `RECORDER OFFLINE`，不要开始动作，应先检查 data exporter pane。
7. 拿起桌上的唯一瓶子，走到垃圾桶旁并将它投入桶内。
8. 再按一次 `Left Grip + A`：停止并保存。
9. 等 data exporter 明确输出 `Finished saving episode`。
10. 点击 MuJoCo viewer，按一次 `Backspace`，同时随机桌子、垃圾桶和瓶子位置。
11. 等瓶子和机器人稳定后，再按 `Left Grip + A` 开始下一条。

失败 episode 的处理：

- `Left Grip + B`：丢弃当前 episode；实现上仍会保存，但标记为后处理时移除。
- 键盘等价控制是 `c` 开始/结束、`x` 丢弃，但键盘命令依赖单独的 ZMQ keyboard publisher
  （默认端口 5580）。当前一键数据采集流程推荐直接使用 PICO 组合键。

不要在看到 `Finished saving episode` 之前按 Backspace，避免动作尾部与新场景复位的时间点混淆。
PICO App 自带的 `Data Collection -> Record` 不是当前 SONIC LeRobot exporter 的主要录制入口；
本项目以 `Left Grip + A` 通过 `manager_state` 控制 `run_data_exporter.py`。

数据默认保存到：

```text
outputs/<dataset-name>/
```

统一启动器默认把 `--dataset-name` 当作前缀，并追加启动时的本地时间：

```text
pico_bottle_to_bin_YYYYMMDD_HHMMSS
```

例如 `2026-08-01 15:30:45` 启动时，最终目录是
`outputs/pico_bottle_to_bin_20260801_153045/`。启动器开头会打印最终的 `Dataset name`。
如果确实需要继续写入一个已有的固定数据集，可以增加 `--no-append-dataset-timestamp`；此时显式传入
同一个 `--dataset-name` 才会继续向原目录添加 episode。切换任务定义、相机配置或随机分布时，不应
关闭时间后缀，以免把语义不同的数据混在一起。

## 6. 常见问题速查

### PICO 显示 WORKING，但 Listen 报 TCP connect error

`WORKING` 是追踪链路，不代表视频桥接器存在。检查：

```bash
ss -ltnp | grep 13579
```

并确认 `xr_camera` tmux window 没有退出、PICO 填写的是工作站 IP、局域网和防火墙允许 TCP 13579。

### 收到 OPEN_CAMERA，但视频回连失败

检查 sender 日志中的目标 IP 是否确实是 PICO IP，并确认 PICO 仍在 Listen 状态且 TCP 12345 可达。

### sender 一直显示 waiting for ego_view/third_person_view frames

确认 `sim` window 没有退出，启动命令包含 `--enable-image-publish --enable-offscreen`，并检查 5555：

```bash
ss -ltnp | grep 5555
```

### PICO 画面显示 RECORDER OFFLINE

这表示 XR 视频本身正常，但 data exporter 没有通过 TCP `5560` 发布心跳。检查：

```bash
ss -ltnp | grep 5560
tmux capture-pane -p -S -100 -t sonic_data_collection:data_collection.2
```

常见原因包括 exporter pane 已退出、数据集目录是不完整的旧目录、5560 被其他进程占用，或手动启动
sender/exporter 时两边使用了不同的 `--recording-status-port`。

### Backspace 后桌子、垃圾桶或瓶子没有随机

依次检查：

1. 是否重新启动过 MuJoCo。
2. MuJoCo viewer 是否获得键盘焦点。
3. 当前加载的是否确实是 `scene_43dof_bottle_to_bin.xml`。
4. XML 是否仍包含 `random_spawn_annulus_body_bottle_table`、
   `random_spawn_annulus_body_trash_bin` 和
   `random_spawn_on_body_bottle_1_free__bottle_table`。
5. 仿真终端是否输出 `Randomized 'bottle_1_free' reset pose: ...` 等日志。

### Backspace 后随机范围不合适

桌子范围由 `random_spawn_annulus_body_bottle_table` 控制，垃圾桶范围由
`random_spawn_annulus_body_trash_bin` 控制，瓶子在桌面的范围由各自的
`random_spawn_on_body_bottle_*__bottle_table` 控制。桌桶距离由
`spawn_distance_body_bottle_table_trash_bin` 控制；任意修改后应重新运行稳定性和碰撞验证。

### 想固定随机序列复现实验

代码支持配置项 `RESET_RANDOM_SEED`，但当前 `run_sim_loop.py`/launcher 还没有暴露对应 CLI 参数。
如有复现实验需求，下一步应把该配置正式接入 `SimLoopConfig` 和启动参数。

## 7. 已采集数据与首次 VLA 训练准备

2026-08-02 对四批瓶子投桶数据做了只读检查，并生成了一个非破坏性的合并训练集。原始目录均未修改：

```text
outputs/pico_bottle_to_bin_20260801_155748  12 episodes / 36773 frames
outputs/pico_bottle_to_bin_20260801_164827   4 episodes /  2567 frames
outputs/pico_bottle_to_bin_20260801_173110   8 episodes / 15677 frames
outputs/pico_bottle_to_bin_20260801_181151  15 episodes / 31712 frames
```

四批合计 39 个 episode，其中 14 个已在采集时通过丢弃操作写入
`discarded_episode_indices`。使用下面的命令合并时保留了 25 个有效 episode、64262 帧，约
21.4 分钟：

```bash
source .venv_data_collection/bin/activate
python gear_sonic/scripts/process_dataset.py \
  --dataset-path \
    outputs/pico_bottle_to_bin_20260801_155748 \
    outputs/pico_bottle_to_bin_20260801_164827 \
    outputs/pico_bottle_to_bin_20260801_173110 \
    outputs/pico_bottle_to_bin_20260801_181151 \
  --output-path outputs/pico_bottle_to_bin_merged_20260802 \
  --no-remove-stale-smpl
```

这里必须使用 `--no-remove-stale-smpl`：当前 VLA 训练动作是 `motion_token + 双手关节`，而
`PLANNER` 行走阶段的 `smpl_pose` 本来就可能为零。按 SMPL 零值清理会错误删除重要的行走帧。

合并集已经验证：

- 25 个 parquet、25 个 H.264 MP4 和 25 条 episode metadata 一一对应；每个视频帧数与 parquet
  行数一致。
- state、64 维 motion token 和双手动作均没有 NaN/Inf，视频为 `640x480@50fps`，抽样画面不是
  全灰或全黑。
- 四批数据的 features、`modality.json` 和 `script_config` 完全一致。
- 已用 Isaac-GR00T N1.7 的 `UNITREE_G1_SONIC` 配置生成 `meta/stats.json` 和
  `meta/relative_stats.json`，并成功通过官方 `LeRobotEpisodeLoader` 加载低维状态、动作和语言。
- `process_dataset.py` 已修复合并后 `total_videos` 沿用首个数据集旧值的问题；合并集现在正确记录为
  25。

当前合并集的统一训练提示词来自第一批数据：

```text
pick up every bottle from the table and put it into the trash bin
```

它对单瓶场景语义仍成立。部署时最好使用相同提示词，后续继续采集则建议统一改成单数版本，避免同一
任务出现不必要的语言差异。

### 7.1 本地网页人工审核

仓库提供了 Episode Review Studio，用于对已经保存为可用的数据再次人工复核：

```bash
source .venv_data_collection/bin/activate
python gear_sonic/scripts/run_dataset_review.py \
  --dataset-path outputs/pico_bottle_to_bin_merged_20260802
```

启动器会打开 `http://127.0.0.1:3000`。网页同步显示第一人称视频、仓库中真实
`g1_29dof_with_hand.urdf` + STL 网格、实际关节/控制指令曲线、stream mode 时间条和当前帧。
模型按数据里的 `joint_names` 驱动全部 43 个机身与手部关节，而不是使用固定下标猜测关节轴。支持：

- 从 episode 全时长均匀抽取 12 张视频帧形成总截图时间轴，点击截图即可同步跳转；
- 审核操作区位于总时间轴下方，便于先浏览全局、再保留/丢弃/裁切；
- 在操作区下方同时绘制全部 43 个关节的状态/指令轨道，可逐个隐藏或显示，也可全选/全不选；
- `K`：保留整个 episode；
- `D`：丢弃整个 episode；
- `I` / `O`：设置同步裁切的起点和终点；
- `Space`：播放/暂停；方向键跳转时间；
- 填写人工审核备注，并按状态筛选 episode。

审核结果会立即原子化保存到数据集的 `meta/review.jsonl`，不会改动原 parquet 或 MP4。全部审核后，
用同一个清单生成新的训练集：

```bash
source .venv_data_collection/bin/activate
python gear_sonic/scripts/process_dataset.py \
  --dataset-path outputs/pico_bottle_to_bin_merged_20260802 \
  --output-path outputs/pico_bottle_to_bin_reviewed \
  --review-file outputs/pico_bottle_to_bin_merged_20260802/meta/review.jsonl \
  --no-remove-stale-smpl
```

传入 review 文件时，未审核 episode 默认不进入输出；如确实需要保留可加 `--include-unreviewed`。
`trim` 会用相同帧索引裁剪 parquet 和所有视频，并重建 timestamp、frame index、episode index 和
全局 index。输出保留 `meta/source_review.jsonl` 作为审核记录。三维视图使用真实 G1 URDF 关节树和
原始 mesh，但当前 `observation.state` 只包含 43 个关节角，没有 floating-base 的世界位置和朝向，
因此网页中 pelvis 固定在展示原点；它仍不是完整的 MuJoCo 物理状态回放。

训练在云端的相邻仓库 Isaac-GR00T 中使用 GR00T N1.7 和
`UNITREE_G1_SONIC` 完成。训练使用的审核后数据目录为
`./data/pico_bottle_to_bin_reviewed`；权重已拉取到：

```text
/data/pateo/proj/robot/Isaac-GR00T/outputs/pico_bottle_to_bin-1
```

当前本地已用该权重启动 PolicyServer：

```bash
cd /data/pateo/proj/robot/Isaac-GR00T
uv run python gr00t/eval/run_gr00t_server.py \
  --model-path ./outputs/pico_bottle_to_bin-1 \
  --embodiment-tag UNITREE_G1_SONIC \
  --device cuda:0 \
  --port 5550
```

2026-08-04 已确认 `0.0.0.0:5550` 监听正常，PolicyClient `ping=True`；模型动作
horizon 为 40，与 SONIC 推理客户端默认值一致。当前工作站为单张 16GB RTX A4000，
PolicyServer 加载后约占 6.3GB 显存；微调仍应在 40GB 以上显存的云端 GPU 进行。

### 7.2 本地 VLA 推理环境安装故障与解决方案

2026-08-04 首次运行下面的标准安装命令失败：

```bash
bash install_scripts/install_inference.sh
```

故障不是网络或 CUDA 问题，而是 WholeBodyControl 的旧安装声明与当前
Isaac-GR00T 上游打包方式不再匹配。实际遇到了三层问题：

1. `gear_sonic/pyproject.toml` 原先把依赖名写成
   `Isaac-GR00T @ git+https://github.com/NVIDIA/Isaac-GR00T.git`，但当前上游
   `pyproject.toml` 的真实项目名是 `gr00t`。`uv` 因此报错：

   ```text
   Package metadata name `gr00t` does not match given name `isaac-gr00t`
   ```

2. 只把依赖名改为 `gr00t @ git+...` 仍不够。当前 Isaac-GR00T 对 ARM64 的
   `torchcodec` 使用了仓库内本地 wheel 引用。Isaac-GR00T 作为传递 Git 依赖时，
   `uv 0.10.12` 会在解析阶段直接拒绝该本地文件源，即使当前机器是 x86_64：

   ```text
   Git repository references local file source, but only directories are
   supported as transitive Git dependencies: ...torchcodec...linux_aarch64.whl
   ```

3. 旧 `install_inference.sh` 固定创建 Python 3.10 环境，而当前 Isaac-GR00T
   明确要求 Python `>=3.12,<3.13`。因此即使绕过前两个问题，Python 3.10 也无法完成安装。

最终修复如下：

- `install_inference.sh` 改用独立的 Python 3.12 `.venv_inference`；不修改仍在使用
  Python 3.10 的 teleop、MuJoCo 和 data-collection 环境。
- `gear_sonic` 的依赖名改为正确的 `gr00t`。
- 不再让 Isaac-GR00T 作为 Git 传递依赖解析，而是把已有的本地仓库
  `/data/pateo/proj/robot/Isaac-GR00T` 作为顶层 editable 项目安装。
- 安装脚本默认查找 `$REPO_ROOT/../Isaac-GR00T`；如果仓库在其他位置，使用
  `ISAAC_GROOT_PATH=/path/to/Isaac-GR00T bash install_scripts/install_inference.sh`。
- 为了仍在 `gear_sonic/pyproject.toml` 中保留可追溯的 Git 源，脚本通过
  `uv pip install --overrides` 把 `gr00t` 绝对覆盖为本地 `file://` URI，并把
  Isaac-GR00T 和 `gear_sonic[inference]` 同时作为顶层 editable 项目安装。

修复后安装解析了 155 个包。上游基础依赖会下载约 4.1GiB 的
`tensorrt-cu12-libs`，这是正常现象。如果 `uv` 缓存和项目不在同一文件系统，还会看到
`Failed to hardlink files; falling back to full copy`；它只表示需要复制文件，不是安装失败。
本次首次冷启动导入 `gr00t.policy` 时由于加载 Torch/Transformers 和大量共享库，在慢磁盘上
超过 120 秒；文件缓存预热后再次导入仅需约 2.1 秒，不应将首次长时间无输出误判为卡死。

最终验证结果：

```text
Python: 3.12.13
gr00t source: /data/pateo/proj/robot/Isaac-GR00T/gr00t
pinocchio import: OK
PolicyClient import: OK
PolicyServer ping: True
modality keys: action, language, state, video
```

环境修复后无需重启 PolicyServer。本地仿真推理启动器也已新增
`--sim-robot-scene`，并检查它必须与 `--sim` 同时使用且场景文件存在。当前推荐命令为：

```bash
python gear_sonic/scripts/launch_inference.py \
  --sim \
  --sim-robot-scene gear_sonic/utils/mujoco_sim/scenes/scene_43dof_bottle_to_bin.xml \
  --policy-host localhost \
  --policy-port 5550 \
  --embodiment-tag unitree_g1_sonic \
  --prompt "pick up every bottle from the table and put it into the trash bin" \
  --action-horizon 40 \
  --no-data-exporter
```

## 8. 当前未完成与建议下一步

按优先级排列：

1. 在 PICO 真机完成 Remote Vision 端到端验收，并记录实际 FPS、延迟、编码器和最终成功日志。
2. 用当前瓶子投桶场景完整采集若干 episode，检查抓取难度、瓶子是否容易滑落，以及导出数据中的
   视频、机器人状态、SMPL 和时间戳是否同步。
3. 对随机范围做人工可达性验收；如果边缘位置太难，先收紧范围，再按课程式逐渐扩大。
4. 考虑实现“保存 episode 成功后自动通知 MuJoCo 随机复位”。当前仍是保存后手动 Backspace，
   这样更可控，但连续大规模采集时操作较繁琐。
5. 增加确定性复位快捷键，例如 `Shift+Backspace` 或单独的键；当前只有 Backspace 随机复位，
   内部 `reset()` 可固定复位但没有专门的 viewer 快捷键。
6. 为随机复位补正式 pytest。当前做过 5000 次脚本验证，但尚未加入仓库测试文件。
7. 确认后把 `base_sim.py`、瓶子投桶 XML、XR 多画面修改和本说明文档提交；提交前注意不要误带
   用户自己的 `run.sh`。
8. 当前已经支持机器人周围 360° 环形采样、物体距离约束和接触拒绝。后续如果扩大到更远的地面范围，
   还应加入路径可达性和相机可见性检查，可能也要让机器人初始朝向共同随机化。

## 9. 新对话接手提示

可以在新对话中直接发送：

```text
请先完整阅读 docs/note/README.md，并运行 git status --short 核对当前工作区。
在保留已有未提交修改的前提下，继续处理“PICO + MuJoCo 瓶子投桶数据采集”任务。
```

相关正式文档：

- `docs/source/getting_started/vr_teleop_setup.md`
- `docs/source/tutorials/vr_wholebody_teleop.md`
- `docs/source/tutorials/data_collection.md`

本页记录的是当前分支上的专项开发状态；如果它与正式文档冲突，应先核对实际代码和 Git 历史，
再决定更新哪一处。
