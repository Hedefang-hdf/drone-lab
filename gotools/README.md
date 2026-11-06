# gotools · Go 工具区

这里放 Go 编写的无人机工具，发挥 Go 的长处：单二进制分发、并发、跨平台编译。

## 规划中的工具（里程碑 M3）

### `cmd/ulog2html` — ulog 日志 → HTML 报告站

```
用法（规划）:
  ulog2html data/20261027-sitl-hover.ulg -o reports/

产出:
  reports/20261027-sitl-hover/
    ├── index.html        # 总览：判定结果 + 关键指标卡片
    ├── attitude.html     # 姿态曲线（SVG 图，无外部依赖）
    ├── throttle.html     # 油门与电机输出
    └── messages.html     # 系统消息时间线
```

设计原则：

1. **单二进制**：Python 侧用 pyulog 抽取数据导出 JSON，Go 侧只做
   渲染与站点生成——职责切干净，两边各自好维护
2. **零外部依赖的图表**：手写 SVG 而不是嵌 Chart.js，保证离线可用
3. **报告即产物**：跑完测试顺手出报告，面试时直接给链接

为什么用 Go 做这一层：日志文件可能几百 MB、多机多架次批量出报告，
Go 的并发和内存占用比脚本语言稳；且编译出一个可执行文件，
同事拿去就能用，不用装 Python 环境。
