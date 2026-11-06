"""TC-005 日志完整性 —— 先验证数据本身可用

判定口径：必备 topic 齐全、时长足够、采样率达标、时间戳单调。

为什么要有这一组：
    后面所有判读都建立在这条日志上。如果日志本身是残缺的
    （丢 topic、采样率过低、时间戳回退），那么"PASS"是不可信的。
    **先证明数据有效，再谈数据说明什么** —— 这是测试的第一性原理，
    也是最容易被跳过的一步。

面试可讲点：
    "我的用例在断言性能之前，先断言前提成立。否则一个残缺日志
    也能跑出 PASS，这种 PASS 比 FAIL 更危险。"
"""

from __future__ import annotations

import pytest


@pytest.mark.integrity
def test_required_topics_present(flight, m, th):
    """判读所需的必备 topic 全部存在。"""
    assert not m.missing_topics, (
        f"{m.source}: 缺少必备 topic {m.missing_topics}。"
        f"必备清单：{list(th.required_topics)}。"
        f"常见原因：PX4 日志配置未开启该 topic、或日志被截断。"
    )


@pytest.mark.integrity
def test_log_duration(m):
    """日志时长至少 5 秒（太短无法覆盖一个完整的悬停周期）。"""
    assert m.duration_s >= 5.0, (
        f"{m.source}: 日志仅 {m.duration_s:.1f}s，短于 5s，不足以判读悬停质量。"
    )


@pytest.mark.integrity
def test_attitude_sample_rate(flight):
    """姿态 topic 采样率应 ≥50 Hz（PX4 默认输出 250 Hz）。"""
    att = flight.topic("vehicle_attitude")
    assert att is not None, "缺少 vehicle_attitude"
    ts = att.data["timestamp"]
    assert len(ts) >= 2, "vehicle_attitude 采样点不足"
    span_s = (ts[-1] - ts[0]) / 1e6
    rate = (len(ts) - 1) / span_s if span_s > 0 else 0
    assert rate >= 50, (
        f"{flight.path}: 姿态采样率 {rate:.0f} Hz，低于 50 Hz。"
        f"采样率过低会漏掉高频震荡，振动判定将不可信。"
    )


@pytest.mark.integrity
def test_timestamps_monotonic(flight):
    """时间戳必须单调不减（回退说明日志损坏或时钟异常）。"""
    for name in ("vehicle_attitude", "vehicle_local_position"):
        d = flight.topic(name)
        if d is None:
            continue
        ts = d.data["timestamp"]
        bad = [i for i in range(1, len(ts)) if ts[i] < ts[i - 1]]
        assert not bad, (
            f"{name}: 时间戳在 {len(bad)} 处发生回退（首个位置 index={bad[0]}），日志可能损坏。"
        )
