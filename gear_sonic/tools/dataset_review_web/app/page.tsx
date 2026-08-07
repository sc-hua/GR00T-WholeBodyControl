import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import RobotViewer from "./components/RobotViewer";

const API = import.meta.env.VITE_REVIEW_API_URL ?? "http://127.0.0.1:8765";

type ReviewStatus = "unreviewed" | "keep" | "discard" | "trim";

type Episode = {
  episode_index: number;
  length: number;
  duration: number;
  tasks: string[];
  status: ReviewStatus;
  trim_start: number | null;
  trim_end: number | null;
  notes: string;
  updated_at?: string;
  collection_discarded: boolean;
};

type DatasetSummary = {
  dataset_name: string;
  dataset_path: string;
  review_file: string;
  fps: number;
  total_episodes: number;
  total_frames: number;
  counts: Record<ReviewStatus, number>;
  episodes: Episode[];
};

type MotionData = {
  episode_index: number;
  length: number;
  fps: number;
  joint_names: string[];
  timestamps: number[];
  state: number[][];
  command: number[][];
  stream_mode: number[];
  token_norm: number[];
};

const statusCopy: Record<ReviewStatus, string> = {
  unreviewed: "待审核",
  keep: "保留",
  discard: "丢弃",
  trim: "已裁切",
};

const reviewStatuses: ReviewStatus[] = ["unreviewed", "keep", "trim", "discard"];

const modeCopy: Record<number, string> = {
  0: "OFF",
  1: "POSE",
  2: "PLANNER",
  3: "FROZEN UPPER",
  4: "POSE PAUSE",
  5: "VR 3PT",
};

const jointGroups = [
  { label: "腿部", matches: (name: string) => /_(hip|knee|ankle)_/.test(name) },
  { label: "腰部", matches: (name: string) => name.startsWith("waist_") },
  { label: "左臂", matches: (name: string) => /^left_(shoulder|elbow|wrist)_/.test(name) },
  { label: "左手", matches: (name: string) => name.startsWith("left_hand_") },
  { label: "右臂", matches: (name: string) => /^right_(shoulder|elbow|wrist)_/.test(name) },
  { label: "右手", matches: (name: string) => name.startsWith("right_hand_") },
];

// Shared geometry for the multi-joint timeline and its sticky time ruler.
const JOINT_LABEL_WIDTH = 220;
const JOINT_ROW_HEIGHT = 44;

function formatTime(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return "--:--.--";
  const min = Math.floor(seconds / 60);
  return `${String(min).padStart(2, "0")}:${(seconds % 60).toFixed(2).padStart(5, "0")}`;
}

function formatUpdatedAt(value: string | undefined) {
  if (!value) return "未审核";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).replaceAll("/", "-");
}

type FilmstripFrame = { time: number; image: string };

function VideoFilmstrip({
  src,
  duration,
  currentTime,
  trimStart,
  trimEnd,
  onSeek,
}: {
  src: string;
  duration: number;
  currentTime: number;
  trimStart: number | null;
  trimEnd: number | null;
  onSeek: (time: number) => void;
}) {
  const [frames, setFrames] = useState<FilmstripFrame[]>([]);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const video = document.createElement("video");
    video.crossOrigin = "anonymous";
    video.muted = true;
    video.preload = "auto";

    const waitFor = (eventName: "loadeddata" | "seeked") =>
      new Promise<void>((resolve, reject) => {
        const done = () => {
          video.removeEventListener(eventName, done);
          video.removeEventListener("error", fail);
          resolve();
        };
        const fail = () => {
          video.removeEventListener(eventName, done);
          video.removeEventListener("error", fail);
          reject(new Error("视频截图解码失败"));
        };
        video.addEventListener(eventName, done, { once: true });
        video.addEventListener("error", fail, { once: true });
      });

    const generate = async () => {
      try {
        const loaded = waitFor("loadeddata");
        video.src = src;
        video.load();
        await loaded;
        const count = 12;
        const nextFrames: FilmstripFrame[] = [];
        const canvas = document.createElement("canvas");
        canvas.width = 200;
        canvas.height = 150;
        const context = canvas.getContext("2d");
        if (!context) throw new Error("浏览器不支持截图画布");

        for (let index = 0; index < count && !cancelled; index += 1) {
          const time = (index / (count - 1)) * Math.max(0, duration - 0.04);
          if (Math.abs(video.currentTime - time) > 0.02) {
            const sought = waitFor("seeked");
            video.currentTime = time;
            await sought;
          }
          context.drawImage(video, 0, 0, canvas.width, canvas.height);
          nextFrames.push({ time, image: canvas.toDataURL("image/jpeg", 0.72) });
          if (!cancelled) setFrames([...nextFrames]);
        }
      } catch {
        if (!cancelled) setFailed(true);
      }
    };

    generate();
    return () => {
      cancelled = true;
      video.pause();
      video.removeAttribute("src");
      video.load();
    };
  }, [duration, src]);

  const dragging = useRef(false);
  const seekFromClientX = (element: HTMLDivElement, clientX: number) => {
    const rect = element.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    onSeek(ratio * duration);
  };

  const percentage = duration > 0 ? Math.min(100, Math.max(0, (currentTime / duration) * 100)) : 0;

  return (
    <div
      className="filmstrip-track"
      onPointerDown={(event) => {
        dragging.current = true;
        event.currentTarget.setPointerCapture(event.pointerId);
        seekFromClientX(event.currentTarget, event.clientX);
      }}
      onPointerMove={(event) => {
        if (dragging.current) seekFromClientX(event.currentTarget, event.clientX);
      }}
      onPointerUp={(event) => {
        dragging.current = false;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
          event.currentTarget.releasePointerCapture(event.pointerId);
        }
      }}
      onPointerCancel={() => {
        dragging.current = false;
      }}
      aria-label="视频截图总时间轴，点击或拖动跳转"
    >
      <div className="filmstrip-frames">
        {frames.map((frame) => (
          <div className="filmstrip-frame" key={frame.time}>
            {/* Generated locally from the dataset video, not a remote image. */}
            <img src={frame.image} alt={`${formatTime(frame.time)} 视频帧`} draggable={false} />
            <span>{formatTime(frame.time)}</span>
          </div>
        ))}
        {!failed && frames.length < 12 && Array.from({ length: 12 - frames.length }).map((_, index) => (
          <div className="filmstrip-placeholder" key={`placeholder-${index}`}><i /></div>
        ))}
        {failed && <div className="filmstrip-error">无法生成截图，请确认视频可正常播放</div>}
      </div>
      {trimStart != null && <i className="filmstrip-mask start" style={{ width: `${trimStart / duration * 100}%` }} />}
      {trimEnd != null && <i className="filmstrip-mask end" style={{ width: `${100 - trimEnd / duration * 100}%` }} />}
      <i className="filmstrip-cursor" style={{ left: `${percentage}%` }}><span>{formatTime(currentTime)}</span></i>
    </div>
  );
}

function MultiJointTimeline({
  motion,
  currentFrame,
  visibleJoints,
  trimStart,
  trimEnd,
  onSeek,
}: {
  motion: MotionData;
  currentFrame: number;
  visibleJoints: number[];
  trimStart: number | null;
  trimEnd: number | null;
  onSeek: (time: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rowHeight = JOINT_ROW_HEIGHT;
  const headerHeight = 0;
  const labelWidth = JOINT_LABEL_WIDTH;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const draw = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio, 2);
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.scale(dpr, dpr);
      const width = rect.width;
      const height = rect.height;
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#111918";
      ctx.fillRect(0, 0, width, height);

      const total = Math.max(1, motion.length - 1);
      const duration = motion.length / motion.fps;
      const plotWidth = Math.max(1, width - labelWidth);
      const step = Math.max(1, Math.floor(motion.length / Math.max(1, plotWidth)));

      ctx.fillStyle = "#0d1413";
      ctx.fillRect(0, 0, labelWidth, height);
      ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.textBaseline = "middle";
      // Vertical time gridlines; numeric labels live in the sticky ruler above.
      for (let tick = 0; tick <= 4; tick += 1) {
        const x = labelWidth + (tick / 4) * plotWidth;
        ctx.strokeStyle = "rgba(74, 92, 87, .22)";
        ctx.beginPath();
        ctx.moveTo(x + 0.5, 0);
        ctx.lineTo(x + 0.5, height);
        ctx.stroke();
      }

      visibleJoints.forEach((jointIndex, row) => {
        const rowTop = headerHeight + row * rowHeight;
        const plotTop = rowTop + 5;
        const plotHeight = rowHeight - 10;
        ctx.fillStyle = row % 2 === 0 ? "#121a19" : "#101716";
        ctx.fillRect(labelWidth, rowTop, plotWidth, rowHeight);
        ctx.strokeStyle = "#263330";
        ctx.beginPath();
        ctx.moveTo(0, rowTop + rowHeight - 0.5);
        ctx.lineTo(width, rowTop + rowHeight - 0.5);
        ctx.stroke();

        let min = Infinity;
        let max = -Infinity;
        for (let frame = 0; frame < motion.length; frame += 1) {
          min = Math.min(min, motion.state[frame][jointIndex] ?? 0, motion.command[frame][jointIndex] ?? 0);
          max = Math.max(max, motion.state[frame][jointIndex] ?? 0, motion.command[frame][jointIndex] ?? 0);
        }
        const span = Math.max(0.001, max - min);
        const drawLine = (source: number[][], color: string, lineWidth: number) => {
          ctx.beginPath();
          ctx.strokeStyle = color;
          ctx.lineWidth = lineWidth;
          for (let frame = 0; frame < motion.length; frame += step) {
            const x = labelWidth + (frame / total) * plotWidth;
            const value = source[frame][jointIndex] ?? 0;
            const y = plotTop + (1 - (value - min) / span) * plotHeight;
            if (frame === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          }
          ctx.stroke();
        };
        drawLine(motion.command, "rgba(234, 182, 115, .72)", 1);
        drawLine(motion.state, "#72d6c7", 1.4);

        const name = motion.joint_names[jointIndex] ?? `joint_${jointIndex}`;
        ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace";
        ctx.fillStyle = "#c6d2ce";
        ctx.fillText(name.replace("_joint", ""), 12, rowTop + rowHeight / 2 - 7);
        ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
        const actual = motion.state[currentFrame]?.[jointIndex] ?? 0;
        const command = motion.command[currentFrame]?.[jointIndex] ?? 0;
        ctx.fillStyle = "#7ce0d1";
        ctx.fillText(`实 ${actual.toFixed(2)}`, 12, rowTop + rowHeight / 2 + 9);
        ctx.fillStyle = "#f0bd7e";
        ctx.fillText(`令 ${command.toFixed(2)}`, 110, rowTop + rowHeight / 2 + 9);
      });

      if (trimStart != null) {
        const x = labelWidth + Math.min(plotWidth, (trimStart / duration) * plotWidth);
        ctx.fillStyle = "rgba(5, 8, 8, .62)";
        ctx.fillRect(labelWidth, headerHeight, x - labelWidth, height - headerHeight);
        ctx.strokeStyle = "#6fd9c7";
        ctx.strokeRect(x, headerHeight, 1, height - headerHeight);
      }
      if (trimEnd != null) {
        const x = labelWidth + Math.max(0, (trimEnd / duration) * plotWidth);
        ctx.fillStyle = "rgba(5, 8, 8, .62)";
        ctx.fillRect(x, headerHeight, width - x, height - headerHeight);
        ctx.strokeStyle = "#eab673";
        ctx.strokeRect(x, headerHeight, 1, height - headerHeight);
      }

      const cursor = labelWidth + (Math.min(currentFrame, total) / total) * plotWidth;
      ctx.strokeStyle = "#f3f0e8";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cursor + 0.5, 0);
      ctx.lineTo(cursor + 0.5, height);
      ctx.stroke();
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [currentFrame, labelWidth, motion, rowHeight, trimEnd, trimStart, visibleJoints]);

  if (visibleJoints.length === 0) {
    return <div className="joint-empty">当前未显示任何关节，请在上方选择关节或点击“全选”。</div>;
  }

  return (
    <canvas
      ref={canvasRef}
      className="multi-joint-canvas"
      style={{ height: `${headerHeight + visibleJoints.length * rowHeight}px` }}
      onPointerDown={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        const x = event.clientX - rect.left;
        const y = event.clientY - rect.top;
        if (x < labelWidth && y >= headerHeight) {
          return;
        }
        if (x >= labelWidth) onSeek(((x - labelWidth) / (rect.width - labelWidth)) * (motion.length / motion.fps));
      }}
      aria-label="全部已选关节动作时间轴，点击曲线跳转，点击名称选择关节"
    />
  );
}

export default function Home() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [dataset, setDataset] = useState<DatasetSummary | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [motion, setMotion] = useState<MotionData | null>(null);
  const [currentFrame, setCurrentFrame] = useState(0);
  const [visibleJoints, setVisibleJoints] = useState<Set<number>>(new Set());
  const [visibleStatuses, setVisibleStatuses] = useState<Set<ReviewStatus>>(
    () => new Set(reviewStatuses),
  );
  const [query, setQuery] = useState("");
  const [mediaCompact, setMediaCompact] = useState(false);
  const [busy, setBusy] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const selected = dataset?.episodes.find((episode) => episode.episode_index === selectedIndex) ?? null;

  const refresh = useCallback(async () => {
    const response = await fetch(`${API}/api/dataset`);
    if (!response.ok) throw new Error((await response.json()).error ?? "读取数据集失败");
    const next: DatasetSummary = await response.json();
    setDataset(next);
    return next;
  }, []);

  const reloadDataset = useCallback(async () => {
    setRefreshing(true);
    setError("");
    try {
      const response = await fetch(`${API}/api/dataset/refresh`, { method: "POST" });
      if (!response.ok) throw new Error((await response.json()).error ?? "刷新数据集失败");
      const next: DatasetSummary = await response.json();
      setDataset(next);
      setSelectedIndex((current) => {
        if (next.episodes.some((episode) => episode.episode_index === current)) return current;
        return (next.episodes.find((episode) => episode.status === "unreviewed") ?? next.episodes[0]).episode_index;
      });
    } catch (reason) {
      setError(String(reason));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    // Initial data loading intentionally hydrates the client-side review state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh()
      .then((next) => {
        const first = next.episodes.find((episode) => episode.status === "unreviewed") ?? next.episodes[0];
        setSelectedIndex(first.episode_index);
      })
      .catch((reason) => setError(String(reason)))
      .finally(() => setBusy(false));
  }, [refresh]);

  useEffect(() => {
    if (!dataset) return;
    const controller = new AbortController();
    // Reset the synchronized views while the newly selected episode is loading.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMotion(null);
    setCurrentFrame(0);
    fetch(`${API}/api/episodes/${selectedIndex}/motion`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json()).error ?? "动作数据加载失败");
        return response.json();
      })
      .then((nextMotion: MotionData) => {
        setMotion(nextMotion);
        setVisibleJoints(new Set(nextMotion.joint_names.map((_, index) => index)));
      })
      .catch((reason) => {
        if (reason.name !== "AbortError") setError(String(reason));
      });
    return () => controller.abort();
  }, [dataset, selectedIndex]);

  const updateReview = useCallback(
    async (patch: Partial<Episode> & { status?: ReviewStatus }) => {
      if (!selected) return;
      setError("");
      const payload = {
        status: patch.status ?? selected.status,
        trim_start: patch.trim_start !== undefined ? patch.trim_start : selected.trim_start,
        trim_end: patch.trim_end !== undefined ? patch.trim_end : selected.trim_end,
        notes: patch.notes !== undefined ? patch.notes : selected.notes,
      };
      const response = await fetch(`${API}/api/episodes/${selectedIndex}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const body = await response.json();
        setError(body.error ?? "保存审核结果失败");
        return;
      }
      await refresh();
    },
    [refresh, selected, selectedIndex],
  );

  const seek = useCallback((seconds: number) => {
    if (!videoRef.current) return;
    videoRef.current.currentTime = Math.max(0, Math.min(videoRef.current.duration || seconds, seconds));
  }, []);

  const setInPoint = useCallback(() => {
    const time = videoRef.current?.currentTime ?? 0;
    updateReview({ trim_start: Number(time.toFixed(3)) });
  }, [updateReview]);

  const setOutPoint = useCallback(() => {
    const time = videoRef.current?.currentTime ?? selected?.duration ?? 0;
    const start = selected?.trim_start;
    updateReview({
      trim_end: Number(time.toFixed(3)),
      status: start != null && start < time ? "trim" : selected?.status,
    });
  }, [selected, updateReview]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.code === "Space") {
        event.preventDefault();
        const video = videoRef.current;
        if (video?.paused) video.play();
        else video?.pause();
      } else if (event.key.toLowerCase() === "k") updateReview({ status: "keep" });
      else if (event.key.toLowerCase() === "d") updateReview({ status: "discard" });
      else if (event.key.toLowerCase() === "i") setInPoint();
      else if (event.key.toLowerCase() === "o") setOutPoint();
      else if (event.key === "ArrowLeft") seek((videoRef.current?.currentTime ?? 0) - (event.shiftKey ? 5 : 1));
      else if (event.key === "ArrowRight") seek((videoRef.current?.currentTime ?? 0) + (event.shiftKey ? 5 : 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [seek, setInPoint, setOutPoint, updateReview]);

  const visibleEpisodes = useMemo(() => {
    if (!dataset) return [];
    return dataset.episodes.filter((episode) => {
      const statusMatch = visibleStatuses.has(episode.status);
      const queryMatch = !query || String(episode.episode_index).includes(query) || episode.notes.toLowerCase().includes(query.toLowerCase());
      return statusMatch && queryMatch;
    });
  }, [dataset, query, visibleStatuses]);

  const toggleStatusFilter = useCallback((status: ReviewStatus) => {
    setVisibleStatuses((current) => {
      const next = new Set(current);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      return next;
    });
  }, []);

  const toggleJoint = useCallback((jointIndex: number) => {
    setVisibleJoints((current) => {
      const next = new Set(current);
      if (next.has(jointIndex)) next.delete(jointIndex);
      else next.add(jointIndex);
      return next;
    });
  }, []);

  const joints = motion?.state[Math.min(currentFrame, (motion?.length ?? 1) - 1)] ?? [];
  const progress = dataset
    ? Math.round(((dataset.counts.keep + dataset.counts.discard + dataset.counts.trim) / dataset.total_episodes) * 100)
    : 0;

  if (busy) return <main className="center-state">正在读取数据集…</main>;
  if (!dataset || !selected) return <main className="center-state error">{error || "没有可审核的 episode"}</main>;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <div>
            <h1>Episode Review Studio</h1>
            <p>{dataset.dataset_name}</p>
          </div>
          <button type="button" onClick={reloadDataset} disabled={refreshing} title="读取新采集完成的 episodes">
            {refreshing ? "刷新中…" : "刷新数据"}
          </button>
        </div>
        <div className="review-progress">
          <div className="progress-copy"><span>审核进度</span><strong>{progress}%</strong></div>
          <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
          <small>{dataset.total_episodes - dataset.counts.unreviewed} / {dataset.total_episodes} episodes</small>
        </div>
        <div className="top-stats">
          <span>保留 <strong>{dataset.counts.keep}</strong></span>
          <span>裁切 <strong>{dataset.counts.trim}</strong></span>
          <span>丢弃 <strong>{dataset.counts.discard}</strong></span>
        </div>
      </header>

      <aside className="episode-rail">
        <div className="rail-heading">
          <div><span>数据队列</span><strong>{visibleEpisodes.length}</strong></div>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索编号/备注" aria-label="搜索 episode" />
          <fieldset className="status-filter" aria-label="按审核状态多选筛选">
            <button
              type="button"
              className={visibleStatuses.size === reviewStatuses.length ? "selected" : ""}
              aria-pressed={visibleStatuses.size === reviewStatuses.length}
              onClick={() => setVisibleStatuses(new Set(reviewStatuses))}
            >全部</button>
            {reviewStatuses.map((status) => (
              <button
                type="button"
                key={status}
                className={visibleStatuses.has(status) ? `selected ${status}` : ""}
                aria-pressed={visibleStatuses.has(status)}
                onClick={() => toggleStatusFilter(status)}
              >{statusCopy[status]}</button>
            ))}
          </fieldset>
        </div>
        <div className="episode-list">
          {visibleEpisodes.map((episode) => (
            <button
              key={episode.episode_index}
              className={`episode-card ${episode.episode_index === selectedIndex ? "active" : ""}`}
              onClick={() => setSelectedIndex(episode.episode_index)}
            >
              <div className="episode-id"><span>EP</span>{String(episode.episode_index).padStart(3, "0")}</div>
              <div className="episode-meta">
                <strong>{formatTime(episode.duration)}</strong>
                <small>{episode.length.toLocaleString()} 帧</small>
              </div>
              <span className={`status-pill ${episode.status}`}>
                {episode.status === "discard" && episode.collection_discarded ? "采集丢弃" : statusCopy[episode.status]}
              </span>
              <small className="episode-updated" title="审核更新时间">{formatUpdatedAt(episode.updated_at)}</small>
              {episode.trim_start != null && episode.trim_end != null && (
                <div className="mini-range"><i style={{ left: `${episode.trim_start / episode.duration * 100}%`, right: `${100 - episode.trim_end / episode.duration * 100}%` }} /></div>
              )}
            </button>
          ))}
        </div>
      </aside>

      <section
        className={`workspace ${mediaCompact ? "media-is-compact" : ""}`}
        onScroll={(event) => {
          const workspace = event.currentTarget;
          setMediaCompact((current) => (
            current
              ? workspace.scrollTop >= workspace.clientHeight * 0.16
              : workspace.scrollTop >= workspace.clientHeight * 0.26
          ));
        }}
      >
        <div className={`media-row ${mediaCompact ? "compact" : ""}`}>
          <div className="video-panel panel">
          <div className="panel-title">
            <div><h2>第一人称画面</h2><span className="section-meta">Episode {String(selectedIndex).padStart(3, "0")}</span></div>
            <div className="timecode"><strong>{formatTime(currentFrame / dataset.fps)}</strong><span>/ {formatTime(selected.duration)}</span></div>
          </div>
          <div className="video-stage">
            <video
              ref={videoRef}
              key={selectedIndex}
              src={`${API}/api/episodes/${selectedIndex}/video`}
              controls
              preload="metadata"
              onTimeUpdate={(event) => setCurrentFrame(Math.min(selected.length - 1, Math.floor(event.currentTarget.currentTime * dataset.fps)))}
            />
            <div className="mode-badge">{modeCopy[motion?.stream_mode[currentFrame] ?? 0] ?? "UNKNOWN"}</div>
          </div>
          </div>

          <div className="robot-panel panel">
          <div className="panel-title">
            <div><h2>G1 姿态</h2><span className="section-meta">第 {currentFrame.toLocaleString()} 帧</span></div>
          </div>
          <div className="robot-stage">
            <RobotViewer
              joints={joints}
              jointNames={motion?.joint_names ?? []}
              modelUrl={`${API}/api/g1-model/g1_29dof_with_hand.urdf`}
            />
            <div className="robot-hint">拖动旋转 · 滚轮缩放 · 右键平移</div>
          </div>
          </div>
        </div>

        <div className="review-panel panel" aria-label="Episode 审核决策操作区">
          <div className="review-context">
            <span>Episode {String(selectedIndex).padStart(3, "0")}</span>
            <strong className={selected.status}>{statusCopy[selected.status]}</strong>
          </div>
          <div className="review-actions">
            <button className={`review-button keep ${selected.status === "keep" ? "selected" : ""}`} onClick={() => updateReview({ status: "keep" })}><span>保留整条</span><kbd>K</kbd></button>
            <button className={`review-button discard ${selected.status === "discard" ? "selected" : ""}`} onClick={() => updateReview({ status: "discard" })}><span>丢弃整条</span><kbd>D</kbd></button>
          </div>
          <div className="trim-controls">
            <button onClick={setInPoint}><span>起点 <kbd>I</kbd></span><strong>{formatTime(selected.trim_start)}</strong></button>
            <div className="range-arrow">—</div>
            <button onClick={setOutPoint}><span>终点 <kbd>O</kbd></span><strong>{formatTime(selected.trim_end)}</strong></button>
            <button
              className="apply-trim"
              disabled={selected.trim_start == null || selected.trim_end == null}
              onClick={() => updateReview({ status: "trim" })}
            >应用裁切</button>
          </div>
          <label className="notes-field">
            <span>备注</span>
            <input
              key={`${selectedIndex}-${selected.notes}`}
              defaultValue={selected.notes}
              placeholder="记录异常或裁切原因"
              onBlur={(event) => updateReview({ notes: event.target.value })}
            />
          </label>
          <div className="autosave"><i />已保存</div>
        </div>

        <div className="filmstrip-panel panel">
          <div className="timeline-heading">
            <div><h2>截图时间轴</h2><span className="section-meta">点击或拖动跳转</span></div>
          </div>
          <VideoFilmstrip
            key={selectedIndex}
            src={`${API}/api/episodes/${selectedIndex}/video`}
            duration={selected.duration}
            currentTime={currentFrame / dataset.fps}
            trimStart={selected.trim_start}
            trimEnd={selected.trim_end}
            onSeek={seek}
          />
        </div>

        <details className="joint-timelines-panel panel" open>
          <summary className="joint-timeline-heading">
            <div>
              <h2>关节曲线</h2>
              <span className="section-meta">高级检查 · 当前显示 {visibleJoints.size} / {motion?.joint_names.length ?? 0}</span>
            </div>
            <div className="joint-summary-actions">
              <div className="legend"><span><i className="actual" />实际</span><span><i className="command" />指令</span></div>
              <span className="expand-label">展开</span>
            </div>
          </summary>
          <div className="joint-details">
          <div className="joint-visibility-toolbar">
            <strong>选择曲线</strong>
            <div className="joint-bulk-actions">
              <button onClick={() => setVisibleJoints(new Set(motion?.joint_names.map((_, index) => index) ?? []))}>全选</button>
              <button onClick={() => setVisibleJoints(new Set())}>全不选</button>
            </div>
          </div>
          <div className="joint-group-shortcuts" aria-label="按身体部位选择关节">
            <span>按部位</span>
            {jointGroups.map((group) => {
              const indices = motion?.joint_names
                .map((name, index) => group.matches(name) ? index : -1)
                .filter((index) => index >= 0) ?? [];
              const selectedCount = indices.filter((index) => visibleJoints.has(index)).length;
              return (
                <button
                  key={group.label}
                  className={selectedCount === indices.length && indices.length > 0 ? "selected" : ""}
                  onClick={() => setVisibleJoints((current) => {
                    const next = new Set(current);
                    const shouldSelect = !indices.every((index) => current.has(index));
                    indices.forEach((index) => shouldSelect ? next.add(index) : next.delete(index));
                    return next;
                  })}
                >
                  {group.label}<small>{selectedCount}/{indices.length}</small>
                </button>
              );
            })}
          </div>
          <div className="joint-picker" role="group" aria-label="选择要显示的关节时间线">
            {motion?.joint_names.map((name, index) => (
              <button
                key={name}
                className={visibleJoints.has(index) ? "selected" : ""}
                onClick={() => toggleJoint(index)}
                aria-pressed={visibleJoints.has(index)}
              >
                <i />{name.replace("_joint", "")}
              </button>
            ))}
          </div>
          <div className="joint-timeline-scroll">
            {motion ? (
              <>
                <div className="joint-ruler">
                  <div className="joint-ruler-label" style={{ width: JOINT_LABEL_WIDTH }}>关节 · 实际/指令</div>
                  <div className="joint-ruler-track">
                    {[0, 1, 2, 3, 4].map((tick) => {
                      const style = tick === 4 ? { right: 4 } : { left: `${(tick / 4) * 100}%` };
                      return <span key={tick} style={style}>{formatTime((tick / 4) * (motion.length / motion.fps))}</span>;
                    })}
                    <i
                      className="ruler-cursor"
                      style={{ left: `${motion.length > 1 ? Math.min(100, (currentFrame / (motion.length - 1)) * 100) : 0}%` }}
                    />
                  </div>
                </div>
                <MultiJointTimeline
                  motion={motion}
                  currentFrame={currentFrame}
                  visibleJoints={[...visibleJoints].sort((a, b) => a - b)}
                  trimStart={selected.trim_start}
                  trimEnd={selected.trim_end}
                  onSeek={seek}
                />
              </>
            ) : <div className="timeline-loading">动作数据加载中…</div>}
          </div>
          </div>
        </details>
      </section>

      {error && <div className="error-toast" role="alert">{error}<button onClick={() => setError("")}>×</button></div>}
    </main>
  );
}
