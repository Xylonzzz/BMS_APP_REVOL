# bms-contracts

跨 Windows、Android、浏览器和微信小程序共享的 BMS 数据合同。`quality` 和 `valid` 是必需的质量状态，不能由客户端省略或自行推断。

当前模拟快照使用 144 软件电压槽、136 物理槽；`physicalValid=false` 表示保留槽。`physicalValid` 缺失时，客户端为兼容旧记录将其纳入非保留槽统计；这不证明实物存在或物理有效性。温度扩展特征、PEC、时间和序号是模拟证据，不表示真实固件已上送这些字段。`totalVoltage` 单位 mV，表示软件槽累压；`current` 单位 A。

可选 `BmsSnapshot.packStatus` 保存整包状态，五个字段均可选：`totalVoltageAdc`（ADC 总压，V）、`totalVoltageAccumulated`（状态帧累压，V）、`prechargeVoltage`（ADC 预充/负载侧电压，V）、`current`（电流，A，放电正/充电负）、`soc`（%）。每项携带 `BmsValue<number | null>` 的质量、采样时间、序号、原始帧 ID，以及显式 `unit`。共享聚合从 0x100/0x101 解码，分别将原始 0.1 V、mA、0.1% 换算为 V、A、%；短帧保留 `partial` 和不可解析的 `null`，缺帧不构造数字。旧 `totalVoltage` 仍表示 mV 软件槽累压，不能作为 ADC 总压证据。

新模拟快照包含 36 组单体帧及 0x100/0x101 两帧。ADC 总压、预充电压和 SOC 使用独立模拟输入，累压沿现有软件槽求和口径以 0.1 V 编码；模拟质量仍属于 `qualityMetadataSource=simulation` 帧外元数据。超时/断线记录保留前一次 `packStatus` 数字、采样时间和序号，标记 `stale`/`disconnected`，不生成新帧。API 对历史记录仅补充实际状态帧可解析的字段；没有状态帧也没有显式状态字段时，`packStatus` 保持缺失，不改写原始 JSONL。

`CellVoltage.moduleIndex` 与 `channelIndex` 是可选的非负整数，分别表示从 0 开始的模组索引和模组内软件通道索引。当前模拟器声明 8 个模组，每模组通道 0–16 为物理电芯、通道 17 为保留槽；`cellIndex` 继续表示全局软件槽索引。客户端优先使用显式 `moduleIndex` 分组，不固定模组数；历史记录未声明该字段时，仅完整匹配 `slave_XX` 或 `simulated_slave_XX` 的已知来源格式可作为兼容分组依据，其中数字为从 1 开始的从控编号。其他来源显示“未分组”，不按槽数猜测拓扑；缺失的 `channelIndex` 不推导，显示全局软件槽编号。这些字段是快照元数据，不增加或修改原始 CAN 载荷。

`CanFrame.direction` 相对 PC：rx 为固件上行，tx 为 PC 发送。`qualityMetadataSource=simulation` 与 `signalQualities` 仅用于帧外质量注入。API 可附加 `decoded`；原始 `data` 不改变。快照新增字段保持可选以便读取历史证据，缺失不代表已知。

JSON Schema 入口为 `schemas/signal-tree.json` 和 `schemas/replay-response.json`；运行 `tools/check_contracts.py` 验证实例和引用。
