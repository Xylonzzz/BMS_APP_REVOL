(function () {
  "use strict";

  const qualityLabels = {
    valid: "有效", pec_error: "PEC 错误", timeout: "超时", partial: "部分更新",
    stale: "旧值", range_error: "超出范围", disconnected: "断线 · 旧值",
    unknown: "质量未知", reserved: "保留槽"
  };
  const qualityPriority = ["disconnected", "timeout", "pec_error", "range_error", "stale", "partial", "unknown"];
  const isIndex = value => Number.isInteger(value) && value >= 0;
  const twoDigits = value => String(value).padStart(2, "0");
  const rawValue = value => value == null ? "—" : String(value);
  const isTrusted = cell => cell.physicalValid !== false && cell.valid === true && cell.quality === "valid" && Number.isFinite(cell.value);

  function moduleIndex(cell) {
    if (isIndex(cell.moduleIndex)) return cell.moduleIndex;
    // 旧记录的从控来源可确定模组；软件槽数量不能证明拓扑。
    const match = typeof cell.source === "string" ? cell.source.match(/^(?:simulated_)?slave_(\d+)$/) : null;
    const number = match ? Number(match[1]) : 0;
    return Number.isSafeInteger(number) && number > 0 ? number - 1 : null;
  }

  function groupCells(cells) {
    const groups = new Map();
    for (const cell of cells) {
      const index = moduleIndex(cell);
      const id = index === null ? "ungrouped" : `module-${index}`;
      if (!groups.has(id)) groups.set(id, {id, label: index === null ? "未分组" : `模组 ${twoDigits(index + 1)}`, cells: [], index});
      groups.get(id).cells.push(cell);
    }
    return Array.from(groups.values()).sort((a, b) => (a.index ?? Infinity) - (b.index ?? Infinity)).map(group => {
      group.cells.sort((a, b) => {
        const first = isIndex(a.channelIndex) ? a.channelIndex : Infinity;
        const second = isIndex(b.channelIndex) ? b.channelIndex : Infinity;
        return first === second ? (a.cellIndex ?? Infinity) - (b.cellIndex ?? Infinity) : first - second;
      });
      return {id: group.id, label: group.label, cells: group.cells};
    });
  }

  function cellQuality(cell, apiConnected) {
    if (cell.physicalValid === false) return "reserved";
    if (!apiConnected) return "disconnected";
    if (isTrusted(cell)) return "valid";
    return cell.quality !== "valid" && Object.hasOwn(qualityLabels, cell.quality) ? cell.quality : "unknown";
  }

  function summarize(cells, {apiConnected = true} = {}) {
    const physical = cells.filter(cell => cell.physicalValid !== false);
    const valid = apiConnected ? physical.filter(isTrusted) : [];
    const values = valid.map(cell => cell.value);
    const min = values.length ? Math.min(...values) : null;
    const max = values.length ? Math.max(...values) : null;
    const issues = new Set(physical.map(cell => cellQuality(cell, apiConnected)));
    const quality = !apiConnected ? "disconnected" : qualityPriority.find(item => issues.has(item)) || (physical.length ? "valid" : "reserved");
    return {
      totalCount: cells.length, physicalCount: physical.length, reservedCount: cells.length - physical.length,
      validCount: valid.length, invalidCount: physical.length - valid.length,
      min, max, delta: min === null ? null : max - min, quality
    };
  }

  function appendText(parent, tagName, className, text) {
    const node = document.createElement(tagName);
    node.className = className;
    node.textContent = text;
    parent.append(node);
    return node;
  }

  function cellLabel(cell) {
    if (isIndex(cell.channelIndex)) return `C${twoDigits(cell.channelIndex + 1)}`;
    return isIndex(cell.cellIndex) ? `S${String(cell.cellIndex + 1).padStart(3, "0")}` : "槽位未知";
  }

  function evidenceTitle(cell, group, quality) {
    const timestamp = Number.isFinite(cell.timestamp) ? new Date(cell.timestamp * 1000).toLocaleString("zh-CN", {hour12: false}) : "未提供";
    const frames = (cell.rawFrameIds || []).map(id => `0x${id.toString(16).toUpperCase()}`).join(", ") || "无";
    const originalQuality = quality !== cell.quality ? ` · 原始质量 ${cell.quality || "unknown"}` : "";
    const slot = isIndex(cell.cellIndex) ? `软件槽 ${cell.cellIndex + 1}` : "软件槽未知";
    return `${group.label} · ${cellLabel(cell)} · ${rawValue(cell.value)} ${cell.unit || "mV"} · ${qualityLabels[quality]}${originalQuality}\n${slot} · 来源 ${cell.source || "未提供"}\n记录 ${timestamp} · SEQ ${cell.sequence ?? "—"} · 原始帧 ${frames}`;
  }

  function render(container, cells, {compact = false, apiConnected = true, onSelect, onModuleSelect} = {}) {
    const groups = groupCells(cells);
    const cards = groups.map(group => {
      const stats = summarize(group.cells, {apiConnected});
      const card = document.createElement("section");
      card.className = "module-card";
      card.dataset.moduleId = group.id;
      card.dataset.quality = stats.quality;
      card.setAttribute("aria-label", group.label);

      const head = document.createElement("header");
      head.className = "module-head";
      const heading = document.createElement("div");
      heading.className = "module-heading";
      appendText(heading, "span", "module-index", group.id === "ungrouped" ? "?" : twoDigits(Number(group.id.slice(7)) + 1));
      appendText(heading, "h3", "module-name", group.label);
      appendText(heading, "span", "module-count", `${stats.physicalCount} 电芯 · ${stats.reservedCount} 保留`);
      head.append(heading);
      const state = appendText(head, "span", "module-state", qualityLabels[stats.quality]);
      state.dataset.quality = stats.quality;
      if (onModuleSelect) {
        const open = appendText(head, "button", "module-open", "查看单体");
        open.type = "button";
        open.setAttribute("aria-label", `查看${group.label}单体`);
        open.addEventListener("click", () => onModuleSelect(group));
      }
      card.append(head);

      const metrics = document.createElement("div");
      metrics.className = "module-metrics";
      appendText(metrics, "span", "module-range", `有效值 ${stats.min === null ? "—" : `${stats.min}–${stats.max}`} mV`);
      appendText(metrics, "span", "module-delta", `有效压差 ${rawValue(stats.delta)} mV`);
      appendText(metrics, "span", "module-validity", `${stats.validCount}/${stats.physicalCount} 有效${stats.invalidCount ? ` · ${stats.invalidCount} 无效` : ""}`);
      card.append(metrics);

      const cellGrid = document.createElement("div");
      cellGrid.className = `module-cells${compact ? " compact" : ""}`;
      for (const cell of group.cells) {
        const quality = cellQuality(cell, apiConnected);
        const button = document.createElement("button");
        button.type = "button";
        button.className = `cell${compact ? " compact" : ""}`;
        button.dataset.quality = quality;
        button.dataset.moduleId = group.id;
        button.dataset.cellIndex = String(cell.cellIndex);
        if (isIndex(cell.channelIndex)) button.dataset.channelIndex = String(cell.channelIndex);
        button.title = evidenceTitle(cell, group, quality);
        button.setAttribute("aria-label", `${group.label} ${cellLabel(cell)} ${rawValue(cell.value)} ${cell.unit || "mV"} ${qualityLabels[quality]}`);
        appendText(button, "span", "cell-label", cellLabel(cell));
        if (!compact) {
          // 无效值和断线旧值保留显示，只有可信统计排除它们。
          appendText(button, "strong", "cell-value", rawValue(cell.value));
          appendText(button, "span", "cell-unit", cell.unit || "mV");
          appendText(button, "span", "cell-quality", qualityLabels[quality]);
        }
        if (onSelect) button.addEventListener("click", () => onSelect({cell, module: group}));
        cellGrid.append(button);
      }
      card.append(cellGrid);
      return card;
    });
    container.replaceChildren(...cards);
    return groups;
  }

  window.BmsModules = {groupCells, summarize, render};
})();
