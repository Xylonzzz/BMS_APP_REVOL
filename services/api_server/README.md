# API Server

启动只读模拟 API：

```powershell
python services/api_server/server.py --port 8765 --data-dir runtime/live-check/current
```

接口：

- `GET /api/v1/health`
- `GET /api/v1/capture`（接收状态、错误、累计帧数）
- `GET /api/v1/catalog`（字段名→路径/标签/工程单位/除数）
- `GET /api/v1/snapshots/latest`
- `GET /api/v1/snapshots?offset=0&limit=100`
- `GET /api/v1/diagnostics`（最新快照事件）
- `GET /api/v1/sessions`
- `GET /api/v1/sessions/{id}/snapshots?offset=0&limit=100`
- `GET /api/v1/sessions/{id}/export?kind=summary|full|snapshot`
- `GET /api/v1/schema`
- `GET /api/v1/replay/scenarios`
- `GET /api/v1/replay/scenarios/{name}`
- `GET /api/v1/ai/providers`
- `POST /api/v1/ai/analyze`（默认本地规则，仅分析已记录的最新快照）

API仅监听127.0.0.1并提供同源静态页面。模拟、文件和真实CAN分别选择 `--mode simulation|file|can`；文件使用 `--input`，CAN使用 `--dll`及设备参数。真实/文件采集要求新的空会话，不能将输入路径作为输出。所有模式均无BMS控制命令，CAN使用只听模式。

只有simulation空目录播种示例；file/can使用CaptureWorker连续接收。分页默认100、最多200，固定文件边界后不阻塞追加扫描；历史原文件保持不变。导出冻结完整行边界、保留质量及版本，完成或传输断开后清理临时文件。运行和验证详见 `docs/Windows与浏览器使用说明.md`。
