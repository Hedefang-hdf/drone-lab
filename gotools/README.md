# gotools · Go 工具区

Go 编写的无人机工具，发挥 Go 的长处：**单二进制分发、零运行时依赖、快**。

## `cmd/ulog2html` — .ulg 日志 → 单文件 HTML 报告

```bash
# 在仓库根目录
make ulog2html                                  # 编译 + 给样例日志出报告（输出到 reports/）
make test-go                                    # 跑 Go 单测

# 或者直接跑
cd gotools
go run ./cmd/ulog2html ../data/20261027-sitl-hover.ulg -o ../reports/hover.html
```

产出一份自包含的 HTML：结论横幅、指标卡片、判定明细、**悬停段高度/姿态 SVG 曲线**、
系统消息、可追溯元信息。不引任何外部 CSS/JS/字体，离线可看、可直接发人。

退出码可直接串进流水线：

```bash
ulog2html flight.ulg || echo "体检没过，别提交"
```

## 三个包

| 包 | 职责 |
|---|---|
| `ulog/` | **纯 Go 的 ULog 二进制解析**：格式定义、订阅、数据解码、系统消息 |
| `hover/` | 判读：悬停窗口、指标测量、阈值判定（与 Python 侧 `pytools/flightdata.py` 同口径） |
| `report/` | 渲染：单文件 HTML + 手写 SVG 曲线 |

## 为什么值得重写一遍

Python 侧有 pyulog，功能也够用。但：

1. **分发**：编译出一个可执行文件，同事拿到日志就能出报告，不用装 Python/venv
2. **速度**：8.4MB / 9.5 万条数据消息，解析 + 判读 + 出 HTML 约 0.5 秒
3. **交叉验证**：两套独立实现算同一份日志，数字必须一致——
   对得上才说明**协议理解正确**，而不只是"代码符合我的理解"（见 `hover/metrics_test.go`）

> 早期计划的方案是「Python 用 pyulog 抽数据导出 JSON，Go 只做渲染」。
> 实际动手时改成纯 Go 解析：既然要证明 Go 能处理这套日志，就不该让它只当模板引擎。

协议踩坑记录（填充字段不落盘、msg_id 由文件指定、等级是 ASCII 数字等）：
[`docs/ulog-二进制格式-Go实现.md`](../docs/ulog-二进制格式-Go实现.md)

## 后续可做

- 批量出报告：一个目录 N 条日志 → 一个带索引的静态站点（Go 并发很适合）
- 多架次趋势对比：同一机型多次飞行，把关键指标画成趋势线
