# BMS_APP_REVOL

BMS_APP_REVOL 是面向 BMS 的多端监控、诊断、数据记录与运维平台。它不是只替换旧 WinForms 上位机，而是把 BMS 数据链、诊断能力、数据证据和多端界面统一起来，为 Windows、Android、浏览器和微信小程序提供不同入口。

当前文档是产品方案与开发入口。实现前应先阅读 `docs/BMS_APP_REVOL_产品方案与需求.md`，再根据其中的阶段目标建立实施计划。

## 产品定位

平台的核心是可被多个客户端调用的 BMS API、协议模型和诊断服务：

```text
CAN/串口/USB/蓝牙网关
          ↓
    Edge Agent
          ↓
协议解析、质量判断、快照、回放
          ↓
        BMS API
   ┌──────┼────────┬────────┐
 Windows  Android  浏览器  微信小程序
 Tauri    Tauri    Web     Taro
```

Windows 桌面端负责本地 CAN、USB 和工装调试；Android、浏览器和微信小程序主要通过 API、WebSocket 或网关访问数据。BMS 协议、数据模型、诊断规则和导出格式保持共享，界面根据平台能力适配。

## 设计原则

- 原始数据、解析结果、质量状态和诊断证据分开保存。
- API、Schema 和事件先于界面实现，方便 Vibe Coding 和多端复用。
- Agent 先读取结构化证据，再生成带依据的诊断意见。
- PEC 失败的数据可以展示，但必须携带无效状态，不能被当作可信采样值。
- 用户可以选择布局、主题、密度、动画和信息层级；安全状态、错误颜色语义和关键保护提示不能被主题覆盖。
- Windows 本地硬件能力与远程客户端能力分层，不能让微信小程序依赖本地 Python 进程。
- 先支持只读监控、记录和诊断，再评估控制命令；所有控制操作必须有权限、确认和反馈。

## 多端策略

| 端 | 技术入口 | 主要用途 |
|---|---|---|
| Windows | Tauri 2 + TypeScript + Python Edge Agent | CAN/USB 工装、实时调试、完整功能 |
| Android | Tauri 2 或 Android 客户端 + 原生硬件插件/网关 | 移动查看、现场诊断、蓝牙或网络网关 |
| 浏览器 | React/TypeScript Web App | 远程监控、历史数据、多人协作 |
| 微信小程序 | Taro | 移动查看、告警、设备和快照查询 |
| 服务端 | API Server + Agent 服务 | 设备接入、权限、同步、Agent、历史数据 |

Tauri 2 支持桌面和移动构建，但不能直接生成微信小程序。微信小程序和 H5 采用 Taro 或等价多端框架。不能要求四个平台使用完全相同的页面代码；应共享业务包、数据合同和 API，平台页面只在交互和能力差异处适配。

## 用户定制化

定制化分为四层：

1. **主题**：浅色、深色、低亮度、工业高对比度，以及用户自定义颜色、圆角、字体和背景。
2. **布局**：卡片、表格、仪表盘、趋势图、CAN 原始帧、A03 结构体和诊断面板的显示或隐藏、顺序和尺寸。
3. **信息密度**：工装调试、日常监控、远程运维和大屏展示等密度预设。
4. **交互行为**：刷新频率、告警声音、动画开关、自动滚动、单位制式和默认筛选条件。

配置应保存为版本化的 `UserViewProfile`，支持导出、导入、恢复默认和按角色下发。系统预置至少三套风格：

- **Engineering**：高信息密度、原始帧和质量状态优先。
- **Operations**：状态、告警、继电器、预充和关键数值优先。
- **Minimal**：简洁卡片、少量动画和关键指标优先。

动画只用于状态转换、数据更新提示和告警变化，不应阻塞采集线程，也不能用动画代替质量状态。红色、黄色、灰色等安全语义应由状态系统统一定义，主题只能调整表现方式，不能改变语义。

## 推荐目录

```text
BMS_APP_REVOL/
├─ apps/
│  ├─ desktop-tauri/
│  ├─ android-tauri/
│  ├─ web/
│  └─ weapp/
├─ packages/
│  ├─ bms-contracts/
│  ├─ bms-protocol/
│  ├─ bms-diagnostics/
│  ├─ bms-client/
│  ├─ bms-ui/
│  └─ bms-export/
├─ services/
│  ├─ edge-agent/
│  ├─ api-server/
│  ├─ sync-service/
│  └─ llm-agent/
├─ schemas/
│  ├─ openapi.yaml
│  ├─ websocket-events.json
│  └─ signal-tree.json
└─ docs/
   └─ BMS_APP_REVOL_产品方案与需求.md
```

## 主要数据链

```text
原始CAN帧
  → 接收时间/总线/ID/DLC/IDE/RTR
  → 协议解析
  → 数值、单位、槽位和结构体
  → PEC/长度/序号/范围/新鲜度判断
  → 快照和诊断事件
  → 本地记录与服务器同步
  → 各端页面和 Agent
```

每个重要数据至少应能追溯到：原始帧、解析规则版本、固件版本、采集时间、设备标识、质量状态和相关诊断事件。

## 当前阶段边界

第一阶段基础骨架已实现：共享数据合同、模拟 CAN 会话、PEC/超时/部分更新/旧值/断线回放、AF/EF 识别、只读 API 和浏览器监控页面。运行命令见 `docs/第一阶段运行说明.md`。当前仍未连接真实 CAN，未实现 Tauri 打包、Android 原生硬件插件、微信小程序构建、服务器同步和控制命令。后续顺序为：真实协议合同 → Tauri Windows MVP → Web/Android/小程序客户端 → 服务器同步与长期分析 → Agent 增强。

来源参考：

- [Tauri 官方介绍](https://v2.tauri.app/start/)
- [Tauri 分发文档](https://v2.tauri.app/distribute/)
- [Tauri sidecar](https://v2.tauri.app/develop/sidecar/)
- [Taro 多端文档](https://docs.taro.zone/docs/1.x/)
- [Taro 跨平台开发](https://docs.taro.zone/docs/envs)
- [Vector CANalyzer](https://www.vector.com/int/en/products/products-a-z/software/canalyzer/)
- [SavvyCAN 文档](https://www.savvycan.com/docs/)
- [Eclipse KUKSA](https://projects.eclipse.org/projects/automotive.kuksa)


