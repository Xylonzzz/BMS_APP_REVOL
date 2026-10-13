# BMS_APP_REVOL 重启推进计划

## Goal
交付可运行的 Windows 与浏览器候选产品，完成真实CAN/文件输入的软件链路、核心监控、记录回放/导出、只读诊断和显示档案；真实硬件由用户实测，准备具体联调步骤。

## Phases
- [complete] Phase 1: 协议合同与回放质量基线设计
- [complete] Phase 2: 扩展 Schema、协议解码和模拟场景
- [complete] Phase 3: API/浏览器证据链验证与文档更新
- [complete] Phase 3b: 2026-10-13 回放证据一致性与 Web 状态修复
- [complete] Phase 3c: 模组工作台视觉重设、动态分组与单体证据详情
- [complete] Phase 3d: 整包总压/累压/预充/电流/SOC合同、状态帧与总览补齐
- [complete] Phase 4a: ControlCAN/文件适配、连续聚合与记录、超时断线质量
- [complete] Phase 4b: 温度/状态/阈值/A03目录、会话分页/趋势/导出、只读诊断及显示档案
- [complete] Phase 4c: Tauri Windows安装/便携包、生命周期、最终发布验收；交付 `dist/windows-tauri/0.1.0-candidate-20261013-080259`
- [external_pending] 现场设备/驱动/固件确认及真实CAN联调；软件准备见《真实CAN只读联调》

## 后续设计要求
- [complete] REVOL 创意电池图标：电池轮廓+负空间R，网页SVG/favicon与Windows图标共用造型，已验收浅深主题和手机尺寸。

## Next Step
Windows与浏览器软件交付完成；使用说明和验收见 `docs/Windows与浏览器使用说明.md` 与 `docs/validation/2026-10-13-product-validation.md`。用户下一步确认现场设备/固件并按只读联调文档实测。原始历史记录保留。

## Errors Encountered
| Error | Attempt | Resolution |
|---|---:|---|
| 初次运行时尚无 Git | 历史 | 已失效；2026-10-13 起始基线为 main / fb80249，工作区干净 |


