"use strict";
const $ = (selector) => document.querySelector(selector);
const invoke = (...args) => window.__TAURI__.core.invoke(...args);
let busy = false;
let active = false;
let polling = false;
const labels = {idle: "准备就绪", starting: "正在启动", running: "会话运行中", stopping: "正在停止", stopped: "会话已停止", completed: "文件已读完", error: "采集出现错误"};
function mode() { return $('input[name="mode"]:checked').value; }
function refreshControls() {
  $("#file-fields").hidden = mode() !== "file";
  $("#can-fields").hidden = mode() !== "can";
  $("#firmware-field").hidden = mode() === "simulation";
  document.querySelectorAll("#connection-form input, #connection-form select, [data-pick]").forEach(node => node.disabled = busy || active);
  $("#start").disabled = busy || active;
  $("#stop").disabled = busy || !active;
  $("#workspace").disabled = busy || !active;
}
function showError(error) { $("#status-message").textContent = String(error); $("#status-message").className = "error"; $("#status-dot").className = "status-dot error"; }
function render(status) {
  active = Boolean(status.active);
  const capture = status.capture;
  const state = capture && status.state === "running" ? capture.state : status.state;
  $("#status-title").textContent = labels[state] || state;
  $("#status-dot").className = `status-dot ${status.error ? "error" : active ? "running" : ""}`;
  $("#frame-count").textContent = capture ? `已接收 ${capture.receivedFrames} 帧` : "";
  $("#status-message").className = status.error ? "error" : "";
  $("#status-message").textContent = status.error || (active ? (status.mode === "simulation" ? "当前为模拟数据，可在工作台中选择质量与断线回放场景。" : "原始帧与解析快照写入本次会话，关闭主窗口会停止服务。") : "选择数据来源后启动。历史会话和原始记录继续保留。");
  $("#session-path").textContent = status.sessionDirectory || "";
  $("#api-url").textContent = status.url || "";
  refreshControls();
}
async function request(command, settings) { return invoke("service_request", {command, settings: settings || null}); }
async function action(operation) {
  if (busy) return;
  busy = true; refreshControls();
  try { await operation(); } catch (error) { showError(error); }
  finally { busy = false; refreshControls(); }
}
$("#connection-form").addEventListener("submit", event => {
  event.preventDefault();
  action(async () => {
    const settings = {mode: mode(), input_path: $("#input-path").value, dll_path: $("#dll-path").value,
      device_type: Number($("#device-type").value), device_index: Number($("#device-index").value), channel: Number($("#channel").value),
      bitrate: Number($("#bitrate").value), firmware_version: $("#firmware-version").value};
    render(await request("start", settings));
    await invoke("show_workspace");
  });
});
$("#stop").addEventListener("click", () => action(async () => render(await request("stop"))));
$("#workspace").addEventListener("click", () => action(() => invoke("show_workspace")));
document.querySelectorAll('input[name="mode"]').forEach(node => node.addEventListener("change", refreshControls));
document.querySelectorAll("[data-pick]").forEach(node => node.addEventListener("click", () => action(async () => {
  const value = await invoke("select_file", {kind: node.dataset.pick});
  if (value) $(node.dataset.pick === "dll" ? "#dll-path" : "#input-path").value = value;
})));
async function poll() {
  if (busy || polling) return;
  polling = true;
  try { render(await request("status")); } catch (error) { showError(error); }
  finally { polling = false; }
}
async function initialize() {
  refreshControls();
  try {
    const settings = await request("settings");
    for (const [key, id] of Object.entries({input_path:"input-path", dll_path:"dll-path", device_type:"device-type", device_index:"device-index", channel:"channel", bitrate:"bitrate", firmware_version:"firmware-version"})) $("#" + id).value = settings[key];
    await poll();
  } catch (error) { showError(error); }
  setInterval(poll, 1000);
}
initialize();
