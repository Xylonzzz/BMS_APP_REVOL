# REVOL Tauri 2 Windows 桌面端

本目录是正式桌面壳的实现，与 `apps/desktop_launcher.py` 的 Tk 便携候选版分开。桌面壳使用 Tauri 2 / WebView2，采集和 API 继续使用现有 Python 实现；工作台直接在独立内嵌窗口加载本机 API 页面。

## 生命周期

1. 打开桌面端只启动无采集的 sidecar 控制通道；默认模拟模式，不加载 CAN DLL。
2. 用户点击“启动工作台”，Tauri 通过标准输入 JSON Lines 请求 sidecar 创建独立会话、绑定随机本地端口并启动输入。
3. Tauri 打开内嵌工作台，页面从同源 HTTP API 读取数据。工作台窗口不授予桌面原生操作权限。
4. “停止会话”停止采集和 API。关闭主窗口请求 sidecar 正常结束，等待退出；异常超时后终止该子进程。
5. 主进程异常退出导致标准输入 EOF 时，sidecar 也执行 `controller.stop()`。

命令白名单只有 `settings/start/status/stop/shutdown`，没有发送 CAN 或继电器控制命令。配置与会话沿用 `%LOCALAPPDATA%\REVOL`，每次启动使用独立目录。读取偏好始终回到模拟模式。

## 构建

环境需要 Node/npm、Rust MSVC、Visual Studio C++ Build Tools、Windows SDK 和 WebView2。2026-10-13 已核实本机 VS2022/MSVC 14.41、SDK 10.0.22621.0、WebView2 154；Rust 安装在项目专用 `runtime/live-check/toolchains`，不会修改系统 PATH。

```powershell
# 项目根目录
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
Push-Location apps/desktop
npm ci
Pop-Location
powershell -ExecutionPolicy Bypass -File tools/build_tauri_windows.ps1
```

脚本先将 Python sidecar 打包到 `apps/desktop/sidecar/revol-sidecar`，再构建 Rust 壳和当前用户 NSIS 安装包。`-SkipSidecar` 仅适合 sidecar 已是当前源码的重编译；`-NoBundle` 只构建 EXE。构建不安装桌面程序、不访问真实 CAN。

产物位置：

- `apps/desktop/src-tauri/target/release/revol-desktop.exe`
- `apps/desktop/src-tauri/target/release/sidecar/`：运行必需的后台与资源。
- `apps/desktop/src-tauri/target/release/bundle/nsis/`：Windows 安装包。

安装包当前要求机器已有 WebView2。没有把 WebView2 自动下载器或厂商 CAN SDK 混入安装流程。DLL 和进程位数必须一致；64 位包不能加载旧 32 位 SDK。

## 离线验证

```powershell
python -m unittest apps.desktop.sidecar.test_host -v
npm --prefix apps/desktop run check
```

EXE 支持 `--self-test <绝对JSON路径>`。该模式不开 GUI、不连接 CAN：验证 sidecar IPC、默认模拟、API 可用、停止后端口关闭以及子进程退出。sidecar 也提供独立 `--self-test`，可验证首页、Schema、电池图标等冻结资源。

单元测试、冻结包自检、实际桌面打开和真实 CAN 联调属于不同验收项。只有通过对应操作才能报告该项完成。
