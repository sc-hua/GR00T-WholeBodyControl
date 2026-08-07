import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("builds the episode review application shell", async () => {
  const html = await readFile(new URL("../dist/index.html", import.meta.url), "utf8");
  assert.match(html, /<title>Episode Review Studio<\/title>/i);
  assert.match(html, /<div id="root"><\/div>/i);
  assert.match(html, /<script[^>]+src="\/assets\/[^"']+\.js"/i);
});

test("keeps review interactions and local API wiring in the client", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const robotViewer = await readFile(new URL("../app/components/RobotViewer.tsx", import.meta.url), "utf8");
  assert.match(page, /api\/episodes\/\$\{selectedIndex\}\/review/);
  assert.match(page, /api\/dataset\/refresh/);
  assert.match(page, /刷新数据/);
  assert.match(page, /formatUpdatedAt/);
  assert.match(page, /审核更新时间/);
  assert.match(page, /toggleStatusFilter/);
  assert.match(page, /按审核状态多选筛选/);
  assert.match(page, /collection_discarded/);
  assert.match(page, /采集丢弃/);
  assert.match(page, /RobotViewer/);
  assert.match(page, /VideoFilmstrip/);
  assert.match(page, /MultiJointTimeline/);
  assert.match(page, /setVisibleJoints/);
  assert.match(page, /全不选/);
  assert.match(page, /setInPoint/);
  assert.match(page, /setOutPoint/);
  assert.match(page, /VITE_REVIEW_API_URL/);
  assert.match(robotViewer, /new THREE\.LoadingManager\(\)/);
  assert.match(robotViewer, /loader\.loadMeshCb/);
  assert.match(robotViewer, /meshesSettled !== meshRequests/);
  assert.match(robotViewer, /controls\.fitToSphere/);
  assert.match(robotViewer, /controls\.reset\(true\)/);
});
