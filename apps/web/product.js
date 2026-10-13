/* 产品视图仅消费合同；测点质量与原始对象保持不变。 */
(function (root) {
  "use strict";
  const qualities = {valid: "有效", unknown: "有效性待确认", pec_error: "PEC 错误", timeout: "超时", stale: "旧值", partial: "部分更新", range_error: "超出范围", disconnected: "断线 · 保留旧值", reserved: "保留槽 · 非物理电芯"};
  const packNames = {totalCurrent: "current", totalVoltageAdc: "totalVoltageAdc", totalVoltageAccumulated: "totalVoltageAccumulated", prechargeVoltage: "prechargeVoltage", soc: "soc"};
  function signalValue(snapshot, name, catalog = {}) {
    const meta = catalog[name] || {}; let item = snapshot.signals?.[name]; let divisor = Number(meta.divisor) || 1;
    const cell = name.match(/^cellVoltage\[(\d+)\]$/); const temp = name.match(/^cellTemperature\[(\d+)\]$/);
    if (packNames[name]) { item = snapshot.packStatus?.[packNames[name]]; divisor = 1; }
    else if (cell) { item = snapshot.cells?.find(c => c.cellIndex === Number(cell[1])); divisor = 1; }
    else if (temp) { item = snapshot.temperatures?.[Number(temp[1])]; divisor = 1; }
    return {name, label: meta.label || name, path: meta.path || name, unit: meta.unit || item?.unit || "", item,
      value: Number.isFinite(item?.value) ? item.value / divisor : null};
  }
  function qualityOf(field, connected = true) {
    if (field.item?.physicalValid === false) return "reserved";
    if (field.value === null) return "unavailable";
    if (!connected) return "disconnected";
    return field.item?.valid === true && field.item.quality === "valid" ? "valid" : field.item?.quality === "valid" ? "unknown" : field.item?.quality || "unknown";
  }
  function feedback(value) { return value === null ? "未收到" : value === 0 ? "断开 · 0" : value === 1 ? "闭合 · 1" : `异常状态 · ${value}`; }
  function temperatureGroups(snapshot) {
    const temperatures = snapshot.temperatures || [];
    const groups = Array.from({length: 8}, (_, module) => ({label: `软件模组 ${String(module + 1).padStart(2, "0")}`, module,
      channels: Array.from({length: 6}, (_, channel) => ({index: module * 6 + channel, channel: channel + 1, item: temperatures[module * 6 + channel]}))}));
    if (temperatures.length > 48) groups.push({label: snapshot.dataSource === "simulation" ? "模拟扩展通道" : "协议外附加通道", extra: true,
      channels: temperatures.slice(48).map((item, i) => ({index: i + 48, item}))});
    return groups;
  }
  function trendPoints(records, field) {
    return records.map(({snapshot}) => {
      const item = snapshot.packStatus?.[field];
      return {time: snapshot.timestamp, value: Number.isFinite(item?.value) ? item.value : null,
        quality: qualityOf({value: Number.isFinite(item?.value) ? item.value : null, item}, snapshot.connectionState !== "disconnected")};
    });
  }
  const model = {signalValue, qualityOf, feedback, temperatureGroups, trendPoints};
  if (typeof module === "object" && module.exports) { module.exports = model; return; }
  root.BmsProductModel = model;
  const $ = selector => document.querySelector(selector);
  const text = (parent, tag, content, className) => { const node = document.createElement(tag); node.textContent = content; if (className) node.className = className; parent.append(node); return node; };
  const time = value => Number.isFinite(value) ? new Date(value * 1000).toLocaleString("zh-CN", {hour12: false}) : "—";
  const number = value => value === null ? "—" : value.toLocaleString("zh-CN", {maximumFractionDigits: 3});
  const frameIds = item => (item?.rawFrameIds || []).map(id => `0x${id.toString(16).toUpperCase()}`).join(" · ") || "未提供";
  const qualityText = value => value === "unavailable" ? "未收到" : qualities[value] || value;
  let catalog = {}; let catalogError = "目录读取中…"; let last = null; let selectedTemperature = null; let libraryVersion = 0; let catalogVersion = 0;
  const expandedGroups = new Set(["A03.State"]);
  const evidenceNodes = new Map();
  let catalogNodes = new Map();
  function badge(parent, field, connected) { const q = qualityOf(field, connected); const node = text(parent, "span", qualityText(q), "quality-badge"); node.dataset.quality = q; return node; }
  function evidence(parent, field) {
    const details = document.createElement("details"); details.className = "signal-evidence"; text(details, "summary", "接收证据");
    // 直接读取上一个节点的 open，避免异步 toggle 在节点替换后回写旧状态。
    details.open = evidenceNodes.get(field.name)?.open === true; evidenceNodes.set(field.name, details);
    text(details, "p", `${field.path} · 原值 ${field.item?.value ?? "—"}`);
    text(details, "p", `${frameIds(field.item)} · ${field.value === null ? "未收到" : time(field.item?.timestamp)} · 序号 ${field.item?.sequence ?? "—"}`);
    text(details, "p", `来源 ${field.item?.source || "未提供"} · 原始质量 ${field.item?.quality || "unknown"}`); parent.append(details);
  }
  function tile(parent, field, connected, format) {
    const card = document.createElement("article"); card.className = "telemetry-card";
    text(card, "span", field.label, "telemetry-label"); text(card, "b", format ? format(field.value) : `${number(field.value)}${field.value !== null && field.unit ? ` ${field.unit}` : ""}`, "telemetry-value");
    badge(card, field, connected); evidence(card, field); parent.append(card);
  }
  function renderTemperature(snapshot, context) {
    const grid = $("#temperature-grid"); grid.replaceChildren();
    const values = (snapshot.temperatures || []).slice(0, 48); const received = values.filter(v => Number.isFinite(v?.value)).length;
    const valid = values.filter(v => v?.valid === true && v.quality === "valid" && Number.isFinite(v.value) && context.connected).length;
    $("#temperature-summary").textContent = `${received} / 48 已收到 · ${valid} 有效`;
    for (const group of temperatureGroups(snapshot)) {
      const section = document.createElement("article"); section.className = "thermal-module";
      const head = document.createElement("div"); head.className = "thermal-heading"; text(head, "h3", group.label);
      text(head, "small", group.extra ? "不计入当前固件 48 路" : `Temp[${group.module * 6}…${group.module * 6 + 5}]`); section.append(head);
      const channels = document.createElement("div"); channels.className = "thermal-channels";
      for (const channel of group.channels) {
        const field = {item: channel.item, value: Number.isFinite(channel.item?.value) ? channel.item.value : null};
        const button = document.createElement("button"); button.className = "thermal-channel"; button.type = "button"; button.dataset.quality = qualityOf(field, context.connected);
        button.setAttribute("aria-pressed", selectedTemperature === channel.index); text(button, "span", group.extra ? `Temp[${channel.index}]` : `GPIO ${channel.channel}`);
        text(button, "b", `${number(field.value)}${field.value === null ? "" : "°"}`); text(button, "small", qualityText(button.dataset.quality));
        button.addEventListener("click", () => { selectedTemperature = channel.index; renderTemperature(last.snapshot, last.context); }); channels.append(button);
      }
      section.append(channels); grid.append(section);
    }
    const inspector = $("#temperature-inspector"); inspector.replaceChildren();
    if (selectedTemperature === null) { text(inspector, "p", "选择温度通道查看接收时间、质量与原始帧。"); return; }
    const item = snapshot.temperatures?.[selectedTemperature];
    const field = {item, value: Number.isFinite(item?.value) ? item.value : null, path: `A03.Cell.Temp[${selectedTemperature}]`};
    text(inspector, "h3", `温度通道 ${selectedTemperature} · ${selectedTemperature < 48 ? `软件模组 ${Math.floor(selectedTemperature / 6) + 1} / GPIO ${selectedTemperature % 6 + 1}` : "扩展记录"}`);
    text(inspector, "p", `${number(field.value)} ℃ · ${qualityText(qualityOf(field, context.connected))} · ${field.value === null ? "未收到" : time(item?.timestamp)}`);
    text(inspector, "code", `${field.path} · ${frameIds(item)} · ${item?.source || "未提供"}`);
  }
  function field(snapshot, name, defaults) { return signalValue(snapshot, name, {...(defaults ? {[name]: defaults} : {}), ...catalog}); }
  function renderPower(snapshot, context) {
    const relay = $("#relay-grid"); relay.replaceChildren();
    for (const [name, label] of [["relayPositive", "主正反馈"], ["relayNegative", "主负反馈"], ["relayPrecharge", "预充反馈"], ["relayDcdc", "DCDC 反馈"]]) tile(relay, field(snapshot, name, {label}), context.connected, feedback);
    const charger = $("#charger-grid"); charger.replaceChildren();
    for (const [name, label, unit, divisor] of [["chargerVoltage", "反馈电压", "V", 10], ["chargerCurrent", "反馈电流", "A", 10], ["chargeTargetVoltage", "目标电压回读", "V", 10], ["chargeTargetCurrent", "目标电流回读", "A", 10], ["chargerStatus", "充电机状态字", "raw", 1], ["bmsState", "BMS 状态码", "raw", 1], ["initFlag", "阈值初始化标志", "raw", 1], ["alarmMask", "故障屏蔽掩码", "raw", 1]]) tile(charger, field(snapshot, name, {label, unit, divisor}), context.connected);
    const alarm = $("#alarm-grid"); alarm.replaceChildren();
    const names = Object.keys(catalog).filter(name => name.startsWith("alarm.")); let active = 0; let received = 0;
    for (const name of names) {
      const value = field(snapshot, name); const row = document.createElement("article"); row.className = "alarm-row";
      if (value.value !== null) received++; if (value.value !== null && value.value !== 0) {active++; row.dataset.alarm = "active";}
      text(row, "span", value.label); text(row, "b", value.value === null ? "未收到" : value.value === 0 ? "无告警 · 0" : `告警原值 ${value.value}`); badge(row, value, context.connected); evidence(row, value); alarm.append(row);
    }
    if (!names.length) text(alarm, "p", catalogError || "未提供告警字段目录。", "product-note");
    $("#alarm-summary").textContent = `${received} / ${names.length} 已收到 · ${active} 非零`;
  }
  function signalTable(entries, snapshot, context) {
    const table = document.createElement("table"); const head = document.createElement("thead"); const headings = document.createElement("tr");
    for (const label of ["字段", "读数", "质量", "记录时间 / 原始帧"]) text(headings, "th", label); head.append(headings); table.append(head);
    const body = document.createElement("tbody");
    for (const [name] of entries) {
      const value = signalValue(snapshot, name, catalog); const row = document.createElement("tr"); const label = document.createElement("td"); text(label, "strong", value.label); text(label, "small", value.path, "field-path"); row.append(label);
      text(row, "td", `${number(value.value)}${value.value !== null && value.unit ? ` ${value.unit}` : ""}`); const quality = document.createElement("td"); badge(quality, value, context.connected); row.append(quality);
      const source = document.createElement("td"); text(source, "span", value.value === null ? "未收到" : time(value.item?.timestamp)); text(source, "small", `${frameIds(value.item)} · SEQ ${value.item?.sequence ?? "—"}`, "field-path"); row.append(source); body.append(row);
    }
    table.append(body); return table;
  }
  function renderCatalog(snapshot, context) {
    const query = $("#catalog-search").value.trim().toLowerCase();
    for (const [group, previous] of catalogNodes) if (!previous.searchExpanded) {
      if (previous.node.open) expandedGroups.add(group); else expandedGroups.delete(group);
    }
    catalogNodes = new Map();
    const entries = Object.entries(catalog).filter(([name, meta]) => `${name} ${meta.label} ${meta.path}`.toLowerCase().includes(query));
    $("#catalog-count").textContent = `${entries.length} 字段`; const tree = $("#catalog-tree"); tree.replaceChildren();
    if (!entries.length) text(tree, "p", catalogError || "没有匹配字段，试试 A03.State、温度或 SOC。", "product-note");
    const groups = new Map();
    for (const entry of entries) { const group = entry[1].path.split(".").slice(0, 2).join("."); if (!groups.has(group)) groups.set(group, []); groups.get(group).push(entry); }
    for (const [group, fields] of groups) {
      const details = document.createElement("details"); details.className = "catalog-group"; details.open = query.length > 0 || expandedGroups.has(group);
      text(details, "summary", `${group} · ${fields.length} 字段`); const surface = document.createElement("div"); surface.className = "table-surface"; surface.append(signalTable(fields, snapshot, context)); details.append(surface);
      catalogNodes.set(group, {node: details, searchExpanded: query.length > 0}); tree.append(details);
    }
    const threshold = $("#threshold-table"); threshold.replaceChildren(); const parameters = Object.entries(catalog).filter(([name]) => name.startsWith("threshold."));
    if (parameters.length) threshold.append(signalTable(parameters, snapshot, context)); else text(threshold, "p", catalogError || "未提供阈值字段目录。", "product-note");
  }
  function svgElement(name, attrs, content) { const node = document.createElementNS("http://www.w3.org/2000/svg", name); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value)); if (content !== undefined) node.textContent = content; return node; }
  function renderTrend(context) {
    const target = $("#trend-chart"); target.replaceChildren(); const points = trendPoints(context.records || [], $("#trend-signal").value || "totalVoltageAdc");
    const numeric = points.filter(point => point.value !== null && Number.isFinite(point.time));
    const caption = $("#trend-caption"); caption.textContent = points.length ? `${context.session?.name || "模拟场景"} · 仅当前加载窗口：记录 ${context.offset + 1}–${context.offset + points.length} · ${numeric.length} 个有数值点` : "先从记录库加载会话；每页最多 100 条，不对未加载的历史做统计。";
    if (!numeric.length) { text(target, "p", points.length ? "当前窗口尚无此测点的读数。" : "等待选择记录", "trend-empty"); return; }
    const low = Math.min(...numeric.map(point => point.value)); const high = Math.max(...numeric.map(point => point.value));
    const padding = high === low ? Math.max(Math.abs(high) * .02, 1) : (high - low) * .1;
    const domainLow = low - padding; const domainHigh = high + padding;
    caption.textContent += ` · 实际范围 ${number(low)}–${number(high)}`;
    const t0 = Math.min(...numeric.map(point => point.time)); const t1 = Math.max(...numeric.map(point => point.time));
    const x = point => t0 === t1 ? 402.5 : 65 + (point.time - t0) / (t1 - t0) * 675;
    const y = point => 210 - (point.value - domainLow) / (domainHigh - domainLow) * 170;
    const svg = svgElement("svg", {viewBox: "0 0 780 260", role: "img", "aria-label": `当前窗口 ${points.length} 条记录，范围 ${number(low)} 至 ${number(high)}`});
    const ticks = low === high ? [domainLow, low, domainHigh] : [low, (low + high) / 2, high];
    for (const value of ticks) { const py = y({value}); svg.append(svgElement("line", {x1: 65, x2: 740, y1: py, y2: py, class: "trend-guide"}), svgElement("text", {x: 55, y: py + 4, "text-anchor": "end", class: "trend-label"}, number(value))); }
    if (t0 === t1) svg.append(svgElement("text", {x: 402.5, y: 245, "text-anchor": "middle", class: "trend-label"}, time(t0)));
    else svg.append(svgElement("text", {x: 65, y: 245, class: "trend-label"}, time(t0)), svgElement("text", {x: 740, y: 245, "text-anchor": "end", class: "trend-label"}, time(t1)));
    points.forEach((point, index) => {
      if (point.value === null || !Number.isFinite(point.time)) return;
      const previous = points[index - 1];
      if (previous?.value !== null && previous && Number.isFinite(previous.time)) svg.append(svgElement("line", {x1: x(previous), y1: y(previous), x2: x(point), y2: y(point), class: `trend-line${previous.quality !== "valid" || point.quality !== "valid" ? " uncertain" : ""}`}));
      const circle = svgElement("circle", {cx: x(point), cy: y(point), r: index === context.index ? 5 : 3, class: `trend-point${point.quality !== "valid" ? " uncertain" : ""}`});
      circle.append(svgElement("title", {}, `${time(point.time)} · ${number(point.value)} · ${qualityText(point.quality)}`)); svg.append(circle);
    }); target.append(svg);
  }
  async function loadCatalog() {
    const base = root.BmsWorkspace.apiBase(); const version = ++catalogVersion;
    try { const payload = await root.BmsWorkspace.fetchJson(`${base}/api/v1/catalog`); if (version !== catalogVersion || base !== root.BmsWorkspace.apiBase()) return;
      if (!payload.items || Array.isArray(payload.items) || typeof payload.items !== "object") throw new Error("字段目录格式不正确");
      catalog = payload.items; catalogError = "";
    } catch (error) { if (version !== catalogVersion || base !== root.BmsWorkspace.apiBase()) return; catalog = {}; catalogError = `字段目录读取失败：${error.message}。刷新快照可重试。`; }
    root.BmsWorkspace.refresh();
  }
  async function loadSessions() {
    const base = root.BmsWorkspace.apiBase(); const version = ++libraryVersion; $("#sessions-status").textContent = "正在读取记录库…";
    try {
      const payload = await root.BmsWorkspace.fetchJson(`${base}/api/v1/sessions`); if (version !== libraryVersion || base !== root.BmsWorkspace.apiBase()) return;
      if (!Array.isArray(payload.items)) throw new Error("会话列表格式不正确");
      const list = $("#session-list"); list.replaceChildren();
      for (const session of payload.items) {
        const row = document.createElement("article"); row.className = "session-entry";
        const about = document.createElement("div"); text(about, "h3", session.name); text(about, "p", `${({can: "CAN 接收", file: "文件输入", simulation: "模拟记录"})[session.source] || "来源未提供"} · ${time(session.first)} → ${time(session.last)}`); row.append(about);
        const actions = document.createElement("div"); actions.className = "session-actions";
        const load = text(actions, "button", "加载记录 ↗", "button primary"); load.addEventListener("click", () => root.BmsWorkspace.loadSessionPage(session));
        for (const [kind, label] of [["summary", "简易 CSV"], ["full", "完整 ZIP"], ["snapshot", "快照 JSON"]]) { const link = text(actions, "a", label, "button"); link.href = `${base}/api/v1/sessions/${encodeURIComponent(session.id)}/export?kind=${kind}`; link.setAttribute("download", ""); }
        row.append(actions); list.append(row);
      }
      $("#sessions-status").textContent = payload.items.length ? `${payload.items.length} 个会话。逐页加载，每页最多 100 条；上方箭头可跨页回放。导出不修改原始记录。` : "尚无会话。通过 Windows 启动器开始模拟、文件输入或 CAN 只读采集。";
    } catch (error) { if (version !== libraryVersion || base !== root.BmsWorkspace.apiBase()) return; $("#session-list").replaceChildren(); $("#sessions-status").textContent = `记录库读取失败：${error.message}。检查连接设置后刷新列表。`; }
  }
  root.BmsProduct = {render(snapshot, context) { last = {snapshot, context}; renderTemperature(snapshot, context); renderPower(snapshot, context); renderCatalog(snapshot, context); renderTrend(context); }};
  $("#catalog-search").addEventListener("input", () => { if (last) renderCatalog(last.snapshot, last.context); });
  $("#trend-signal").addEventListener("change", () => { if (last) renderTrend(last.context); });
  $("#sessions-refresh").addEventListener("click", loadSessions);
  $("#api-url").addEventListener("change", () => Promise.all([loadCatalog(), loadSessions()]));
  $("#refresh").addEventListener("click", loadCatalog);
  loadCatalog(); loadSessions(); root.BmsWorkspace.refresh();
})(typeof window === "undefined" ? globalThis : window);
