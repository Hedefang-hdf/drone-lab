# flytests · 飞行测试用例库

**这是本仓库的核心资产**：把"怎么验一架无人机飞得好不好"写成结构化用例，
再用 pytest 对日志做断言——即"飞行的回归测试"。

## 用例编号规范

`TC-<三位编号>-<名称>.md`，每个用例包含：

- **前置条件**：机型、模式、环境（SITL / 台架 / 外场）
- **步骤**：可重复执行的操作序列
- **预期结果**：量化判据（不许写"飞行正常"这种模糊话）
- **数据来源**：验证用的日志 topic 与判读方法

## 现有用例

| 编号 | 名称 | 状态 |
|---|---|---|
| TC-001 | 悬停稳定性 | 用例就绪，pytest 断言在 M2 实现 |

## M2 目标：日志断言自动化

把 `docs/ulog-判读.md` 里的五项标准写成 pytest 断言：

```python
def test_roll_within_limits(hover_report):
    assert hover_report.roll_span / 2 <= 1.0, "横滚角超出悬停标准"

def test_throttle_in_margin(hover_report):
    assert 0.45 <= hover_report.throttle_mean <= 0.55, \
        "悬停油门偏离 50%，动力配比或桨效率异常"
```

之后每次仿真飞行 → 自动取日志 → `make test` → PASS/FAIL，
改参数、换机型都能立刻看到回归结果。
