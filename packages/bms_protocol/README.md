# BMS CAN protocol decoder

当前实现来自 `BMS软硬件与上位机链路审阅.md` 第 16、19 节的已核对 CAN1 合同。`decode_can1_frame` 只解析，不发送；调用方必须传入方向，不能仅按 CAN ID 判断含义。

已覆盖：`0x100`、`0x101`、`0x104–0x127`、`0x128–0x133`、`0x139`、`0x140`。短帧返回 `partial`，未知方向/ID 返回 `unknownFrame`，不填充虚构数值。
