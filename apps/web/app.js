const element = selector => document.querySelector(selector);
const apiInput = element("#api-url");
// API 可直接提供页面；独立 4173 预览仍连接默认后台端口。
if (window.location && ["http:", "https:"].includes(window.location.protocol) && window.location.port !== "4173") apiInput.value = window.location.origin;
const theme = element("#theme");
const connectionPill = element(".connection-pill");
const pages = {
  state: ["电池总览", "OVERVIEW", "从整包状态，到每一个模组。"],
  voltage: ["单体电压", "CELL VOLTAGES", "每个模组独立展开，每一个单体都有据可查。"],
  temperature: ["单体温度", "TEMPERATURES", "关注模组温度与通道状态。"],
  relay: ["继电器与充电", "POWER SYSTEM", "整包通断与充电状态。"],
  threshold: ["保护阈值", "PROTECTION", "查看告警与保护参数。"],
  can: ["原始报文", "CAN EXPLORER", "从显示值追溯到原始字节。"],
  catalog: ["A03 字段目录", "SIGNAL DIRECTORY", "从结构体字段，追溯数值与接收证据。"],
  sessions: ["记录与趋势", "SESSION LIBRARY", "让每一次采集，都成为可以重新阅读的记录。"],
  diagnostics: ["诊断与分析", "DIAGNOSTICS", "把异常、判断与证据放在一起。"]
};
const qualityLabels = {valid: "有效", pec_error: "PEC 错误", timeout: "超时", stale: "旧值", partial: "部分更新", range_error: "超出范围", disconnected: "断线 · 保留旧值", unknown: "有效性待确认", reserved: "保留槽"};

// 原始快照保留为证据；连接状态与手动显示选择单独保存。
let activeSnapshot = emptySnapshot();
let replayRecords = [];
let replayIndex = 0;
let replayName = "";
let replaySession = null;
let replayOffset = 0;
let replayNextOffset = null;
let sourceMode = "latest";
let apiStatus = "loading";
let failureMessage = "";
let requestVersion = 0;
let listVersion = 0;
let overrideTime = null;
let selectedCellIndex = null;
let viewIntent = "latest";
const pendingSnapshots = new Set();
const putText = (selector, text) => { const node = element(selector); if (node) node.textContent = text; };
const apiBase = () => apiInput.value.trim().replace(/\/$/, "");
const valueText = value => value == null ? "—" : Number(value).toLocaleString("zh-CN");
const timeText = timestamp => Number.isFinite(timestamp) ? new Date(timestamp * 1000).toLocaleString("zh-CN", {hour12: false}) : "—";
const validValue = value => value?.valid === true && value.quality === "valid" && Number.isFinite(value.value);
const physicalCells = snapshot => snapshot.cells.filter(cell => cell.physicalValid !== false);
const sequenceLabel = snapshot => snapshot.sequenceScope === "host_rx" ? "主机接收序号" : "记录序号";
const sourceDescriptions = {
  simulation: {tag: "SIMULATION", label: "模拟数据，未连接真实 CAN", note: "正在使用模拟数据。质量状态不等于真实硬件验收。"},
  can: {tag: "CAN · RX", label: "CAN 接收数据", note: "来自 CAN 只读接收。主机接收时间与序号不代表固件采样时间或批次；未提供有效性证据的读数标记待确认。"},
  file: {tag: "FILE · OFFLINE", label: "离线文件记录，非实时 CAN", note: "正在查看离线文件记录，非实时 CAN。时间与序号追溯文件输入，不代表设备当前状态。"},
  unknown: {tag: "WAITING", label: "数据来源未提供", note: "等待 API 提供数据来源；尚未收到的测点显示为空。"}
};
const sourceDescription = snapshot => sourceDescriptions[snapshot?.dataSource] || sourceDescriptions.unknown;
const frameKey = frame => `${frame.bus}:${frame.id}:${frame.timestamp}:${frame.sequence}`;
const hasReadings = snapshot => [snapshot.totalVoltage, snapshot.current, ...snapshot.cells, ...Object.values(snapshot.packStatus || {})].some(item => Number.isFinite(item?.value));
function retainedFrames(snapshot) {
  const current = new Set(snapshot.frames.map(frameKey));
  return (snapshot.evidenceFrames || []).filter(frame => !current.has(frameKey(frame)));
}
function emptySnapshot() {
  const missing = unit => ({value: null, unit, valid: false, quality: "unknown", timestamp: null, source: "unavailable", rawFrameIds: []});
  return {id: "", timestamp: null, firmwareVersion: "unknown", dataSource: "unknown", connectionState: "disconnected",
    totalVoltage: missing("mV"), current: missing("A"), packStatus: {}, frames: [], evidenceFrames: [], diagnostics: [],
    cells: Array.from({length: 144}, (_, index) => ({...missing("mV"), cellIndex: index, moduleIndex: Math.floor(index / 18), channelIndex: index % 18, physicalValid: index % 18 !== 17})),
    temperatures: Array.from({length: 48}, () => missing("℃")),
    modelDetection: {model: "unknown", mode: "automatic", reason: "等待数据与识别证据", confidence: "low"}};
}

function addText(parent, tag, text) {
  const node = document.createElement(tag); node.textContent = text; parent.append(node); return node;
}
function selectPage(page) {
  if (!pages[page]) return;
  document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.page === page));
  document.querySelectorAll(".page").forEach(item => item.classList.remove("active"));
  element(`#page-${page}`).classList.add("active");
  putText("#page-title", pages[page][0]);
  addText(element("#page-title"), "span", ".").className = "heading-dot";
  putText("#page-kicker", pages[page][1]); putText("#page-description", pages[page][2]);
}
function markSelectedCell() {
  document.querySelectorAll(".cell").forEach(node => node.setAttribute("aria-pressed", selectedCellIndex !== null && Number(node.dataset.cellIndex) === selectedCellIndex));
}
function renderInspector() {
  if (selectedCellIndex === null || !activeSnapshot) return;
  const target = element("#cell-inspector"); target.replaceChildren();
  const group = window.BmsModules.groupCells(activeSnapshot.cells).find(item => item.cells.some(cell => cell.cellIndex === selectedCellIndex));
  if (!group) { addText(target, "p", "当前记录未包含所选槽位，请选择另一个单体。").className = "inspector-empty"; return; }
  const cell = group.cells.find(item => item.cellIndex === selectedCellIndex);
  const reserved = cell.physicalValid === false;
  const connected = apiStatus === "connected" && activeSnapshot.connectionState !== "disconnected";
  const quality = reserved ? "reserved" : !connected ? "disconnected" : validValue(cell) && Number.isFinite(cell.value) ? "valid" : cell.quality === "valid" ? "unknown" : cell.quality;
  const channel = Number.isInteger(cell.channelIndex) ? `C${String(cell.channelIndex + 1).padStart(2, "0")}` : `槽 ${cell.cellIndex + 1}`;
  addText(target, "p", `${group.label} / ${channel}`).className = "selected-cell-title";
  const value = addText(target, "div", valueText(cell.value)); value.className = "selected-cell-value";
  addText(value, "small", cell.unit || "mV");
  const badge = addText(target, "span", qualityLabels[quality] || "待确认"); badge.className = "selected-cell-quality"; badge.dataset.quality = quality;
  const facts = document.createElement("dl"); facts.className = "inspector-facts";
  const frames = (cell.rawFrameIds || []).map(id => `0x${id.toString(16).toUpperCase()}`).join(" · ") || "未提供";
  for (const [label, text] of [["软件槽位", `${cell.cellIndex + 1} · ${reserved ? "非物理电芯" : "物理电芯"}`], ["原始质量", cell.quality || "unknown"], [activeSnapshot.sequenceScope === "host_rx" ? "主机接收时间" : "记录时间", cell.value == null ? "尚未收到" : timeText(cell.timestamp)], [sequenceLabel(activeSnapshot), cell.sequence ?? "—"], ["数据来源", cell.source || "未提供"], ["原始帧引用", frames]]) {
    const row = document.createElement("div"); addText(row, "dt", label); addText(row, "dd", text); facts.append(row);
  }
  target.append(facts);
  const link = addText(target, "button", "查看原始帧证据 ↗"); link.className = "text-button inspector-link"; link.type = "button";
  link.addEventListener("click", () => { selectPage("can"); element("#page-can").scrollIntoView?.({block: "start"}); });
}
function displayDetection(snapshot) {
  const original = snapshot.modelDetection || {model: "unknown", mode: "automatic", reason: "未提供识别证据"};
  const override = element("#model-override").value;
  return override ? {...original, model: override, mode: "manual", reason: `界面手动选择 ${override.toUpperCase()} · ${timeText(overrideTime)}；服务端识别仍为 ${original.model.toUpperCase()}（${original.reason}）`} : original;
}
function snapshotQuality(snapshot) {
  if (apiStatus !== "connected" || snapshot.connectionState === "disconnected") return "disconnected";
  const issues = [snapshot.totalVoltage, snapshot.current, ...physicalCells(snapshot)].filter(value => !validValue(value)).map(value => value?.quality === "valid" ? "unknown" : value?.quality || "unknown");
  issues.push(...packMeasurements(snapshot).filter(item => item.data).map(item => measurementQuality(item, true)).filter(quality => quality !== "valid"));
  return ["disconnected", "timeout", "pec_error", "range_error", "stale", "partial", "unknown"].find(quality => issues.includes(quality)) || "valid";
}
function packMeasurements(snapshot) {
  const pack = snapshot.packStatus || {};
  // 历史 totalVoltage 是 mV 槽位和，只用于累压；不能替代独立 ADC 测量。
  const accumulated = pack.totalVoltageAccumulated === undefined && snapshot.totalVoltage ? {
    ...snapshot.totalVoltage, value: Number.isFinite(snapshot.totalVoltage.value) ? snapshot.totalVoltage.value / 1000 : null, unit: "V"
  } : pack.totalVoltageAccumulated;
  return [
    {id: "pack-voltage", data: pack.totalVoltageAdc, unit: "V", source: "独立总压采样"},
    {id: "total-voltage", data: accumulated, unit: "V", source: pack.totalVoltageAccumulated === undefined ? "槽位计算" : "单体电压累加"},
    {id: "precharge-voltage", data: pack.prechargeVoltage, unit: "V", source: "负载侧独立采样"},
    {id: "current", data: pack.current === undefined ? {...snapshot.current, unit: "A"} : pack.current, unit: "A", source: "放电＋ / 充电−"},
    {id: "soc", data: pack.soc, unit: "%", source: "固件 SOC 估计"}
  ];
}
function measurementQuality(item, connected) {
  if (!item.data) return "unavailable";
  if (!connected) return "disconnected";
  if (item.data.unit !== item.unit) return "unknown";
  if (validValue(item.data)) return "valid";
  return item.data.quality === "valid" ? "unknown" : item.data.quality || "unknown";
}
function renderPackMeasurements(snapshot, connected) {
  for (const item of packMeasurements(snapshot)) {
    const {id, data, unit} = item; const quality = measurementQuality(item, connected);
    const numeric = data?.unit === unit && Number.isFinite(data?.value);
    const number = numeric ? data.value.toLocaleString("zh-CN", {maximumFractionDigits: 3}) : "—";
    putText(`#${id}`, numeric ? `${number} ${unit}` : "—");
    putText(`#${id}-quality`, quality === "unavailable" ? "未接入" : !numeric && !connected ? "尚未收到" : qualityLabels[quality] || "有效性待确认");
    element(`#${id}-quality`).dataset.quality = quality;
    element(`#${id}-card`).dataset.quality = quality;
    const frames = (data?.rawFrameIds || []).map(frameId => `0x${frameId.toString(16).toUpperCase()}`);
    // 历史槽位和引用单体帧；卡片保留计算来源，不伪称收到状态帧。
    const statusFrames = frames.filter(frame => frame === "0x100" || frame === "0x101");
    putText(`#${id}-source`, `${item.source}${statusFrames.length ? ` · ${statusFrames.join(" / ")}` : ""}`);
    element(`#${id}-card`).title = data ? `来源 ${data.source || "未提供"} · 原始质量 ${data.quality}\n记录 ${numeric ? timeText(data.timestamp) : "尚未收到"} · ${sequenceLabel(snapshot)} ${data.sequence ?? "—"}\n原始帧 ${frames.join(" · ") || "未提供"}` : "本记录没有该测点，等待独立数据来源。";
  }
}
function renderStatus() {
  const connected = apiStatus === "connected";
  const source = sourceDescription(activeSnapshot);
  const hasSnapshot = !!activeSnapshot?.id;
  const autoRefresh = element("#auto-refresh").checked;
  putText("#environment-tag", source.tag);
  putText("#source-note", source.note);
  putText("#auto-refresh-note", viewIntent === "replay" ? "回放时暂停" : autoRefresh ? "每秒更新" : "已暂停");
  connectionPill.dataset.state = apiStatus;
  connectionPill.classList.toggle("connected", connected);
  putText("#connection-side", connected ? `API 已连接 · ${sourceMode === "replay" ? "回放" : "快照"}` : apiStatus === "loading" ? "正在读取 API" : "API 失联 / 读取失败");
  const status = element("#data-status"); status.dataset.state = apiStatus;
  if (apiStatus === "loading") status.textContent = hasSnapshot ? "正在读取；暂时保留上次显示值，不能作为本次采样。" : "正在读取 API 快照…";
  else if (!connected) status.textContent = `${failureMessage}；${hasSnapshot ? "保留旧值及原始时间，不能作为当前采样。" : "尚未收到可显示的快照。"}`;
  else status.textContent = `${sourceMode === "replay" ? `记录回放 ${replayName}` : `API 最新快照（${autoRefresh ? "每秒刷新" : "手动刷新"}）`} · ${source.label} · 快照 ${activeSnapshot?.id || "—"}${activeSnapshot?.connectionState === "disconnected" ? hasReadings(activeSnapshot) ? " · 本步设备断线，数值为历史保留值" : " · 尚未收到设备读数" : ""}`;
  const record = replayRecords[replayIndex];
  putText("#replay-position", sourceMode === "replay" && record ? replaySession ? `记录 ${replayOffset + replayIndex + 1} · 本页 ${replayIndex + 1} / ${replayRecords.length} · ${timeText(record.timestamp)}` : `${replayIndex + 1} / ${replayRecords.length} · SEQ ${record.sequence ?? "—"} · ${timeText(record.timestamp)}` : "选择场景后加载；按记录逐步查看");
  element("#replay-prev").disabled = !connected || sourceMode !== "replay" || replayIndex <= 0 && !(replaySession && replayOffset > 0);
  element("#replay-next").disabled = !connected || sourceMode !== "replay" || replayIndex >= replayRecords.length - 1 && !(replaySession && replayNextOffset !== null);
  window.bmsViewState = {mode: sourceMode, apiStatus, snapshotId: activeSnapshot?.id || null, canAnalyze: sourceMode === "latest" && connected && hasSnapshot};
  document.dispatchEvent(new CustomEvent("bms-view-change", {detail: window.bmsViewState}));
}
function renderCells(snapshot) {
  const apiConnected = apiStatus === "connected" && snapshot.connectionState !== "disconnected";
  const onSelect = ({cell}) => {
    selectedCellIndex = cell.cellIndex; renderInspector(); markSelectedCell();
    if (window.matchMedia("(max-width: 1350px)").matches) element("#cell-inspector").scrollIntoView?.({block: "center"});
  };
  const groups = window.BmsModules.render(element("#cells"), snapshot.cells, {compact: true, apiConnected, onSelect, onModuleSelect(group) {
    selectPage("voltage"); element(`#cells-page [data-module-id="${group.id}"]`)?.scrollIntoView?.({block: "center"});
  }});
  window.BmsModules.render(element("#cells-page"), snapshot.cells, {apiConnected, onSelect});
  const stats = window.BmsModules.summarize(snapshot.cells, {apiConnected});
  const moduleCount = groups.filter(group => group.id !== "ungrouped").length;
  putText("#cell-summary", `${moduleCount} 个模组${groups.some(group => group.id === "ungrouped") ? " · 含未分组槽位" : ""} · ${stats.physicalCount} 物理 · ${stats.reservedCount} 保留 · ${stats.invalidCount} 无效`);
  renderInspector(); markSelectedCell();
}
function renderFrameTable(frames, target = "#frame-table", empty = "本步没有新原始帧；保留值的原帧证据见下表。") {
  const rows = (target === "#frame-table" ? frames.slice(-40) : [...frames]).reverse().map(frame => {
    const row = document.createElement("tr");
    const decoded = (frame.decoded || []).map(signal => `${signal.name}=${valueText(signal.value)}${signal.unit ? `${signal.unit.startsWith("0.1") ? " × " : " "}${signal.unit}` : ""} [${signal.quality || (signal.valid === false ? "无效" : "质量未提供")}]`).join(" · ") || "解析未提供";
    for (const text of [timeText(frame.timestamp), frame.sequence ?? "—", frame.bus, `0x${frame.id.toString(16).toUpperCase()}`, frame.dlc, frame.direction, frame.data.map(byte => byte.toString(16).padStart(2, "0")).join(" "), decoded]) addText(row, "td", text); return row;
  });
  if (!rows.length) { const row = document.createElement("tr"); addText(row, "td", empty).colSpan = 8; rows.push(row); }
  element(target).replaceChildren(...rows);
}
function renderEvidence(snapshot, detection, quality) {
  const frames = snapshot.frames; const last = frames[frames.length - 1]; const original = snapshot.modelDetection;
  const items = [
    {label: "01 · RAW FRAME", title: `${frames.length} 个本步原始帧`, body: `${frames.length ? `CAN 页显示${snapshot.dataSource === "simulation" ? "模拟" : snapshot.dataSource === "file" ? "文件中的" : "接收的"}原始字节、记录时间与解析质量。` : "本步没有新帧；旧值不代表收到新帧。"}${retainedFrames(snapshot).length ? ` 另有 ${retainedFrames(snapshot).length} 个保留读数证据帧。` : ""}`, quality: frames.length ? "valid" : "unknown"},
    {label: "02 · QUALITY", title: quality.toUpperCase(), body: "依据已接入的整包测点、物理电芯与连接状态；未接入测点单独标注，保留槽不计作故障。", quality},
    {label: "03 · MODEL DETECTION", title: `${detection.model.toUpperCase()} · ${detection.mode} · 服务端置信度 ${original?.confidence || "—"}`, body: `${detection.reason} · 规则 ${original?.ruleVersion || "—"} · 证据时间 ${timeText(original?.timestamp)}`, quality: original?.confidence !== "high" || original?.model === "unknown" ? "unknown" : "valid"}
  ];
  if (snapshot.diagnostics.length) { const event = snapshot.diagnostics[0]; items.push({label: "04 · DIAGNOSTIC", title: event.code, body: event.message, quality: event.severity === "error" ? "pec_error" : "partial"}); }
  element("#evidence-rail").replaceChildren(...items.map(item => { const node = document.createElement("div"); node.className = "ev"; node.dataset.quality = item.quality; addText(node, "small", item.label); addText(node, "b", item.title); addText(node, "p", item.body); return node; }));
  putText("#last-frame", last ? `${last.bus.toUpperCase()}  0x${last.id.toString(16).toUpperCase()}  ${last.direction.toUpperCase()}  DLC ${last.dlc}\n${last.data.map(byte => byte.toString(16).padStart(2, "0")).join(" ")}\n${timeText(last.timestamp)} · SEQ ${last.sequence ?? "—"}` : "本步无新 CAN 帧");
}
function render() {
  renderStatus(); if (!activeSnapshot) return;
  const snapshot = activeSnapshot; const detection = displayDetection(snapshot); const physical = physicalCells(snapshot);
  const connected = apiStatus === "connected" && snapshot.connectionState !== "disconnected";
  const invalid = window.BmsModules.summarize(snapshot.cells, {apiConnected: connected}).invalidCount;
  const quality = snapshotQuality(snapshot);
  putText("#quality-state", quality.toUpperCase()); element("#quality-state").dataset.quality = quality; element("#quality-detail").dataset.quality = quality;
  putText("#quality-detail", !connected ? hasReadings(snapshot) ? "保留旧值" : "尚未收到数据" : quality === "valid" ? "已显示信号有效" : quality === "unknown" ? "有效性待确认" : "本步存在质量问题");
  putText("#quality-copy", apiStatus !== "connected" ? "API 未成功返回本次数据。保留最后快照及其原始记录时间。" : `${physical.length} 个物理电芯，${invalid} 个无效；${snapshot.cells.length - physical.length} 个保留槽。`);
  renderPackMeasurements(snapshot, connected);
  putText("#firmware", snapshot.firmwareVersion); putText("#firmware-side", snapshot.firmwareVersion); putText("#updated", timeText(snapshot.timestamp));
  const hostSequences = [...snapshot.frames, ...(snapshot.evidenceFrames || [])].map(frame => frame.sequence).filter(Number.isInteger);
  putText("#sequence-label", snapshot.sequenceScope === "host_rx" ? "主机接收序号" : "SEQ");
  putText("#sequence", snapshot.sequenceScope === "host_rx" ? hostSequences.length ? Math.max(...hostSequences) : "—" : sourceMode === "replay" ? replayRecords[replayIndex]?.sequence ?? "—" : snapshot.current.sequence ?? "—");
  putText("#quality-summary", `${invalid} 个物理槽无效 · 原始质量：槽位累压 ${snapshot.totalVoltage.quality} / 电流 ${snapshot.current.quality}`); putText("#snapshot-id", snapshot.id);
  putText("#model", `${detection.model.toUpperCase()} · ${detection.mode}`); putText("#model-reason", detection.reason); putText("#model-reason-side", detection.reason);
  renderCells(snapshot);
  const events = snapshot.diagnostics.map(event => { const node = document.createElement("div"); node.className = `event ${event.severity}`; addText(node, "b", event.code); addText(node, "div", event.message); addText(node, "small", `链路 ${event.chain} · 规则 ${event.ruleVersion} · ${(event.evidence || []).join(" · ")}`); return node; });
  if (!events.length) { const node = document.createElement("p"); node.className = "muted"; node.textContent = "本记录没有诊断事件。"; events.push(node); }
  element("#diagnostic-detail").replaceChildren(...events); renderFrameTable(snapshot.frames); renderEvidence(snapshot, detection, quality);
  const retained = retainedFrames(snapshot);
  putText("#frame-time-label", snapshot.sequenceScope === "host_rx" ? "主机接收时间" : "记录时间");
  putText("#frame-sequence-label", sequenceLabel(snapshot));
  putText("#evidence-frame-count", `${retained.length} 帧`);
  renderFrameTable(retained, "#evidence-frame-table", "没有额外保留的原帧证据。历史记录若未提供原帧，请查看读数的时间与帧引用。");
  window.BmsProduct?.render(snapshot, {connected, records: replayRecords, index: replayIndex, offset: replayOffset, session: replaySession, sourceMode});
}
async function fetchJson(url) {
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 10000);
  try { const response = await fetch(url, {signal: controller.signal}); if (!response.ok) throw new Error(`HTTP ${response.status}`); return await response.json(); }
  finally { clearTimeout(timer); }
}
function verifySnapshot(snapshot) {
  if (!snapshot || !Array.isArray(snapshot.cells) || !Array.isArray(snapshot.frames) || !Array.isArray(snapshot.diagnostics) || !snapshot.totalVoltage || !snapshot.current) throw new Error("快照结构不完整");
  return snapshot;
}
async function loadSnapshot({background = false} = {}) {
  if (background && (viewIntent !== "latest" || sourceMode !== "latest" || pendingSnapshots.size || !element("#auto-refresh").checked)) return;
  const base = apiBase(); const version = ++requestVersion; viewIntent = "latest"; pendingSnapshots.add(version);
  if (!background) { apiStatus = "loading"; failureMessage = ""; render(); }
  try {
    const snapshot = verifySnapshot(await fetchJson(`${base}/api/v1/snapshots/latest`)); if (version !== requestVersion || base !== apiBase()) return;
    activeSnapshot = snapshot; sourceMode = "latest"; replayRecords = []; replayName = ""; replaySession = null; replayOffset = 0; replayNextOffset = null; if (!background) element("#replay-scenario").value = ""; apiStatus = "connected"; render();
  } catch (error) { if (version !== requestVersion || base !== apiBase()) return; apiStatus = "disconnected"; failureMessage = `快照读取失败：${error.message}`; render(); }
  finally { pendingSnapshots.delete(version); }
}
async function loadReplay() {
  const name = element("#replay-scenario").value; if (!name) return loadSnapshot();
  const base = apiBase(); const version = ++requestVersion; viewIntent = "replay"; apiStatus = "loading"; failureMessage = ""; render();
  try {
    const payload = await fetchJson(`${base}/api/v1/replay/scenarios/${encodeURIComponent(name)}`);
    if (!Array.isArray(payload.items) || !payload.items.length) throw new Error("场景没有回放记录"); payload.items.forEach(record => verifySnapshot(record.snapshot));
    if (version !== requestVersion || base !== apiBase()) return;
    replayRecords = payload.items; replayIndex = 0; replayName = name; replaySession = null; replayOffset = 0; replayNextOffset = null; sourceMode = "replay"; activeSnapshot = replayRecords[0].snapshot; apiStatus = "connected"; render();
  } catch (error) { if (version !== requestVersion || base !== apiBase()) return; apiStatus = "disconnected"; failureMessage = `回放加载失败：${error.message}`; render(); }
}
async function loadSessionPage(session, offset = 0, end = false) {
  const base = apiBase(); const version = ++requestVersion; viewIntent = "replay"; apiStatus = "loading"; failureMessage = ""; render();
  try {
    const payload = await fetchJson(`${base}/api/v1/sessions/${encodeURIComponent(session.id)}/snapshots?offset=${offset}&limit=100`);
    if (!Array.isArray(payload.items) || !payload.items.length) throw new Error("该会话没有可读取的快照");
    payload.items.forEach(verifySnapshot);
    if (version !== requestVersion || base !== apiBase()) return;
    replayRecords = payload.items.map(snapshot => ({snapshot, timestamp: snapshot.timestamp, sequence: snapshot.current?.sequence}));
    replayIndex = end ? replayRecords.length - 1 : 0; replaySession = session; replayOffset = offset;
    replayNextOffset = Number.isInteger(payload.nextOffset) && payload.nextOffset > offset ? payload.nextOffset : null;
    replayName = session.name; sourceMode = "replay"; activeSnapshot = replayRecords[replayIndex].snapshot; apiStatus = "connected"; render();
  } catch (error) { if (version !== requestVersion || base !== apiBase()) return; apiStatus = "disconnected"; failureMessage = `会话读取失败：${error.message}`; render(); }
}
function moveReplay(offset) {
  const index = replayIndex + offset; if (apiStatus !== "connected" || sourceMode !== "replay") return;
  if (replaySession && index < 0 && replayOffset > 0) return loadSessionPage(replaySession, Math.max(0, replayOffset - 100), true);
  if (replaySession && index >= replayRecords.length && replayNextOffset !== null) return loadSessionPage(replaySession, replayNextOffset);
  if (index < 0 || index >= replayRecords.length) return;
  replayIndex = index; activeSnapshot = replayRecords[index].snapshot; render();
}
async function loadReplayList() {
  const base = apiBase(); const version = ++listVersion;
  try { const payload = await fetchJson(`${base}/api/v1/replay/scenarios`); if (version !== listVersion || base !== apiBase()) return; const select = element("#replay-scenario"); const previous = select.value; select.replaceChildren(new Option("API 最新快照", ""), ...(payload.items || []).map(name => new Option(name, name))); select.value = (payload.items || []).includes(previous) ? previous : ""; }
  catch (_) { /* 快照读取负责显示 API 错误，此处保留上次场景选择。 */ }
}
function savePreference(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* 偏好保存失败不影响数据展示。 */ } }
function readPreference(key) { try { return localStorage.getItem(key); } catch (_) { return null; } }
function saveViewProfile() {
  const profile = {version: 1, name: theme.value, theme: theme.value, density: document.body.classList.contains("compact") ? "compact" : "cozy", animation: document.body.classList.contains("no-motion") ? "off" : "full", autoRefresh: element("#auto-refresh").checked};
  savePreference("bms-view-profile", JSON.stringify(profile)); return profile;
}
function setTheme(value) { if (!["minimal", "engineering", "operations"].includes(value)) value = "minimal"; theme.value = value; document.body.dataset.theme = value; document.body.classList.toggle("paper", value === "minimal"); savePreference("bms-workspace-theme", value); saveViewProfile(); }
function setDensity(value) { document.body.classList.toggle("compact", value === "compact"); savePreference("bms-density", value); saveViewProfile(); }
function stopMotion() { document.body.classList.add("no-motion"); savePreference("bms-motion", "off"); saveViewProfile(); }
function applyViewProfile(profile) { setTheme(profile.theme); setDensity(profile.density); document.body.classList.toggle("no-motion", profile.animation === "off" || window.matchMedia("(prefers-reduced-motion: reduce)").matches); element("#auto-refresh").checked = profile.autoRefresh; savePreference("bms-auto-refresh", profile.autoRefresh ? "on" : "off"); saveViewProfile(); renderStatus(); }
document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => selectPage(button.dataset.page)));
element("#open-voltage").addEventListener("click", () => selectPage("voltage"));
element("#open-status-frames").addEventListener("click", () => selectPage("can"));
theme.addEventListener("change", () => setTheme(theme.value));
document.querySelectorAll("button[data-theme]").forEach(button => button.addEventListener("click", () => setTheme(button.dataset.theme)));
document.querySelectorAll("button[data-density]").forEach(button => button.addEventListener("click", () => setDensity(button.dataset.density)));
document.querySelectorAll("button[data-motion]").forEach(button => button.addEventListener("click", stopMotion));
element("#refresh").addEventListener("click", () => Promise.all([loadSnapshot(), loadReplayList()]));
element("#auto-refresh").addEventListener("change", () => { savePreference("bms-auto-refresh", element("#auto-refresh").checked ? "on" : "off"); saveViewProfile(); renderStatus(); });
element("#replay-load").addEventListener("click", loadReplay);
element("#replay-prev").addEventListener("click", () => moveReplay(-1)); element("#replay-next").addEventListener("click", () => moveReplay(1));
element("#model-override").addEventListener("change", () => { overrideTime = Date.now() / 1000; render(); });
apiInput.addEventListener("change", () => Promise.all([loadSnapshot(), loadReplayList()]));
element("#fullscreen").addEventListener("click", () => document.documentElement.requestFullscreen?.());
let savedProfile = null;
try { const stored = readPreference("bms-view-profile"); if (stored && window.BmsProfiles) savedProfile = window.BmsProfiles.validate(JSON.parse(stored)); } catch (_) { /* 非法旧配置回落到本地基础偏好。 */ }
setTheme(readPreference("bms-workspace-theme") || "minimal"); setDensity(readPreference("bms-density") || "cozy");
if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || readPreference("bms-motion") === "off") stopMotion();
element("#auto-refresh").checked = readPreference("bms-auto-refresh") !== "off";
if (savedProfile) applyViewProfile(savedProfile); else saveViewProfile();
element("#profile-reset")?.addEventListener("click", () => { applyViewProfile({theme: "minimal", density: "cozy", animation: "full", autoRefresh: true}); putText("#profile-status", "已恢复默认视图。"); });
element("#profile-export")?.addEventListener("click", () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(saveViewProfile(), null, 2)], {type: "application/json"})); const link = document.createElement("a"); link.href = url; link.download = "REVOL-view-profile-v1.json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); putText("#profile-status", "已导出视图档案。");
});
element("#profile-import")?.addEventListener("change", async event => {
  const file = event.target.files?.[0]; if (!file) return;
  try { if (file.size > 65536) throw new Error("视图档案应小于 64 KB。"); const profile = window.BmsProfiles.validate(JSON.parse(await file.text())); applyViewProfile(profile); putText("#profile-status", "已应用视图档案。"); }
  catch (error) { putText("#profile-status", `导入失败：${error.message}`); }
  event.target.value = "";
});
window.setInterval(() => loadSnapshot({background: true}), 1000);
loadReplayList(); loadSnapshot();
window.BmsWorkspace = {apiBase, fetchJson, loadSessionPage, selectPage, refresh: render, latest: loadSnapshot};
