# REVOL Windows 1.0 交付计划

用户于 2026-10-13 同意方案并授权 goal 执行。范围为 Windows 与浏览器端，Android、小程序与公网同步保留为后续版本。已有未提交改动继续保留，不自动提交、推送或修改固件。

## 交付顺序

1. 统一真实 CAN 与 JSONL 文件输入、连续接收、原始记录、逐字段聚合及断线状态；提供浏览器可用版本。
2. 准备首次真实 CAN 只读联调，由用户核实设备/固件/接线并操作硬件；无需等待桌面壳完成。
3. 补齐温度、状态反馈、会话管理、趋势、导出、A03 已传输字段与只读诊断。
4. 提供双击启动的便携候选版，随后完成 Tauri 2 桌面壳与后台进程生命周期。
5. 验证安装/启动、连续记录、回放、导出和退出，形成版本包与使用文档；软件验收与实机结果分别记录。

## 第一阶段接口与文件分工

- `services/edge_agent/adapters.py`：只读适配器接口及文件源。适配器提供 `open()`、`receive(limit=1000, timeout_ms=100)`、`close()`，EOF 与空接收区分；禁止提供发送命令。
- `services/edge_agent/controlcan.py`：ControlCAN ctypes 适配，惰性加载 SDK，明确 DLL 位数和返回码错误。设备/通道/500k 或 250k 可配置；本轮只用假 SDK 测试，不加载真实设备。
- `packages/bms_protocol/aggregator.py`：`SnapshotAggregator(data_source='can', firmware_version='unknown', stale_after=2.0)`；`ingest(frame)` 更新证据，`snapshot(timestamp=None, connected=True)` 返回快照。接收序号是主机序号，不是固件批次；旧值保留原时间/序号，空槽为 null，缺测点不造零。
- `services/edge_agent/capture.py`：独立接收 worker，原始帧追加落盘，定时发布快照，停止/错误时关闭适配器并发布断线状态；文件 EOF 明确结束。
- `services/edge_agent/session_store.py`：读取最新记录避免每次解析全文件；增加并发保护，原始 JSONL 不改写。
- `services/api_server/server.py`：模拟/文件/真实 CAN 明确选择，不把空真实会话自动播种为模拟；API 返回采集状态，支持统一页面服务。
- `schemas/signal-tree.json`、`packages/bms-contracts/types.ts`：新增来源、主机接收证据和可空值；旧模拟合同继续兼容。
- `apps/web`：按 API 来源更新文案与连接状态，持续刷新最新快照但不打断回放；替换为创意电池品牌标识。

## 数据和硬件边界

- 当前确认 CAN1、标准数据帧、DLC 8；设备名称 USBCAN2 不代表主控 CAN2。实际适配器型号、DLL 位数、板内固件与现场参数仍需核实。
- 未携带固件有效性证据的真实读数标记 unknown；本机接收时间/序号不得伪装固件采样时间/批次。文件输入不得冒充实时 CAN。
- 每次快照的 `frames` 只包含新输入；旧值可引用 `evidenceFrames` 中的原帧。读取快照不得重新解释旧帧覆盖超时/断线质量。
- ADC 总压、累压、预充、电流、SOC 的单位和来源沿用已核对合同；144 软件电压槽与 136 物理电芯布局保持兼容。
- 接收与写盘失败必须显式报告，不能悄悄丢帧；有界接收批次和快照频率，UI 筛选不影响采集。
- 本轮不连接真实 CAN、不执行固件/高压命令；接线及实机操作由用户完成。

## 验证与风险

- 聚合器：分批帧、缺帧、重复帧、错总线/方向、短帧、时间/主机序号、超时、断线与恢复；不把未经证实的质量升级有效。
- 适配器：ABI 大小、Open/Init/Start/Receive/Close 错误、空接收、错误码与资源释放，全部使用假 SDK。
- 端到端：录制帧 → 原始 JSONL → 聚合 → API → 浏览器，核对数值、来源和质量；运行既有回归与 Schema 验证。
- 品牌图标：原创电池轮廓与负空间 R，静态 SVG，实际尺寸及浅深主题验收；不写复述图形实现的测试。
- 风险为 SDK/进程位数差异、接收时间不能代表采样时间及现有协议缺质量位；分别以明确错误、来源标签和 unknown 状态处理。
- 回退仅针对本阶段文件差异，保留原始记录与此前成果。

## 状态

- [complete] 输入、连续聚合、原帧记录；含分页不阻塞采集与文件输入限额
- [complete] 首轮真实 CAN 联调准备：旧上位机参数已核对，现场步骤与实测记录模板已落盘
- [external_pending] 用户实物设备/驱动/固件确认及真实CAN测试，未冒充已通过
- [complete] 温度、反馈、阈值、A03目录、会话分页/趋势/导出、只读诊断和显示档案
- [complete] Tauri Windows程序、NSIS安装器和便携ZIP已交付；最终包自检、实际窗口和临时安装/卸载烟测通过。目录 `dist/windows-tauri/0.1.0-candidate-20261013-080259`
- [complete] REVOL 创意电池图标与浅深主题/手机尺寸验收
