"""TC-001 悬停稳定性 —— 姿态与高度

对应文档：flytests/TC-001-悬停稳定性.md
判定口径：只对"稳定悬停段"统计（排除起飞爬升与降落过程）。

为什么不能整段统计：
    整条日志混着起飞、悬停、降落。把全程的 roll/pitch 取极值，
    爬升时的俯仰前倾和降落时的姿态变化都会被算进去，
    结果就是"飞机很稳却判 FAIL"。所以先分段，再判读。
"""

from __future__ import annotations

import pytest


@pytest.mark.hover
def test_hover_segment_exists(m):
    """必须存在可判读的稳定悬停段（否则本用例组没有意义）。"""
    assert m.hover_found, (
        f"{m.source}: 未检测到稳定悬停段（判据：离地 >0.8 m 且 |vz| <0.3 m/s，"
        f"至少 10 个采样点）。日志时长 {m.duration_s:.1f}s。"
    )
    assert m.hover_s >= 3.0, (
        f"{m.source}: 悬停段仅 {m.hover_s:.1f}s，短于 3s 不足以判读稳定性。"
    )


@pytest.mark.hover
def test_roll_stability(m, th):
    """横滚角偏差在 ±1° 以内（悬停时飞机不应左右晃）。"""
    assert m.roll_half_deg is not None, "日志缺少 vehicle_attitude，无法判定姿态"
    assert m.roll_half_deg <= th.roll_deg, (
        f"{m.source}: 横滚 ±{m.roll_half_deg:.2f}°，超出标准 ±{th.roll_deg:.1f}°。"
        f"排查方向：横滚方向 PID 的 P 过大（等幅震荡）或 D 过小（回中慢）。"
    )


@pytest.mark.hover
def test_pitch_stability(m, th):
    """俯仰角偏差在 ±1° 以内。"""
    assert m.pitch_half_deg is not None, "日志缺少 vehicle_attitude，无法判定姿态"
    assert m.pitch_half_deg <= th.pitch_deg, (
        f"{m.source}: 俯仰 ±{m.pitch_half_deg:.2f}°，超出标准 ±{th.pitch_deg:.1f}°。"
        f"排查方向：重心前后偏移会让悬停俯仰角持续偏置，先查重心再看 PID。"
    )


@pytest.mark.hover
def test_altitude_hold(m, th):
    """悬停高度漂移在 0.5 m 以内（定高能力）。"""
    assert m.alt_drift_m is not None, "日志缺少 vehicle_local_position，无法判定高度"
    assert m.alt_drift_m <= th.alt_drift_m, (
        f"{m.source}: 高度漂移 {m.alt_drift_m:.2f} m（均高 {m.alt_mean_m:.2f} m），"
        f"超出标准 {th.alt_drift_m:.1f} m。"
        f"排查方向：气压计受气流干扰、定高环 I 过大导致过冲、或地效区悬停。"
    )
