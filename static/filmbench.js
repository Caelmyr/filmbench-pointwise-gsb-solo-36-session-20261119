"use strict";

const byId = id => document.getElementById(id);
const all = selector => Array.from(document.querySelectorAll(selector));
const stage = byId("stage");
const plate = byId("plate");
const overlay = byId("overlay");
const tools = all("[data-tool]");
const modes = all("[data-mode]");
const toneDefaults = {black: .04, white: 1.25, exposure: 0, contrast: 1, red: 0, green: 0, blue: 0};
const viewLabels = {
  positive: "当前正片", split: "原片与正片同位对照", source: "完整扫描原片",
  density: "当前画格相对密度", clipping: "完整扫描的通道截断标注",
  sprocket: "完整扫描上这一排齿孔的破口标注"
};
let active;
let metrics;
let sprocketReport = null;
let mode = "positive";
let tool = null;
let firstClone = null;
let selection = null;
let probe = null;
let busy = false;
let viewVersion = 0;

async function json(url, options) {
  const response = await fetch(url, options);
  const report = await response.json();
  if (!response.ok) throw new Error(report.error || "操作失败");
  return report;
}

function message(text = "", error = false) {
  const status = byId("status");
  status.textContent = text;
  status.classList.toggle("error", error);
}

function route(project = active) {
  return "/film/projects/" + project.id;
}

async function listProjects() {
  const data = await json("/film/projects");
  byId("projects").replaceChildren(...data.projects.map(project => {
    const button = document.createElement("button");
    button.className = "project";
    button.dataset.project = project.id;
    button.textContent = project.title;
    button.title = project.origin + " · " + project.title;
    button.setAttribute("aria-current", String(active?.id === project.id));
    button.addEventListener("click", () => loadProject(() => json(route(project))));
    return button;
  }));
  byId("demos").replaceChildren(...data.demos.map(demo => {
    const button = document.createElement("button");
    button.className = "demo";
    const description = document.createElement("small");
    description.textContent = demo.description;
    button.append(demo.title, description);
    button.addEventListener("click", () => openDemo(demo.id));
    return button;
  }));
}

async function loadProject(action, refresh = false) {
  if (busy) return false;
  busy = true;
  message("正在读取扫描…");
  try {
    const project = await action();
    activate(project);
    if (refresh) await listProjects();
    else updateCurrentProject();
    message("工程已打开。");
    return true;
  } catch (error) {
    message(error.message, true);
    return false;
  } finally {
    busy = false;
  }
}

function updateCurrentProject() {
  const projects = all(".project");
  for (const button of projects) {
    button.setAttribute("aria-current", String(button.dataset.project === active.id));
  }
}

function activate(project) {
  active = project;
  metrics = null;
  sprocketReport = null;
  drawHistogram();
  drawSprocketReport();
  mode = "positive";
  probe = null;
  setTool(null);
  fillProject();
  renderProject();
}

function fillProject() {
  const {settings, title, origin, size, frame_size: frameSize, depth, scan_format: format, version} = active;
  byId("empty").hidden = true;
  for (const id of ["plate", "modeBar", "toolBar", "revision", "details", "settings"]) byId(id).hidden = false;
  byId("projectTitle").textContent = title;
  byId("projectMeta").textContent = [origin, format, depth + " 位", size.join(" × ") + " px"].join("  ·  ");
  byId("revision").textContent = "修订 " + version;
  byId("frameReading").textContent = frameSize.join(" × ") + " px · 顺时针 " + settings.turn * 90 + "°";
  byId("encoding").value = settings.encoding;
  for (const [name, value] of Object.entries(settings.tone)) byId("toneForm").elements[name].value = value;
  byId("mirror").setAttribute("aria-pressed", String(settings.mirror));
  byId("sprocketSide").value = settings.sprocket.side;
  byId("resetSprocket").disabled = !settings.sprocket.region;
  byId("sprocketReading").textContent = settings.sprocket.region
    ? "正在检查这一排齿孔…"
    : "先框出一段完整的齿孔作参照。";
  byId("resetCrop").disabled = !settings.crop;
  byId("resetRetouch").disabled = !settings.retouch;
  byId("resetBase").disabled = !settings.base_region;
  for (const link of all("[data-output]")) link.href = route() + "/export/" + link.dataset.output;
}

function renderProject() {
  drawView();
  updateProbe();
  const render = ++viewVersion;
  byId("baseReading").textContent = "正在读取片基…";
  json(route() + "/metrics").then(report => {
    if (render !== viewVersion) return;
    metrics = report;
    const {base, codes} = report;
    const source = base.mode === "selection" ? "手动取样" : "从片边估计";
    const clipped = base.clipped_fraction > .05 ? " · 选区含截断像素" : "";
    byId("baseReading").textContent = source + " · " + base.transmission.map(v => v.toFixed(3)).join(" / ") + clipped;
    byId("baseReading").title = "线性 RGB 透射率 · " + base.count.toLocaleString() + " 个样本";
    modes.find(btn => btn.dataset.mode === "clipping").title =
      "零值 " + codes.low.toLocaleString() + " px · 满量程 " + codes.high.toLocaleString() + " px · 双重 " + codes.both.toLocaleString() + " px";
    drawHistogram();
  }).catch(error => {
    if (render === viewVersion) message(error.message, true);
  });
  if (active.settings.sprocket.region) {
    json(route() + "/sprocket").then(report => {
      if (render !== viewVersion) return;
      sprocketReport = report;
      drawSprocketReport();
      drawView();
    }).catch(error => {
      if (render !== viewVersion) return;
      sprocketReport = {ready: false, message: error.message, damage: [], notes: [], clipped: {count: 0}};
      drawSprocketReport();
      if (mode === "sprocket") message(error.message, true);
    });
  } else {
    sprocketReport = null;
    drawSprocketReport();
  }
}

function drawSprocketReport() {
  const panel = byId("sprocketReport");
  const report = sprocketReport;
  byId("resetSprocket").disabled = !active?.settings.sprocket.region;
  if (!report || !report.ready) {
    panel.hidden = true;
    panel.replaceChildren();
    byId("sprocketReading").textContent = report
      ? report.message : "先框出一段完整的齿孔作参照。";
    return;
  }
  panel.hidden = false;
  const {reference: ref, damage} = report;
  byId("sprocketReading").textContent =
    "参照 " + ref.holes + " 个完整孔 · " + (ref.side === "top" ? "上边" : "下边")
    + " · " + (damage.length ? damage.length + " 处破口" : "没有破口");
  byId("sprocketReading").title = report.notes.join("\n");
  const children = [];
  if (!damage.length) {
    const clean = document.createElement("p");
    clean.className = "sprocket-head";
    clean.textContent = "这一排没有破口";
    children.push(clean);
  }
  for (const item of damage) {
    const row = document.createElement("button");
    row.className = "sprocket-break";
    row.type = "button";
    const tag = document.createElement("b");
    tag.textContent = item.index;
    const text = document.createElement("span");
    text.textContent = item.kind === "merged"
      ? "连孔破口 · 原片 x " + item.bounds[0] + "–" + item.bounds[2]
      : "单孔撕开 · 原片 x " + item.bounds[0] + "–" + item.bounds[2];
    row.append(tag, text);
    row.title = item.reason + " · 宽 " + item.width + "、高 " + item.height + " 像素";
    row.addEventListener("click", () => focusSprocket(item.bounds));
    children.push(row);
  }
  const notes = document.createElement("p");
  notes.className = "sprocket-notes";
  notes.textContent = report.notes.join(" ");
  children.push(notes);
  panel.replaceChildren(...children);
}

function focusSprocket(bounds) {
  if (!active) return;
  selection = {start: [bounds[0], bounds[1]], end: [bounds[2], bounds[3]]};
  if (mode !== "sprocket") {
    mode = "sprocket";
    drawView();
  } else drawOverlay();
  const index = (sprocketReport?.damage || []).findIndex(
    item => item.bounds[0] === bounds[0] && item.bounds[1] === bounds[1]);
  message(index >= 0 ? "已在原片上标出第 " + (index + 1) + " 处破口。" : "已在原片上标出破口。");
}

function fullScan() {
  return mode === "source" || mode === "clipping" || mode === "sprocket";
}

function drawView() {
  if (!active) return;
  for (const button of modes) button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  const compare = mode === "split";
  const url = route() + "/preview/";
  const revision = "?v=" + active.version;
  const view = compare ? "positive" : mode;
  const image = byId("viewImage");
  image.alt = viewLabels[mode];
  image.src = url + view + ".png" + revision;
  byId("compareImage").hidden = !compare;
  byId("compareControl").hidden = !compare;
  byId("splitLine").hidden = !compare;
  if (compare) byId("compareImage").src = url + "reference.png" + revision;
  updateSplit();
  sizePlate();
  drawOverlay();
}

function sizePlate() {
  if (!active) return;
  const [width, height] = fullScan() ? active.size : active.frame_size;
  const scale = Math.max(.01, Math.min((stage.clientWidth - 20) / width, (stage.clientHeight - 20) / height, 2));
  plate.style.width = width * scale + "px";
  plate.style.height = height * scale + "px";
}

function updateSplit() {
  const fraction = Number(byId("compareSlider").value);
  byId("compareImage").style.clipPath = "inset(0 " + (100 - fraction) + "% 0 0)";
  byId("splitLine").style.left = "calc(" + fraction + "% - 1px)";
}

function drawHistogram() {
  const canvas = byId("histogram");
  const ctx = canvas.getContext("2d");
  const {width, height} = canvas;
  ctx.clearRect(0, 0, width, height);
  if (!metrics) return;
  const rows = byId("histMode").value === "source" ? metrics.source_histogram : metrics.positive_histogram;
  const peak = Math.max(1, ...rows.flat());
  const colors = ["#efa998", "#8fc68a", "#92b3ec"];
  for (let channel = 0; channel < rows.length; channel++) {
    ctx.strokeStyle = colors[channel];
    ctx.lineWidth = 2;
    ctx.beginPath();
    rows[channel].forEach((amount, index) => {
      const x = index * width / (rows[channel].length - 1);
      const y = height - 3 - (height - 8) * Math.sqrt(amount / peak);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
}

function setMode(next) {
  if (tool) setTool(null);
  if (next === "sprocket" && !active.settings.sprocket.region) {
    message("先在完整原片上框一段完整齿孔作参照。", true);
    return;
  }
  mode = next;
  drawView();
  if (next === "clipping") message("蓝色表示有通道读到零值，橙红表示有通道达到满量程，紫色表示同点同时存在两类截断。");
  else if (next === "density") message("由当前片基测算的相对密度。显示与下载统一映射 0 至 1.6 密度单位。");
  else if (next === "sprocket") message(sprocketReport?.ready
    ? "按整排拟合的孔距和孔形检查这一侧片边，绿色框是参照，红色框是破口。"
    : "正在检查这一排齿孔…");
  else message();
}

function setTool(next) {
  tool = next;
  selection = null;
  firstClone = null;
  plate.classList.toggle("interactive", Boolean(tool));
  for (const button of tools) button.setAttribute("aria-pressed", String(button.dataset.tool === tool));
  byId("cancelTool").hidden = !tool;
  byId("retouchSize").hidden = tool !== "retouch";
  if (tool) {
    mode = "source";
    drawView();
    const hints = {
      base: "在完整原片上拖框，选一块没有画面的透光片边。",
      crop: "从任意一角拖到对角选出画格。此框记录在完整原片上。",
      sprocket: "在完整原片上沿一排齿孔拖框，框住几个完整孔和周围片边。",
      retouch: "依次点选一块干净的来源和要修补的目标。",
      probe: "点击完整原片中的位置，读取原始码值和对应的正片像素。"
    };
    message(hints[tool]);
  } else if (active) {
    drawOverlay();
    message();
  }
}

function drawOverlay() {
  overlay.replaceChildren();
  if (!active || !fullScan()) return;
  overlay.setAttribute("viewBox", "0 0 " + active.size.join(" "));
  if (mode === "clipping") return;
  const {base_region: baseRegion, crop, retouch} = active.settings;
  function shape(tag, attrs) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    node.setAttribute("vector-effect", "non-scaling-stroke");
    overlay.append(node);
  }
  function box(region, stroke, dashed) {
    const [x, y, right, bottom] = region;
    shape("rect", {x, y, width: right - x, height: bottom - y,
      fill: "none", stroke, "stroke-width": 1.5, "stroke-dasharray": dashed ? "5 4" : "none"});
  }
  function marker(coords, color, radius = 6) {
    shape("circle", {cx: coords[0] + .5, cy: coords[1] + .5, r: radius,
      fill: "none", stroke: color, "stroke-width": 1.5});
  }
  if (baseRegion) box(baseRegion, "#91d6af", false);
  if (crop) box(crop, "#f9d79d", true);
  const sprocket = active.settings.sprocket;
  if (sprocket.region) box(sprocket.region, "#91d6af", false);
  if (mode === "sprocket" && sprocketReport?.ready) {
    for (const item of sprocketReport.damage) box(item.bounds, "#ff6b4a", false);
  }
  if (tool === "retouch" && retouch) {
    marker(retouch.source, "#91d6af", retouch.radius);
    marker(retouch.target, "#f9ba9f", retouch.radius);
  }
  if (firstClone) marker(firstClone, "#91d6af");
  if (probe && tool === "probe") marker(probe, "#ebf6f3");
  if (selection) box(regionBetween(selection.start, selection.end), "#e0f1da", false);
}

function imagePoint(event) {
  const box = plate.getBoundingClientRect();
  const [width, height] = active.size;
  const x = Math.floor((event.clientX - box.left) / box.width * width);
  const y = Math.floor((event.clientY - box.top) / box.height * height);
  return [Math.max(0, Math.min(width - 1, x)), Math.max(0, Math.min(height - 1, y))];
}

function regionBetween(a, b) {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]) + 1, Math.max(a[1], b[1]) + 1];
}

function startSelection(event) {
  if (!tool || !active || busy || event.button !== 0) return;
  const coords = imagePoint(event);
  if (tool === "probe") {
    probe = coords;
    drawOverlay();
    updateProbe();
  } else if (tool === "retouch") {
    if (!firstClone) {
      firstClone = coords;
      message("修补来源已选。请点选要覆盖的目标。");
      drawOverlay();
    } else {
      const change = {source: firstClone, target: coords, radius: Number(byId("radius").value)};
      saveSettings({retouch: change}, "已应用点修补。", "positive");
    }
  } else {
    selection = {start: coords, end: coords};
    plate.setPointerCapture(event.pointerId);
    drawOverlay();
  }
}

function moveSelection(event) {
  if (!selection) return;
  selection.end = imagePoint(event);
  drawOverlay();
}

function finishSelection(event) {
  if (!selection) return;
  const region = regionBetween(selection.start, imagePoint(event));
  selection = null;
  drawOverlay();
  if (tool === "base") saveSettings({base_region: region}, "已从所选的透光片边取样。");
  if (tool === "crop") saveSettings({crop: region}, "已按原片坐标裁切画格。", "positive");
  if (tool === "sprocket") {
    if (region[2] - region[0] < 12 || region[3] - region[1] < 12) {
      message("参照框太小，请框住几个完整孔和周围片边。", true);
      return;
    }
    saveSettings({sprocket: {...active.settings.sprocket, region}}, "已按这段齿孔检查整排。", "sprocket");
  }
}

async function updateProbe() {
  const loupe = byId("loupe");
  const values = byId("probeValues");
  if (!probe) {
    loupe.hidden = true;
    values.textContent = "用像素探针点选完整原片，查看对应码值、正片和局部纹理。";
    return;
  }
  const point = probe;
  const saved = active;
  const url = route() + "/probe?x=" + point[0] + "&y=" + point[1];
  try {
    const result = await json(url);
    if (probe !== point || active !== saved) return;
    loupe.src = route() + "/loupe.png?x=" + point[0] + "&y=" + point[1] + "&v=" + active.version;
    loupe.hidden = false;
    values.textContent = "(" + result.point.join(", ") + ") · 原始 " + result.codes.join(" / ")
      + " · 正片 " + result.positive.join(" / ") + (result.clipped_channels.some(Boolean) ? " · 含截断" : "");
  } catch (error) {
    if (probe === point && active === saved) message(error.message, true);
  }
}

async function saveSettings(changes, notice, nextMode) {
  if (!active || busy) return;
  busy = true;
  try {
    active = await json(route(), {method: "PATCH", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({version: active.version, changes})});
    if (nextMode) {
      setTool(null);
      mode = nextMode;
    } else if (changes.base_region !== undefined) setTool(null);
    else if (changes.sprocket) setTool(null);
    fillProject();
    renderProject();
    message(notice);
  } catch (error) {
    message(error.message, true);
  } finally {
    busy = false;
  }
}

function openDemo(key) {
  return loadProject(() => json("/film/demos/" + key, {method: "POST"}), true);
}

byId("loadHarbor").addEventListener("click", () => openDemo("harbor"));
for (const button of modes) button.addEventListener("click", () => setMode(button.dataset.mode));
for (const button of tools) button.addEventListener("click", () => setTool(tool === button.dataset.tool ? null : button.dataset.tool));
byId("sampleBase").addEventListener("click", () => setTool("base"));
byId("cancelTool").addEventListener("click", () => setTool(null));
byId("compareSlider").addEventListener("input", updateSplit);
byId("histMode").addEventListener("change", drawHistogram);
byId("encoding").addEventListener("change", async event => {
  await saveSettings({encoding: event.target.value}, "已重新解释扫描码值。");
  event.target.value = active.settings.encoding;
});
byId("rotate").addEventListener("click", () => saveSettings({turn: (active.settings.turn + 1) % 4}, "已旋转画格。", "positive"));
byId("mirror").addEventListener("click", () => saveSettings({mirror: !active.settings.mirror}, "已更新镜像设置。", "positive"));
byId("resetBase").addEventListener("click", () => saveSettings({base_region: null}, "已恢复片边估计。"));
byId("sampleSprocket").addEventListener("click", () => setTool("sprocket"));
byId("resetSprocket").addEventListener("click", () => {
  if (mode === "sprocket") mode = "source";
  saveSettings({sprocket: {...active.settings.sprocket, region: null}}, "已清除齿孔参照。", "source");
});
byId("sprocketSide").addEventListener("change", async event => {
  await saveSettings({sprocket: {...active.settings.sprocket, side: event.target.value}},
    "已切换齿孔所在片边。");
  event.target.value = active.settings.sprocket.side;
});
byId("resetTone").addEventListener("click", () => saveSettings({tone: toneDefaults}, "已恢复默认显影。", "positive"));
byId("resetCrop").addEventListener("click", () => saveSettings({crop: null}, "已使用完整画格。", "positive"));
byId("resetRetouch").addEventListener("click", () => saveSettings({retouch: null}, "已移除点修补。", "positive"));
byId("toneForm").addEventListener("submit", event => {
  event.preventDefault();
  const tone = Object.fromEntries(Array.from(new FormData(event.currentTarget), ([key, value]) => [key, Number(value)]));
  saveSettings({tone}, "已应用显影设置。", "positive");
});
plate.addEventListener("pointerdown", startSelection);
plate.addEventListener("pointermove", moveSelection);
plate.addEventListener("pointerup", finishSelection);
plate.addEventListener("pointercancel", () => { selection = null; drawOverlay(); });
byId("viewImage").addEventListener("error", () => {
  if (active) message("未能生成当前预览，请尝试切换视图。", true);
});
new ResizeObserver(sizePlate).observe(stage);

const dialog = byId("importDialog");
byId("openImport").addEventListener("click", () => {
  byId("importError").textContent = "";
  dialog.showModal();
});
byId("closeImport").addEventListener("click", () => dialog.close());
byId("importForm").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  byId("importError").textContent = "";
  const loaded = await loadProject(() => json("/film/import", {method: "POST", body: new FormData(form)}), true);
  if (loaded) {
    dialog.close();
    form.reset();
  } else byId("importError").textContent = byId("status").textContent;
});
byId("openArchive").addEventListener("click", () => byId("archiveFile").click());
byId("archiveFile").addEventListener("change", async event => {
  const input = event.currentTarget;
  const archive = input.files[0];
  if (!archive) return;
  const form = new FormData();
  form.append("archive", archive);
  await loadProject(() => json("/film/restore", {method: "POST", body: form}), true);
  input.value = "";
});
listProjects().catch(error => message(error.message, true));
