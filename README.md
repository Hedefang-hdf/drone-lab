# drone-lab · 无人机测试实验室

[![flight-tests](https://github.com/Hedefang-hdf/drone-lab/actions/workflows/test.yml/badge.svg)](https://github.com/Hedefang-hdf/drone-lab/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://github.com/Hedefang-hdf/drone-lab/blob/main/requirements.txt)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> 用软件测试的方法学无人机：SITL 实验、飞行测试用例、日志判读工具、自动化报告。

这是一个无人机学习实验室。用例化、工具化、自动化、报告化——应用到了无人机领域：**每一次仿真飞行都有测试用例，每一条飞行日志都有体检报告，每一个判读标准都写成了断言。**

## 这个仓库里有什么

| 目录 | 内容 |
|---|---|
| [`flytests/`](flytests/) | **飞行测试用例库**：5 组 TC 用例，pytest 断言 = 飞行的回归测试 |
| [`pytools/`](pytools/) | 日志数据层（`flightdata.py`）+ 体检报告生成（`ulog_report.py`） |
| [`gotools/`](gotools/) | **Go 工具**：纯 Go 解析 .ulg → 单文件 HTML 报告（零依赖二进制，见 [`docs/ulog-二进制格式-Go实现.md`](docs/ulog-二进制格式-Go实现.md)） |
| [`docs/`](docs/) | 学习笔记 + 设计说明：SITL 踩坑、PID 原理、日志判读、pytest 设计 |
| [`data/`](data/) | 真实飞行日志样例（PX4 SITL 悬停，36 秒） |
| [`scripts/`](scripts/) | 编排器：无人值守飞行测试流水线 `run_campaign.py` + 负向验证脚本 |

## 快速开始

```bash
# 1. 依赖（建议用虚拟环境）
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 跑飞行测试用例
make test

# 3. 给一条日志出人读的体检报告
make report ULOG=data/20261027-sitl-hover.ulg

# 4. 验证用例本身有效（收紧阈值，看是否翻转成 FAIL）
make repro

# 5. 无人值守飞行测试：起 SITL → 自动飞 → 取日志 → 跑用例 → 出报告
make campaign                 # 需要 PX4 SITL + mavsdk-grpc，见 docs/自动化飞行测试流水线.md

# 6. Go 版报告生成器：纯 Go 解析 .ulg，出一个带曲线的单文件 HTML
make ulog2html                # 产物在 reports/
make test-go                  # Go 单测（含与 Python 数值基准的交叉验证）
```

## 看得到的产物（不用装环境）

不想配环境，也能直接看结果——下面三份都是仓库里现成的自包含 HTML，点开即读：

| 产物 | 内容 |
|---|---|
| [`first-flight-report-original.html`](docs/artifacts/first-flight-report-original.html) | **首航原始报告**：SITL 自动起飞→悬停→降落，5 项体检 4 项 PASS，1 项 FAIL（高度漂移 0.51 m） |
| [`first-flight-report-rejudged.html`](docs/artifacts/first-flight-report-rejudged.html) | **同一份日志重判**：修正度量口径后漂移 0.047 m，全部 PASS —— 标准一个字没动，改的是测量方式 |
| [`ulog2html-sample-report.html`](docs/artifacts/ulog2html-sample-report.html) | **Go 版报告**：纯 Go 解析 .ulg，带手写 SVG 曲线，与 Python 版数值逐位一致 |

> 这两份首航报告对照着看，就是一次完整的测试工程复盘：
> **先怀疑标准，还是先怀疑测量？** 这一课的答案在 [`docs/artifacts/README.md`](docs/artifacts/README.md)。

## 核心设计：测量与判定分离

```
                    ┌──────────────────────────────┐
   .ulg 日志 ──────▶│  pytools/flightdata.py       │
                    │  FlightData —— 只测量，不判定 │
                    └──────────┬───────────────────┘
                               │ 同一份测量
                 ┌─────────────┴─────────────┐
                 ▼                           ▼
      ulog_report.py                 flytests/test_tc*.py
      人读的体检报告                  pytest 断言（机器判定）
```

只有一份测量代码，报告和用例消费同一份数据——永远不会出现"报告说 PASS、用例说 FAIL"。
详细设计见 [`docs/pytest-飞行回归测试.md`](docs/pytest-飞行回归测试.md)。

## 测试用例矩阵

| 编号 | 用例 | 判据 | 标记 |
|---|---|---|---|
| TC-001 | 悬停稳定性 | 悬停段 roll/pitch ≤ ±1°；高度漂移 ≤0.5 m | `hover` |
| TC-002 | 动力裕度 | 悬停油门 45%~55%；峰值 <95%；波动 <15% | `hover` |
| TC-003 | 振动水平 | 三轴角速度峰值 <30 °/s（>20 °/s 预警） | `vibration` |
| TC-004 | 系统消息 | 全程零 ERROR（一票否决） | `safety` |
| TC-005 | 日志完整性 | 必备 topic、时长 ≥5s、采样率 ≥50Hz、时间戳单调 | `integrity` |

样例日志的实测结果：

| 检查项 | 实测 | 标准 | 判定 |
|---|---|---|---|
| 姿态稳定性 | roll ±0.33° / pitch ±0.35° | ±1.0° | PASS |
| 高度保持 | 漂移 0.04 m（均高 2.50 m） | ≤0.5 m | PASS |
| 悬停油门 | 均值 48.9% / 峰值 49.8% | 45%~55% | PASS |
| 振动水平 | 角速度峰值 2.1 °/s | <30 °/s | PASS |
| 系统消息 | ERROR 0 / WARNING 1 | 零 ERROR | PASS |
| 日志完整性 | 4 topic 齐全，采样率 ~250Hz | — | PASS |

```
14 passed, 1 skipped in 1.0s
```

## 为什么这事儿值得做

传统判读：飞完 → 开 Flight Review → 看曲线 → 凭经验说"还行"。有三个硬伤：

- **不可重复**：同一条日志，不同人看结论可能不一样
- **不可批量**：50 条日志要人肉看 50 次，看到第 40 条注意力已经衰减
- **不可回归**：改了 PID 参数重新飞，谁判断这次比上次好？只能靠印象

把判据写成断言，这三点全部解决。**改一个参数，`make test` 跑一遍就知道有没有退化。**

## 里程碑

- [x] **M1** 仓库初始化：日志体检工具 `ulog_report.py` + 真实样例日志
- [x] **M2** 飞行测试用例库：5 组 pytest 用例 + 数据层重构 + 负向验证 → **飞行的回归测试**
- [x] **M2.1** CI 接入：GitHub Actions 三版本矩阵跑用例 + 负向验证守护断言有效性（[看运行记录](https://github.com/Hedefang-hdf/drone-lab/actions/workflows/test.yml)）
- [x] **M4** SITL 自动化流水线：MAVSDK 起飞 → 悬停 → 降落 → 取日志 → 跑用例 → 出 HTML 报告（[设计说明](docs/自动化飞行测试流水线.md)，含首航抓出的 4 个真问题）
- [x] **M3** Go 工具 `ulog2html`：**纯 Go** 解析 `.ulg` 二进制 → 单文件 HTML 报告（含 SVG 曲线）。与 Python 侧双实现交叉验证，同一份日志 9 项指标逐位一致（[协议踩坑记录](docs/ulog-二进制格式-Go实现.md)）

## 学习路线

1. **PX4 SITL 仿真环境** — 编译、机型配置、QGC 联调（[踩坑记录](docs/sitl-搭建踩坑.md)）
2. **日志与数据分析** — .ulg 结构、pyulog、判读标准（[判读方法](docs/ulog-判读.md)）
3. **控制原理** — PID 四环串级（[笔记](docs/pid-四环串级.md)）
4. **飞行测试用例设计** — 从需求到用例到判读到报告闭环（[设计说明](docs/pytest-飞行回归测试.md)）
5. **自动化** — MAVSDK 脚本化飞行、安全包络、无人值守流水线（[流水线设计](docs/自动化飞行测试流水线.md)）

## 环境说明

样例日志来自 PX4 SITL 仿真（`PX4_SIM_MODEL=sihsim_quadx`，36 秒定点悬停），非实飞数据；判读阈值参考 PX4 飞行复查（Flight Review）的公开标准做了简化，供学习交流使用。

## License

[MIT](LICENSE)
