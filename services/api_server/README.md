# API Server

启动只读模拟 API：

```powershell
python services/api_server/server.py --port 8765 --data-dir runtime/demo
```

接口：

- `GET /api/v1/health`
- `GET /api/v1/snapshots/latest`
- `GET /api/v1/snapshots`
- `GET /api/v1/diagnostics`
- `GET /api/v1/schema`

第一阶段只使用模拟数据，不发送任何 BMS 控制命令。
