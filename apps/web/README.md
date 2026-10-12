# Web客户端

启动 API 后，在本目录运行静态服务器：

```powershell
python -m http.server 4173 --directory apps/web
```

浏览器打开 `http://127.0.0.1:4173`，页面默认读取 `http://127.0.0.1:8765`。可以切换 Engineering、Operations 和 Minimal 三种风格；PEC 错误单体会保留数值并使用红色无效状态显示。

顶部“回放”可以加载 `valid`、`pec_error`、`partial`、`timeout_stale`、`disconnected_reconnect`、`af`、`ef` 和 `mixed_model` 场景；“型号”可以模拟旧上位机的 AF/EF 手动覆盖。页面会显示自动/手动来源和识别原因。

界面采用三栏 Signal Workspace：左侧保留旧上位机导航，中间突出快照质量和单体网格，右侧显示原始帧、质量、AF/EF 判断和诊断证据。外观入口支持深石墨、苔绿、纸张、紧凑/舒适密度和动效关闭；这些选项只改变呈现方式，不改变有效/无效/断线/故障的颜色语义。

左侧导航参考旧上位机的 `BMS_STATE`、`Cell_Vol_1/2`、`Cell_Tem_1/2`、`realy_charge`、`Threshold_Value1`、`CAN` 和 `Test_Control` 页面。页面语义兼容旧软件，但不复制旧软件中未经验证的控制闭环。
