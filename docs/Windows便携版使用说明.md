# REVOL Windows 便携候选版

本包包含 REVOL 启动窗口、Python 运行时、本地 API 和浏览器工作台。解压后可双击运行，无需另装 Python 或 Node。当前是便携候选版，正式 Tauri 桌面壳属于后续交付。

## 启动与退出

1. 解压整个 `REVOL` 文件夹，保留 `REVOL.exe` 旁的 `_internal` 目录，不要只复制单个 EXE。
2. 双击 `REVOL.exe`。默认选择“模拟数据”，启动窗口本身不会连接 CAN。
3. 点击“启动工作台”，系统创建独立会话，并在默认浏览器中打开页面。地址为 `http://127.0.0.1:<自动分配端口>/`，只监听本机。
4. 可以关闭浏览器页面后重新点击“打开浏览器”。关闭浏览器不会停止记录。
5. 点击“停止会话”结束记录和本地服务；关闭启动窗口也会先结束会话。退出期间窗口保留状态，发生停止错误时显示具体原因。

同一程序中的采集和 API 使用后台线程，无单独后台服务进程。每次点击启动都创建新会话，不覆盖原记录。端口由系统自动分配，不要求占用旧开发地址的 4173 或 8765。

## 三种数据来源

| 来源 | 用法与边界 |
| --- | --- |
| 模拟数据 | 用于离线查看页面和预置回放，不代表真实测量。启动时生成一条示例快照。 |
| JSONL 文件 | 选择原始帧 JSONL 或含 `frames` 的快照 JSONL；读取后写入新的导入会话，保留输入文件。文件读完显示“文件已读完”，不冒充实时采集。 |
| 只读 CAN | 选择厂商 `ControlCAN.dll` 并填写设备参数，点击启动后才加载驱动和开启接收。当前无控制或发送命令。 |

CAN 配置包括设备类型、设备索引、通道和 500000/250000 波特率。默认值是设备类型 4、设备索引 0、通道 0、500000；它们不是对实物参数的自动识别。`USBCAN2` 设备名与主控的 CAN1/CAN2 总线编号并非同一概念。

程序使用只听模式，不发送 ACK。实际联调需要总线上存在正常应答节点；两节点环境是否能连续收帧必须在现场核实。本包不包含厂商驱动或 SDK，不会自动搜索和加载机器上其他 DLL。DLL 位数必须与便携包位数一致；不一致、缺依赖或驱动失败时，启动窗口显示具体错误。

“固件版本标签”可留空。填写内容仅为用户记录，不是设备握手结果，也不能证明板内固件与源码版本一致。文件和 CAN 输入的接收时间、接收序号是主机证据；协议没有提供采样有效性时显示 unknown，不自动认定为有效。

## 会话与配置位置

- 会话：`%LOCALAPPDATA%\REVOL\sessions\<日期时间-唯一编号>\`
- 原始接收帧：会话下的 `frames.jsonl`。
- 解析快照：会话下的 `snapshots.jsonl`。
- 启动偏好：`%LOCALAPPDATA%\REVOL\settings.json`。

模拟会话只有快照文件；尚未收到帧的会话也可能没有 `frames.jsonl`。停止后可复制整个会话目录归档。偏好只保存文件路径、设备参数和手工版本标签，不保存密码或 API 密钥。每次打开程序仍默认“模拟数据”，不会因为上次使用 CAN 就自动重连。

## 构建

在项目根目录，使用项目虚拟环境安装构建依赖后执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1
```

脚本不自动安装软件。产物写入 `dist/windows-portable/<版本-构建时间>/`，包含 `REVOL` 目录、ZIP 和 SHA256 校验文件；`version.json` 记录构建时间、位数、Git 基线和 EXE 哈希。构建会包含当前未提交源码，因此 Git 提交号不能单独代表包内代码。

源码启动与控制器测试：

```powershell
python apps/desktop_launcher.py
python -m unittest apps.test_desktop_launcher -v
```

冻结包可在不打开窗口和浏览器、不加载 CAN 的情况下自检。结果路径必须为绝对路径：

```powershell
$reportPath = Join-Path $env:TEMP "REVOL-self-test.json"
Start-Process -FilePath .\REVOL.exe -ArgumentList @("--self-test", ('"' + $reportPath + '"')) -WindowStyle Hidden -Wait
Get-Content -LiteralPath $reportPath
```

自检建立临时模拟会话，检查健康接口、首页、信号 Schema 和电池图标，随后停止服务并验证端口关闭。JSON 中的 `success=true`、`errors=[]` 和 `portClosed=true` 表示这些检查通过；自检临时会话在退出时清理，不写入正式会话目录。

## 验收边界

便携包生成成功只代表软件已打包。真实 CAN、具体适配器驱动与板内固件兼容、持续记录和实物测量对照需要单独联调；不能用模拟回放代替。Windows 包、浏览器页面和真实采集的验收结果应分别记录。
