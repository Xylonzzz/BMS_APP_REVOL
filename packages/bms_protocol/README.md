# BMS CAN protocol decoder

当前实现来自 `BMS软硬件与上位机链路审阅.md` 第 16、19 节的已核对 CAN1 合同。`decode_can1_frame` 只解析，不发送；调用方必须传入方向，不能仅按 CAN ID 判断含义。

已覆盖：`0x100`、`0x101`、`0x104–0x127`、`0x128–0x133`、`0x139`、`0x140`。短帧返回 `partial`，未知方向/ID 返回 `unknownFrame`，不填充虚构数值。

解码必须具有明确 `bus=can1`、PC 视角 `direction=rx`、`ide=standard`、`rtr=false`；元数据缺失或不支持时保留原帧并返回 `unknown`。DLC、ID 或字节范围非法时返回 `range_error`，不会静默截断越界字节。模拟帧的 `signalQualities` 只有在 `qualityMetadataSource=simulation` 时才参与质量显示。

当前仍是离线演示合同；完整真实帧到快照的聚合器、适配器输入、固件版本获取及逐字段协议表对齐尚未完成。
