# BMS_APP_REVOL 产品方案与需求

## 1. 目标

重新设计一款面向 BMS 的多端数据平台。它需要同时服务于工装调试、日常监控、故障定位、数据记录、远程查看和后续 Vibe Coding 扩展。

产品名称暂定为 `BMS_APP_REVOL`。它既包含传统上位机能力，也包含 API Server、Edge Agent、数据存储和诊断 Agent，因此更准确的定位是“BMS 监控、诊断、数据记录与运维平台”。

## 2. 已确认需求

1. 显示嵌入式 BMS 固件版本号，并记录该版本与每次数据会话的关系。
2. 能接入服务器，支持本地模式、远程模式和断线后同步。
3. 内置 Agent，使用结构化 BMS 数据、链路文档和问题清单进行实时监视与初步定位。
4. 支持半屏、全屏和多窗口布局。
5. 一键导出简易数据包和全局数据包。简易包包含总压、累压、预充、电流、继电器、IMD、状态和关键告警；全局包保留原始 CAN、解析结果、A03 数据、质量状态、版本和诊断事件。
6. 删除历史遗留且没有使用价值的旧功能。
7. 修改并完善 AF/EF 自动识别方法。
8. 原始 CAN 报文与解析含义一一对照显示。
9. PEC 校验失败的数据仍可上送和显示，但必须标红或使用统一的无效状态；不能把显示值当成本轮有效采样。
10. 显示 A03 等结构体数据，并能看到字段、单位、来源、时间和有效性。
11. 支持 Windows 软件、Android 软件、浏览器访问和微信小程序。
12. 为用户提供主题、布局、信息密度、动画和交互行为的定制空间。

## 3. 多端边界

### 原上位机界面兼容基线

新界面以现有 C# WinForms 上位机的页面语义和操作路径为基线：`BMS_STATE`、`Cell_Vol_1/2`、`Cell_Tem_1/2`、`realy_charge`、`Threshold_Value1`、`CAN` 和 `Test_Control`。第一阶段浏览器壳已经将这些页面映射为导航项，并保留“BMS状态、单体电压、单体温度、继电器—充电状态、阈值、CAN收发、诊断与Agent”的入口。

兼容的含义是保留用户熟悉的字段、单位、页面名称和工作分区，不是复制旧 WinForms 的实现缺陷。新页面必须额外显示原始帧、固件版本、采集时间、质量状态、序号和证据链；原上位机中只读、未接通或语义不可靠的按钮，在新系统中先标注为只读或未实现，不自动伪造控制闭环。

### Windows

Windows 是完整能力端。它可以运行 Tauri 桌面端和 Python Edge Agent，接入 USB-CAN、串口、文件回放和调试工具，提供全部工装和诊断功能。

### Android

Android 主要用于移动监控、现场诊断、告警和远程查看。接入 CAN 时优先使用蓝牙 CAN、局域网网关或服务端，不把 Python Edge Agent 作为 Android 的必需组件。需要直接访问硬件时，应单独实现 Android 原生插件并定义与 `bms-client` 相同的接口。

### 浏览器

浏览器版本通过 HTTPS、WebSocket 或 SSE 访问 API Server。浏览器不直接依赖 Tauri IPC，也不假设能够访问用户电脑的 CAN 适配器。

### 微信小程序

小程序使用 Taro 编译，主要提供设备、会话、告警、快照、趋势和诊断摘要。小程序不能直接读取桌面 CAN，需要通过服务器或已联网网关访问。

## 4. 技术架构

```text
CAN/串口/USB/蓝牙网关
          ↓
    Edge Agent
          ↓
协议解析、PEC、AF/EF、快照、回放
          ↓
       BMS API
          ↓
┌─────────┼─────────┬─────────┐
│         │         │         │
Tauri   Android    Web      Taro
Windows  App       App      小程序
          ↓
   Server / Sync / Agent
```

建议使用：

- Windows 桌面：Tauri 2 + TypeScript。
- Android：Tauri 2 或独立 Android 客户端，按硬件接入需要选择。
- Web：React + TypeScript。
- 微信小程序：Taro。
- 本地采集：Python Edge Agent。
- API：REST + WebSocket + SSE，并用 OpenAPI 描述。
- 本地索引：SQLite。
- 原始证据：不可变 CAN/AIM/JSONL/Parquet 文件。
- 服务端索引：PostgreSQL。
- 长时间序列：需要车队趋势时再增加 InfluxDB/Grafana。
- Agent：结构化诊断服务 + DeepSeek/Harness，初期只读分析。

## 5. Vibe Coding 设计

Vibe Coding 需要让模型能够稳定理解、调用和扩展系统。因此实现顺序应是：

```text
需求
  → OpenAPI / Event Schema
  → TypeScript/Python类型
  → 协议解析和诊断工具
  → 页面组件
  → 平台适配
```

必须维护以下可机器读取的合同：

- `schemas/openapi.yaml`：设备、会话、快照、诊断、用户和权限 API。
- `schemas/websocket-events.json`：实时帧、数据更新、错误和 Agent 事件。
- `schemas/signal-tree.json`：BMS 信号树、A03 字段、单位和质量状态。
- 协议字典：CAN ID、总线、方向、DLC、端序、单位、版本和字段位置。
- 诊断规则：输入、前置条件、判断、证据和输出，不把 LLM 当作唯一判据。

建议提供 API Explorer、Schema 文档、事件回放和模拟数据生成器，让模型或开发者可以在没有实车的情况下开发和验证页面。

## 6. 数据质量与证据

每个采集值应带有：

```typescript
type BmsValue<T> = {
  value: T
  timestamp: number
  source: string
  firmwareVersion?: string
  sequence?: number
  valid: boolean
  quality: "valid" | "pec_error" | "timeout" | "partial" | "stale" | "range_error"
  rawFrameIds?: number[]
}
```

PEC 错误时保留原始值，但页面必须显示质量状态。Agent 读取 `quality`、原始帧、时间和序号后再分析，不能根据“页面有数字”判断采集成功。

## 7. Agent 边界

Agent 的第一阶段能力：

- 监视数据新鲜度、PEC、超时、范围、序号和逐槽有效性。
- 识别预充、继电器、IMD、CAN、从控和参数链路的异常组合。
- 引用 H01–H10、A01–A04 链路文档给出初步定位。
- 输出事实、证据、假设、可能影响和最小验证步骤。
- 生成快照摘要和问题记录草稿。

第一阶段不允许 Agent 自动发送继电器、充电或高压控制命令。控制能力以后必须拥有独立权限、确认和反馈闭环。

## 8. 用户定制化和视觉系统

### 主题

提供浅色、深色、低亮度、工业高对比度主题，并允许修改主色、背景、字体、圆角和边框。颜色语义统一：有效、无效、过期、警告、故障和未连接不能被用户配置成互相混淆的颜色。

### 布局

页面由可注册面板组成：关键状态、总压/电流、预充、继电器、单体电压、温度、CAN 原始帧、A03 结构体、诊断、日志和 Agent。用户可以显示、隐藏、排序、调整尺寸并保存为配置档案。

### 动画

动画用于页面切换、卡片状态变化、连接状态和告警进入。动画支持开关和低动态模式，不能阻塞采集、解析或发送，也不能隐藏质量错误。

### 配置文件

```typescript
type UserViewProfile = {
  id: string
  name: string
  theme: string
  density: "compact" | "comfortable" | "large"
  panels: Array<{ id: string; visible: boolean; order: number; size: string }>
  animation: "full" | "reduced" | "off"
  units: "metric" | "engineering"
  refreshRateMs: number
}
```

配置支持导入、导出、版本迁移、恢复默认和按用户角色下发。

## 9. 数据存储

本地使用 SQLite 保存设备、会话、文件、索引、诊断结果和用户视图配置。原始 CAN、AIM、快照和回放文件以不可变文件保存，并在 SQLite 中记录哈希、大小、版本和来源。

服务端 PostgreSQL 保存用户、权限、设备、固件、会话和文件索引；原始大文件放到对象存储。需要长期趋势、聚合查询和告警时再使用 InfluxDB/Grafana。不要把所有原始 CAN 帧直接堆进 PostgreSQL，也不要用 LLM 生成的摘要替代原始证据。

## 10. 开发阶段

### 阶段一：数据合同和模拟器

完成 OpenAPI、事件 Schema、信号树、A03 类型、质量状态、模拟 CAN 数据和回放格式；保留旧上位机 AF/EF 页面语义，自动识别使用电压边界槽与温度槽证据，并支持手动覆盖。

### 阶段二：Windows MVP

完成 Python Edge Agent、CAN 接入、协议解析、PEC 状态、快照、Tauri 界面和原始帧/解析值对照。

### 阶段三：诊断和定制化

完成 H01–H10、A01–A04 规则接入、Agent 只读分析、主题、布局、密度、动画和配置档案。

### 阶段四：Web 和 Android

完成 API Server、WebSocket、浏览器端、Android 端和断线重连。

### 阶段五：微信小程序

使用 Taro 完成远程查看、告警、快照、趋势和诊断摘要，处理小程序网络域名、登录和权限限制。

### 阶段六：服务器同步和长期分析

增加 PostgreSQL、对象存储、增量同步、InfluxDB/Grafana 和多设备管理。

## 11. 验证要求

- 所有客户端使用同一份模拟会话验证数据含义。
- 每个重要页面都能跳转到原始帧、质量状态和解析规则。
- PEC 错误、超时、部分更新和旧值保留都有可重复的回放样例。
- 同一 `UserViewProfile` 在 Web、Windows 和 Android 上能迁移，平台专属面板允许降级。
- 小程序断网、权限不足、服务器不可用时显示明确状态。
- Agent 输出必须保存输入证据、规则版本、模型版本和生成时间。

## 12. 参考方案

- [Tauri 官方介绍](https://v2.tauri.app/start/)
- [Tauri 分发](https://v2.tauri.app/distribute/)
- [Tauri sidecar](https://v2.tauri.app/develop/sidecar/)
- [Taro 多端介绍](https://docs.taro.zone/docs/1.x/)
- [Taro 跨平台差异](https://docs.taro.zone/docs/envs)
- [Vector CANalyzer](https://www.vector.com/int/en/products/products-a-z/software/canalyzer/)
- [SavvyCAN 文档](https://www.savvycan.com/docs/)
- [Eclipse KUKSA](https://projects.eclipse.org/projects/automotive.kuksa)
