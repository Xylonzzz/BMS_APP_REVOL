# REVOL Windows 与浏览器候选产品

本版本使用 Tauri 2 桌面窗口与同一套浏览器工作台。桌面包内附采集/API运行时，无需另装Python或Node；需要Windows x64与WebView2。真实CAN需要另行准备匹配位数的厂商SDK/驱动。版本仍是候选版，软件离线验收与硬件现场验收分别记录。

## 启动

从 `dist/windows-tauri` 中最新交付目录运行安装器，或解压便携包后运行 `revol-desktop.exe`，保留同目录的 `sidecar` 资源。桌面配置页默认“模拟数据”，不会自动连接上次的 CAN。

选择来源后点击“启动工作台”。桌面打开内嵌窗口；配置页显示 `http://127.0.0.1:<随机端口>/`，也可复制到浏览器打开。这里是这台电脑运行本地服务，只能从本机访问；它不是已部署的公网网站。关闭工作台窗口不会停止采集，“停止会话”或关闭连接窗口才结束采集和API。

每次启动创建 `%LOCALAPPDATA%\REVOL\sessions\<日期时间-唯一编号>`。`frames.jsonl`保存原始输入，`snapshots.jsonl`保存聚合快照；默认模拟只有示例快照。记录不要直接编辑，导出也不会修改原文件。

## 三种来源

- 模拟：查看五项整包数据、按模组单体和预置回放场景。PEC及扩展温度为合成测试证据，页面会标明。
- JSONL文件：选择原帧JSONL或包含frames的快照JSONL，写入新的导入会话。每行上限16MiB、每条记录上限10,000帧。EOF表示文件读完，不表示设备仍在线。
- 只读CAN：选择匹配64位SDK、设备类型/索引/通道/波特率，再启动。兼容默认参考旧上位机：类型4、设备0、通道0、500k。只听不发送ACK，现场条件见《真实CAN只读联调》。

## 查看与追溯

总览分别显示ADC总压、单体累压、预充电压、电流、SOC。单体按模组分隔；当前固件144软件槽对应8×17物理电芯，每模组第18槽是兼容保留位。温度显示48路、8×6软件模组，探头物理位置需现场映射。

继电器与充电页保留0/1与异常原值、充电机反馈和告警；预充反馈不能证明实物触点。保护阈值仅显示接收到的回读，不发送查询或写入。A03目录可搜索名称和结构路径，显示值、单位、时间、质量及帧引用。目录覆盖已定义传输字段，不是完整固件内存。

真实CAN/文件没有逐槽PEC与固件采样批次证据时显示“有效性待确认”。缺值保持空；超时、断线保留原值和原时间。自动型号证据不足则unknown，手动AF/EF只覆盖视图，不改原始会话。

## 回放、趋势和导出

“记录与趋势”列出当前及同目录历史会话。点击“加载记录”后用顶部箭头逐条回放；每页100条，到边界自动换页，自动刷新暂停。“刷新快照”回到最新数据。趋势仅统计当前已加载窗口，缺值断开，空心/虚线表示待确认或无效值；不对未加载历史假装全局统计。

简易CSV含关键工程量、继电器、充电、BMS状态、屏蔽掩码、18项告警及各测点质量/时间/来源，缺值留空。完整ZIP包含原帧（存在时）、快照和带大小/SHA256的manifest；导出期间后续采集追加不混入已冻结的导出边界。快照JSON导出会话最新快照。

## 诊断与显示设置

本地规则只读分析API最新快照，显示证据、规则/模型版本和时间，不执行控制。回放中禁用最新快照分析，避免分析对象错位。没有配置外部模型时可直接用local-rules；远程分析需另行配置并在页面明确允许，桌面默认不外发。

总览底部“显示偏好”提供陶瓷白、深海蓝、雾蓝、密度和动效设置；v1视图JSON可导入、导出、恢复默认。配置只影响展示，不改变错误状态、数值和质量。不同随机端口的浏览器存储可能隔离，跨会话迁移显示偏好可使用导出的JSON。

## 开发运行与验证

```powershell
# 在 D:\BMS\BMS_APP_REVOL，浏览器和API同源
python services/api_server/server.py --mode simulation --port 8765 --data-dir runtime/live-check/current
# 打开 http://127.0.0.1:8765/

.venv\Scripts\python.exe -m unittest discover -s packages -p 'test_*.py'
.venv\Scripts\python.exe -m unittest discover -s services -p 'test_*.py'
.venv\Scripts\python.exe -m unittest apps.test_desktop_launcher apps.desktop.sidecar.test_host
node --test apps/web/test_app.cjs apps/web/test_modules.cjs apps/web/test_product.cjs
.venv\Scripts\python.exe tools/check_contracts.py
```

旧Tk便携启动器仍在 `dist/windows-portable`，属于早期候选构建；优先使用本次Tauri交付目录。完整源码打包流程见 `apps/desktop/README.md`。
