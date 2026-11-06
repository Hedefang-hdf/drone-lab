# flytests · 飞行测试用例库

**这是本仓库的核心资产**：把"怎么验一架无人机飞得好不好"写成结构化用例，
再用 pytest 对日志做断言——即**飞行的回归测试**。

设计说明见 [`docs/pytest-飞行回归测试.md`](../docs/pytest-飞行回归测试.md)。

## 怎么跑

```bash
make test          # 全部用例（日志自动发现，data/*.ulg 每一条都跑一遍）
make test-hover    # 只跑悬停稳定性与动力类
make test-safety   # 只跑安全类（一票否决项）
make repro         # 负向验证：收紧阈值，确认断言真的有效
```

输出示例：

```
flytests/test_tc001_hover_stability.py::test_roll_stability[20261027-sitl-hover] PASSED
...
14 passed, 1 skipped in 1.0s
```

## 用例编号规范

`TC-<三位编号>-<名称>.md` 是**用例文档**（人读），`test_tc<编号>_<主题>.py` 是它的**可执行实现**（机器判定）。

每个用例文档包含：

- **前置条件**：机型、模式、环境（SITL / 台架 / 外场）
- **步骤**：可重复执行的操作序列
- **预期结果**：量化判据（不许写"飞行正常"这种模糊话）
- **数据来源**：验证用的日志 topic 与判读方法

## 用例矩阵

| 编号 | 名称 | 判据 | 实现 | 标记 |
|---|---|---|---|---|
| TC-001 | 悬停稳定性 | 悬停段 roll/pitch ≤ ±1°；高度漂移 ≤0.5 m | `test_tc001_hover_stability.py` | `hover` |
| TC-002 | 动力裕度 | 悬停油门 45%~55%；峰值 <95%；波动 <15% | `test_tc002_throttle.py` | `hover` |
| TC-003 | 振动水平 | 三轴角速度峰值 <30 °/s，>20 °/s 预警 | `test_tc003_vibration.py` | `vibration` |
| TC-004 | 系统消息 | 全程零 ERROR（WARNING 记录不判失败） | `test_tc004_sysmsg.py` | `safety` |
| TC-005 | 日志完整性 | 必备 topic 齐全、时长 ≥5s、采样率 ≥50Hz、时间戳单调 | `test_tc005_log_integrity.py` | `integrity` |

## 三条写用例的规矩

1. **只统计悬停段**。整条日志混着起飞、悬停、降落；对全程取极值会把爬升时的姿态变化算进去，导致误判。分段逻辑在 `pytools/flightdata.py` 的 `hover_window`。
2. **断言消息要能直接当诊断报告**。写清实测值、标准值、排查方向，不写"失败"两个字了事。
3. **先验证数据有效，再验证性能达标**。TC-005 是前置校验——一条残缺的日志跑出 PASS，比 FAIL 更危险。

## 新增用例的流程

1. 在 `flytests/` 写 `TC-00X-名称.md`（判据必须量化）
2. 在 `pytools/flightdata.py` 的 `HoverMetrics` 加测量字段（如果现有字段不够）
3. 写 `test_tc00X_*.py`，标记对应的 marker
4. 在 `pytest.ini` 里注册新 marker（`--strict-markers` 会拦住未注册的）
5. `make test` 全绿后，`make repro` 加一条收紧阈值的验证
