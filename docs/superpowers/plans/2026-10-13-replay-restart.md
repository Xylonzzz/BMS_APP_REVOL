# 数据链与回放重启实施计划

日期：2026-10-13。用户已确认本轮范围：恢复运行，修复逐步回放、自动/手动型号切换、断线显示和模拟值与原始帧一致性。

**Goal:** 让现有离线工作台可以重现并检查每一步质量变化，为 Windows MVP 建立可验收基线。

**Architecture:** Python 统一生成模拟帧、快照和质量证据，API 装饰原始帧的解析结果。Web 保留服务返回的原始快照，型号覆盖和连接状态作为独立视图状态；回放按记录逐步呈现。

**Tech Stack:** Python 标准库、原生 JavaScript、HTML/CSS、unittest、Node 测试和浏览器验证。

## 范围与合同

- 沿用 `GET /api/v1/replay/scenarios/{name}` 的 `{name, items}` 及每条 `snapshot/sequence/timestamp/frames/diagnostics` 结构；现有端点保持兼容。
- 回放初次显示第一条，上一条/下一条显示步骤、序号和时间；失败后保留旧值但明确不可作为当前采样。
- AF/EF 手动覆盖不修改原始识别证据；恢复自动立即回到服务端判断。
- 修改模拟值后，帧字节、帧解码、累压及质量判断保持一致。缺帧/断线场景不得同时声称有新的有效采样；旧值携带旧时间和序号。
- `temperatures`、帧解析结果和回放记录补入共享合同。模拟质量元数据与真实 CAN 中能否携带 PEC 状态分开说明。
- 只修改 REVOL；不修改固件、原始会话、硬件控制或外部服务配置。原始会话文件保持不可变。
- 风险：回放计数、时间、识别及质量显示会变化；通过新增回归测试和八个场景检查验证。回退按本轮 Git 差异恢复代码，新运行数据与历史 demo 分目录保存。

## Task 1: 服务与协议证据一致性

Files: `services/edge_agent/{simulator,replay_scenarios}.py`、`packages/bms_protocol/{decoder,model_detection}.py`、`services/api_server/server.py` 及相邻测试。

- [x] 将 AF 场景单体值与帧解码不一致、累压未更新、无效证据仍判型号的反例写成失败测试。
- [x] 统一模拟帧与单体值来源，保留缺帧/旧值的实际证据，场景改动后重新计算质量与识别。
- [x] API 回放提供与最新快照一致的帧解析结果；解码核对总线、方向、帧类型和长度。
- [x] 运行 `python -m unittest discover -s services -p 'test_*.py' -v` 和协议测试，通过后报告测试数量和边界。

## Task 2: Web 回放和状态

Files: `apps/web/{app.js,index.html,theme.css,ai.js}`、`apps/web/test_app.cjs`。

- [x] 将自动 EF→手动 AF→恢复自动、断线旧值、两条回放逐步呈现写成失败测试。
- [x] 保存原始快照与视图覆盖各自状态，加入逐步回放、请求竞态保护、失联提示。
- [x] 电压页渲染真实槽位；颜色仅依赖质量；移除页面隐藏规则错误及重复 render。
- [x] 明确分析按钮针对 API 最新快照，避免把回放内容当作分析对象；占位内容明确标注。
- [x] 运行 `node --test apps/web/test_app.cjs` 和 JS 语法检查。

## Task 3: 集成验收与接续入口

Files: `packages/bms-contracts/types.ts`、`schemas/`、`README.md`、`docs/第一阶段运行说明.md`、`task_plan.md`、`findings.md`、`progress.md`。

- [x] 更新合同和当前完成边界，修正“无 Git”等已失效记录。
- [x] 重启修复后的本地 API，探测 health、最新快照和全部八个回放场景。
- [x] 浏览器验证断线→恢复、有效→旧值、AF/EF→手动→自动、电压页、API 失败及恢复，检查页面布局与质量颜色。
- [x] 独立审阅本轮差异，修复有证据的问题，记录最终测试结果、访问地址和下一阶段。

## 下一阶段

真实 CAN 合同逐字段对齐及文件回放输入，再进入 Windows Tauri 壳和 Edge Agent 生命周期；当前离线测试不代表真实 CAN、硬件或多端打包验收。
