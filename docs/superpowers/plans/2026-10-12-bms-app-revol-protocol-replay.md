# BMS_APP_REVOL 协议与回放质量基线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 建立兼容旧上位机功能语义、可回放、可追溯的 BMS 协议与质量状态基础，为 Windows MVP 提供稳定合同。

**Architecture:** 保留现有 Python 标准库服务、协议解码器、JSONL SessionStore 和无构建器浏览器页面。新增独立的模型识别模块和回放场景生成器；协议解析、质量判断和 AF/EF 识别在共享 Python 包中完成，API 只暴露结果与证据，页面不自行推断质量。

**Tech Stack:** Python 3.11+ 标准库、unittest、JSON Schema、OpenAPI YAML、原生 HTML/CSS/JavaScript。

## Global Constraints

- 只读数据链：不接入真实 CAN、ST-Link、高压设备或控制命令。
- 保留原始帧和原始数值；`valid=false` 时页面不能当作本轮有效采样。
- 144 软件电压槽与 136 物理电芯分开表达，空槽必须有物理有效性标记。
- AF/EF 自动识别必须有收齐、连续序号和新鲜度证据；手动选择覆盖自动结果并记录来源。
- 同一 CAN ID 的上下行含义分离，协议字段必须包含总线、方向、ID、DLC、端序、单位和规则版本。
- 旧上位机页面语义保留，旧实现中的瞬时切换、混合默认 EF 和逐项覆盖缺陷不得原样复制。
- 每次改动后运行与范围相称的 Python、Schema、API 和前端语法检查。
- 当前目录无 Git 仓库，不执行虚构的 commit；用计划、测试输出和变更文件记录替代。

### Task 1: 扩展共享合同与协议元数据

**Files:** `packages/bms-contracts/types.ts`, `schemas/signal-tree.json`, `schemas/websocket-events.json`, `schemas/openapi.yaml`, `services/api_server/test_api.py`

**Interfaces:** Produces `CanFrame` metadata (`ide`, `rtr`, `bus`, `direction`, `dlc`, `timestamp`, `sequence`), `ModelDetection` (`model`, `mode`, `confidence`, `reason`, `observed`, `ruleVersion`, `timestamp`), and quality states `valid`, `pec_error`, `timeout`, `partial`, `stale`, `range_error`, `disconnected`, `unknown`.

- [x] Extend TypeScript types and JSON Schema without removing existing fields; add `physicalValid` and snapshot `modelDetection`.
- [x] Document the new schemas and replay metadata in OpenAPI.
- [x] Add API assertions for invalid values, raw frame references, and model detection evidence.
- [x] Run JSON Schema loading and expect `schemas ok`.

### Task 2: Implement deterministic AF/EF model detection

**Files:** Create `packages/bms_protocol/model_detection.py`, `packages/bms_protocol/test_model_detection.py`; modify `services/edge_agent/simulator.py`.

**Interfaces:** `detect_model(snapshot: dict, override: str | None = None, previous: dict | None = None) -> dict`; AF evidence uses voltage indices `[17, 35, 53, 71, 89, 107, 125]` and temperature indices `48..69`.

- [x] Write failing tests for complete AF, complete EF, mixed, incomplete, stale and invalid evidence.
- [x] Write failing tests for manual AF/EF override and recovery to automatic mode.
- [x] Implement aggregate evidence checks without per-slot overwrite; return reasons and observed indices.
- [x] Add `modelDetection` to simulator snapshots while preserving existing 136-cell output.
- [x] Run `python -m unittest packages.bms_protocol.test_model_detection services.edge_agent.test_simulator -v`.

### Task 3: Add deterministic replay scenarios

**Files:** Create `services/edge_agent/replay_scenarios.py`, `services/edge_agent/test_replay_scenarios.py`; modify `services/edge_agent/session_store.py`.

**Interfaces:** `build_scenario(name: str) -> list[dict]`; names `valid`, `pec_error`, `partial`, `timeout_stale`, `disconnected_reconnect`, `af`, `ef`, `mixed_model`.

- [x] Test every scenario’s expected quality state, raw frame reference, sequence and diagnostics.
- [x] Implement fixed timestamps and sequences; do not use wall-clock or random scenario identity.
- [x] Add AF, EF, mixed and manual override fixtures using the detector contract.
- [x] Verify JSONL persistence and scenario isolation in a temporary directory.
- [x] Run `python -m unittest services.edge_agent.test_replay_scenarios -v`.

### Task 4: Expose replay metadata through the API

**Files:** Modify `services/api_server/server.py`, `services/api_server/test_api.py`, `schemas/openapi.yaml`.

**Interfaces:** `GET /api/v1/replay/scenarios` and `GET /api/v1/replay/scenarios/{name}`; existing endpoints preserve quality, raw frame references and `modelDetection`.

- [x] Add failing endpoint tests for all replay scenarios and model states.
- [x] Implement read-only routes; unknown names return JSON 404 and do not mutate live storage.
- [x] Assert response evidence includes scenario, sequence, quality, raw frames and model reason.
- [x] Run `python -m unittest services.api_server.test_api -v`.

### Task 5: Preserve old page semantics in the browser

**Files:** Modify `apps/web/index.html`, `apps/web/app.js`, `apps/web/theme.css`, `apps/web/README.md`.

**Interfaces:** Keep BMS state, Cell_Vol_1/2, Cell_Tem_1/2, relay/charge, threshold, CAN and diagnostics/test entries. Display AF, EF or unknown with automatic/manual source and reason; distinguish invalid, stale, partial and disconnected values.

- [x] Add a replay selector, AF/EF/automatic controls, model source/reason and read-only status label.
- [x] Render compatibility page mapping and slot counts from shared snapshot data.
- [x] Render numeric invalid values with their quality label and stale/disconnected state.
- [x] Run `node --check apps/web/app.js`.

### Task 6: End-to-end verification and documentation

**Files:** Modify `docs/第一阶段运行说明.md`, `README.md`, `docs/BMS_APP_REVOL_产品方案与需求.md`, `task_plan.md`, `findings.md`, `progress.md`.

- [x] Run the complete Python suite and protocol tests; record exact results.
- [x] Run JSON/OpenAPI checks and both frontend syntax checks.
- [x] Start the API, query health and every replay scenario, then stop it.
- [x] Document old-page compatibility, AF/EF behavior, replay commands and unverified hardware boundaries.
- [x] Mark only verified phases complete in `task_plan.md` and record evidence in `progress.md`.

## Plan self-review

- Spec coverage: protocol metadata and quality states are Task 1; AF/EF compatibility is Tasks 2 and 5; replay scenarios are Task 3; API evidence is Task 4; browser semantics and verification limits are Tasks 5 and 6.
- Placeholder scan: no TBD, TODO, or unspecified implementation steps.
- Type consistency: `ModelDetection` fields and `detect_model()` signature are defined in Task 1 and consumed by Tasks 2–5.

