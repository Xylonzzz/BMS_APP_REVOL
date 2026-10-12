# BMS_APP_REVOL 第一阶段基础平台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 建立 BMS_APP_REVOL 的第一阶段可运行基础：共享数据合同、模拟 CAN 会话、质量状态、只读 API 和一个可被后续多端复用的客户端数据层。

**Architecture:** 以 Schema-first 和 API-first 为边界。Python 服务负责模拟会话、数据质量判断和 HTTP API；TypeScript 包保存前端共享类型与解析辅助；原始会话文件与 SQLite 索引先采用可替换的存储接口，避免把平台 UI 与采集实现绑定。第一阶段不连接真实 CAN，不发送任何控制命令。

**Tech Stack:** Python 3.11+ 标准库、`unittest`、TypeScript 5.x 类型合同、OpenAPI 3.1、JSON Lines 会话格式。

## Global Constraints

- 面向用户的产品界面、API 字段和文档使用简体中文；代码标识使用英文。
- PEC 错误、超时、部分更新和旧值必须保留质量状态，不能按普通有效值处理。
- 原始会话记录不可变；诊断结果必须引用原始事件和规则版本。
- 第一阶段只读，不实现继电器、预充、均衡或充电控制发送。
- 共享合同必须能被 Windows、Android、浏览器和微信小程序客户端复用。
- 运行验证只使用模拟数据，不操作真实 CAN、ST-Link 或高压设备。

---

### Task 1: 建立项目目录与共享数据合同

**Files:**
- Create: `D:/BMS/BMS_APP_REVOL/schemas/signal-tree.json`
- Create: `D:/BMS/BMS_APP_REVOL/schemas/websocket-events.json`
- Create: `D:/BMS/BMS_APP_REVOL/packages/bms-contracts/types.ts`
- Create: `D:/BMS/BMS_APP_REVOL/packages/bms-contracts/README.md`

**Interfaces:**
- Produces `QualityStatus`, `BmsValue<T>`, `CanFrame`, `CellVoltage`, `BmsSnapshot`, `DiagnosticEvent`。

- [x] **Step 1: 写入 TypeScript 合同**

```ts
export type QualityStatus = "valid" | "pec_error" | "timeout" | "partial" | "stale" | "range_error"
export type BmsValue<T> = { value: T; timestamp: number; source: string; sequence?: number; valid: boolean; quality: QualityStatus; rawFrameIds?: number[] }
export type CanFrame = { id: number; bus: "can1" | "can2"; data: number[]; dlc: number; timestamp: number; direction: "rx" | "tx"; sequence: number }
export type CellVoltage = BmsValue<number> & { cellIndex: number; unit: "mV" }
export type BmsSnapshot = { id: string; timestamp: number; firmwareVersion: string; totalVoltage: BmsValue<number>; current: BmsValue<number>; cells: CellVoltage[]; frames: CanFrame[] }
export type DiagnosticEvent = { id: string; timestamp: number; severity: "info" | "warning" | "error"; chain: string; code: string; message: string; evidence: string[]; ruleVersion: string }
```

- [x] **Step 2: 写入 JSON Schema**，字段与 TypeScript 合同一致，并标记 `quality`、`valid` 和 `rawFrameIds` 为数据质量字段。
- [x] **Step 3: 写入客户端使用说明**，说明浏览器、Tauri 和 Taro 只能依赖合同与 API，不能直接依赖 Python 实现。
- [x] **Step 4: 检查 JSON 合法性和类型名一致性**。

运行：`python -c "import json, pathlib; [json.load(open(p, encoding='utf-8')) for p in pathlib.Path('schemas').glob('*.json')]; print('schemas ok')"`

Expected：`schemas ok`。

---

### Task 2: 实现模拟 BMS 会话与质量判断

**Files:**
- Create: `D:/BMS/BMS_APP_REVOL/services/edge-agent/simulator.py`
- Create: `D:/BMS/BMS_APP_REVOL/services/edge-agent/session_store.py`
- Create: `D:/BMS/BMS_APP_REVOL/services/edge-agent/test_simulator.py`

**Interfaces:**
- `Simulator.next_snapshot() -> dict`
- `Simulator.inject_pec_error(cell_index: int) -> None`
- `SessionStore.append(snapshot: dict) -> str`
- `SessionStore.list_snapshots() -> list[dict]`

- [x] **Step 1: 先写失败测试**，覆盖正常帧、PEC 错误保留数值但 `valid=False`、原始帧引用存在、会话文件追加后可读取。
- [x] **Step 2: 运行 `python -m unittest services/edge-agent/test_simulator.py -v`，确认测试先失败**。
- [x] **Step 3: 实现固定 8 模组 × 17 节的可重复模拟数据，默认生成总压、电流、单体电压和 CAN 原始帧**。
- [x] **Step 4: 实现 PEC 错误注入**：被注入槽位保留模拟数值，质量为 `pec_error`，`valid=False`，并产生诊断事件证据。
- [x] **Step 5: 实现 JSONL 不可变追加存储和读取索引**。
- [x] **Step 6: 重新运行测试，预期全部 PASS**。

---

### Task 3: 提供只读 HTTP API 和 OpenAPI 合同

**Files:**
- Create: `D:/BMS/BMS_APP_REVOL/services/api-server/server.py`
- Create: `D:/BMS/BMS_APP_REVOL/services/api-server/test_api.py`
- Create: `D:/BMS/BMS_APP_REVOL/schemas/openapi.yaml`
- Create: `D:/BMS/BMS_APP_REVOL/services/api-server/README.md`

**Interfaces:**
- `GET /api/v1/health`
- `GET /api/v1/snapshots/latest`
- `GET /api/v1/snapshots`
- `GET /api/v1/diagnostics`
- `GET /api/v1/schema`

- [x] **Step 1: 先写 API 测试**，使用临时会话目录启动服务，验证健康检查、最新快照、PEC 质量状态和诊断事件可读。
- [x] **Step 2: 运行 API 测试，确认未实现时失败**。
- [x] **Step 3: 使用 Python 标准库 `http.server` 实现只读 API**，服务启动参数为 `python services/api-server/server.py --port 8765 --data-dir <path>`。
- [x] **Step 4: 写入 OpenAPI 3.1 文档，明确响应结构和错误响应**。
- [x] **Step 5: 重新运行 API 测试，预期 PASS**。

---

### Task 4: 建立最小可视化客户端数据层

**Files:**
- Create: `D:/BMS/BMS_APP_REVOL/apps/web/index.html`
- Create: `D:/BMS/BMS_APP_REVOL/apps/web/app.ts`
- Create: `D:/BMS/BMS_APP_REVOL/apps/web/theme.css`
- Create: `D:/BMS/BMS_APP_REVOL/apps/web/README.md`

**Interfaces:**
- `GET /api/v1/snapshots/latest` 的数据渲染为总压、电流、单体质量状态和诊断列表。
- 主题配置至少支持 `engineering`、`operations`、`minimal`。

- [x] **Step 1: 写入不依赖框架构建器的最小浏览器页面**，支持 API 地址输入、刷新、主题切换和 PEC 错误标红。
- [x] **Step 2: 增加 `prefers-reduced-motion` 和动画开关，动画不能改变质量状态。
- [x] **Step 3: 用本地静态服务器打开页面，并用模拟 API 手工验证正常值与 PEC 错误显示**。

---

### Task 5: 第一阶段验证与文档更新

**Files:**
- Modify: `D:/BMS/BMS_APP_REVOL/README.md`
- Modify: `D:/BMS/BMS_APP_REVOL/docs/BMS_APP_REVOL_产品方案与需求.md`
- Create: `D:/BMS/BMS_APP_REVOL/docs/第一阶段运行说明.md`

- [x] **Step 1: 运行所有 Python 单元测试**。
- [x] **Step 2: 校验 JSON Schema 和 OpenAPI YAML 的结构。
- [x] **Step 3: 启动模拟 API，访问健康检查和最新快照。
- [x] **Step 4: 更新文档，记录已实现边界、启动命令和未实现功能。
- [x] **Step 5: 删除仅用于测试且不再需要的临时文件。

## 规格覆盖检查

- 数据合同、API 和多端复用：Task 1、Task 3。
- PEC 错误仍可显示但标记无效：Task 1、Task 2、Task 4。
- 原始帧与解析值对照：Task 1、Task 2、Task 4。
- 定制主题和动画策略：Task 4。
- Vibe Coding 可读合同：Task 1、Task 3。
- Windows/Android/微信小程序生产构建：属于第一阶段之后的客户端任务，本阶段只固定共享合同和 API 边界。

