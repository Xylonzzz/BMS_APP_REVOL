#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{mpsc, Mutex};
use std::time::{Duration, Instant};
use tauri::{Manager, State};
use tauri_plugin_dialog::DialogExt;

struct Backend {
    child: Child,
    input: ChildStdin,
    output: mpsc::Receiver<String>,
    sequence: u64,
}

impl Backend {
    fn spawn(path: &Path, data_root: Option<&Path>) -> Result<Self, String> {
        if !path.is_file() {
            return Err(format!("找不到采集服务：{}", path.display()));
        }
        let mut command = Command::new(path);
        command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        if let Some(directory) = data_root {
            command.arg("--data-root").arg(directory);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW，禁止额外控制台。
        }
        let mut child = command.spawn().map_err(|e| format!("采集服务启动失败：{e}"))?;
        let input = child.stdin.take().ok_or("采集服务输入管道不可用")?;
        let output = child.stdout.take().ok_or("采集服务输出管道不可用")?;
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(output).lines() {
                match line {
                    Ok(value) => { if sender.send(value).is_err() { break; } },
                    _ => break,
                }
            }
        });
        Ok(Self { child, input, output: receiver, sequence: 0 })
    }

    fn request(&mut self, command: &str, settings: Option<Value>) -> Result<Value, String> {
        if !["start", "stop", "status", "settings", "shutdown"].contains(&command) {
            return Err("不支持的桌面请求".into());
        }
        if let Some(status) = self.child.try_wait().map_err(|e| e.to_string())? {
            return Err(format!("采集服务已退出：{status}"));
        }
        self.sequence += 1;
        let mut message = json!({"id": self.sequence, "command": command});
        if let Some(settings) = settings {
            message["settings"] = settings;
        }
        writeln!(self.input, "{message}").map_err(|e| format!("采集服务写入失败：{e}"))?;
        self.input.flush().map_err(|e| e.to_string())?;
        let deadline = Instant::now() + Duration::from_secs(12);
        loop {
            let wait = deadline.saturating_duration_since(Instant::now());
            let line = self.output.recv_timeout(wait).map_err(|e| format!("采集服务响应失败：{e}"))?;
            let response: Value = serde_json::from_str(&line).map_err(|e| format!("采集服务响应无效：{e}"))?;
            if response["id"].as_u64() != Some(self.sequence) {
                if Instant::now() >= deadline {
                    return Err("采集服务响应超时".into());
                }
                continue;
            }
            if response["ok"] == true {
                return Ok(response["result"].clone());
            }
            return Err(response["error"].as_str().unwrap_or("采集服务错误").into());
        }
    }

    fn shutdown(&mut self) -> Result<(), String> {
        if self.child.try_wait().map_err(|e| e.to_string())?.is_some() {
            return Ok(());
        }
        let reply = self.request("shutdown", None);
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if self.child.try_wait().map_err(|e| e.to_string())?.is_some() {
                return reply.map(|_| ());
            }
            if Instant::now() >= deadline {
                self.child.kill().map_err(|e| format!("采集服务退出失败：{e}"))?;
                self.child.wait().map_err(|e| e.to_string())?;
                return Err("采集服务未正常退出，已终止进程；请检查会话记录".into());
            }
            std::thread::sleep(Duration::from_millis(25));
        }
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        let _ = self.shutdown();
    }
}

struct DesktopState {
    backend: Mutex<Option<Backend>>,
    executable: PathBuf,
    closing: std::sync::atomic::AtomicBool,
}

fn require_local_setup(window: &tauri::WebviewWindow) -> Result<(), String> {
    let url = window.url().map_err(|error| error.to_string())?;
    if window.label() != "main"
        || !(url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost"))
    {
        return Err("原生操作仅限本地连接窗口".into());
    }
    Ok(())
}

#[tauri::command]
async fn service_request(command: String, settings: Option<Value>, window: tauri::WebviewWindow, state: State<'_, DesktopState>) -> Result<Value, String> {
    require_local_setup(&window)?;
    let mut backend = state.backend.lock().map_err(|_| "采集服务状态锁不可用")?;
    if backend.is_none() {
        *backend = Some(Backend::spawn(&state.executable, None)?);
    }
    backend.as_mut().ok_or("采集服务不可用")?.request(&command, settings)
}

#[tauri::command]
async fn select_file(kind: String, app: tauri::AppHandle, window: tauri::WebviewWindow) -> Result<Option<String>, String> {
    require_local_setup(&window)?;
    tauri::async_runtime::spawn_blocking(move || {
        let dialog = app.dialog().file();
        let selected = if kind == "dll" {
            dialog.add_filter("ControlCAN DLL", &["dll"]).blocking_pick_file()
        } else if kind == "jsonl" {
            dialog.add_filter("JSONL 数据", &["jsonl"]).blocking_pick_file()
        } else {
            return Err("不支持的文件类型".into());
        };
        selected.map(|value| value.into_path().map(|path| path.to_string_lossy().into_owned()).map_err(|e| e.to_string())).transpose()
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn show_workspace(app: tauri::AppHandle, window: tauri::WebviewWindow, state: State<'_, DesktopState>) -> Result<(), String> {
    require_local_setup(&window)?;
    let status = {
        let mut backend = state.backend.lock().map_err(|_| "采集服务状态锁不可用")?;
        backend.as_mut().ok_or("请先启动会话")?.request("status", None)?
    };
    let value = status["url"].as_str().ok_or("请先启动会话")?;
    let url = tauri::Url::parse(value).map_err(|e| e.to_string())?;
    if url.scheme() != "http" || url.host_str() != Some("127.0.0.1") || url.port().is_none() {
        return Err("工作台必须使用当前本机服务地址".into());
    }
    if let Some(window) = app.get_webview_window("workspace") {
        window.navigate(url).map_err(|e| e.to_string())?;
        window.show().map_err(|e| e.to_string())?;
        return window.set_focus().map_err(|e| e.to_string());
    }
    tauri::WebviewWindowBuilder::new(&app, "workspace", tauri::WebviewUrl::External(url))
        .title("REVOL · 电池工作台")
        .inner_size(1440.0, 960.0)
        .min_inner_size(800.0, 600.0)
        .build().map_err(|e| e.to_string())?;
    Ok(())
}

fn desktop_self_test(report: &Path) -> Result<(), String> {
    if !report.is_absolute() {
        return Err("自检结果必须使用绝对路径".into());
    }
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let sidecar = exe.parent().ok_or("无法确定程序目录")?.join("sidecar").join("revol-sidecar.exe");
    let directory = std::env::temp_dir().join(format!("revol-tauri-check-{}", std::process::id()));
    std::fs::create_dir_all(&directory).map_err(|e| e.to_string())?;
    let mut checks = serde_json::Map::new();
    let mut errors = Vec::new();
    let run = (|| -> Result<(), String> {
        let mut backend = Backend::spawn(&sidecar, Some(&directory))?;
        let initial = backend.request("settings", None)?;
        checks.insert("defaultSimulation".into(), json!(initial["mode"] == "simulation"));
        let running = backend.request("start", Some(json!({"mode": "simulation"})))?;
        let url = running["url"].as_str().ok_or("missing API url")?;
        let parsed = tauri::Url::parse(url).map_err(|e| e.to_string())?;
        let address = SocketAddr::from(([127, 0, 0, 1], parsed.port().ok_or("missing port")?));
        let mut socket = TcpStream::connect_timeout(&address, Duration::from_secs(2)).map_err(|e| e.to_string())?;
        socket.set_read_timeout(Some(Duration::from_secs(3))).map_err(|e| e.to_string())?;
        socket.write_all(b"GET /api/v1/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").map_err(|e| e.to_string())?;
        let mut response = String::new();
        std::io::Read::read_to_string(&mut socket, &mut response).map_err(|e| e.to_string())?;
        checks.insert("apiHealth".into(), json!(response.contains("200 OK") && response.contains("simulation")));
        backend.request("stop", None)?;
        checks.insert("portClosed".into(), json!(TcpStream::connect_timeout(&address, Duration::from_millis(300)).is_err()));
        backend.shutdown()?;
        checks.insert("sidecarExited".into(), json!(backend.child.try_wait().map_err(|e| e.to_string())?.is_some()));
        Ok(())
    })();
    if let Err(error) = run {
        errors.push(error);
    }
    if let Err(error) = std::fs::remove_dir_all(&directory) {
        errors.push(format!("临时会话清理失败：{error}"));
    }
    let success = errors.is_empty() && checks.len() == 4 && checks.values().all(|value| value == true);
    let payload = json!({"success": success, "checks": checks, "errors": errors, "hardwareValidated": false});
    if let Some(parent) = report.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    std::fs::write(report, serde_json::to_string_pretty(&payload).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    if success { Ok(()) } else { Err("桌面自检失败，详情见结果文件".into()) }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("--self-test") {
        let code = match args.get(2) {
            Some(path) => if desktop_self_test(Path::new(path)).is_ok() { 0 } else { 1 },
            None => 2,
        };
        std::process::exit(code);
    }
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let executable = app.path().resource_dir()?.join("sidecar").join("revol-sidecar.exe");
            app.manage(DesktopState { backend: Mutex::new(None), executable,
                closing: std::sync::atomic::AtomicBool::new(false) });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![service_request, select_file, show_workspace])
        .on_window_event(|window, event| {
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let app = window.app_handle().clone();
                    let state = app.state::<DesktopState>();
                    if !state.closing.swap(true, std::sync::atomic::Ordering::SeqCst) {
                        std::thread::spawn(move || {
                            let state = app.state::<DesktopState>();
                            if let Ok(mut backend) = state.backend.lock() {
                                if let Some(mut service) = backend.take() {
                                    let _ = service.shutdown();
                                }
                            }
                            app.exit(0);
                        });
                    }
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("REVOL desktop initialization failed");
    app.run(|app, event| {
        if let tauri::RunEvent::Exit = event {
            let state = app.state::<DesktopState>();
            if let Ok(mut backend) = state.backend.lock() {
                if let Some(mut service) = backend.take() {
                    let _ = service.shutdown();
                }
            };
        }
    });
}
