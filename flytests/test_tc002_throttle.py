"""TC-002 动力裕度 —— 悬停油门

判定口径：稳定悬停段的平均油门应落在 45%~55%。

为什么要卡这个区间：
    悬停油门就是"飞机用多少力才停在半空"的量化。
      - 明显偏低（<45%）：桨效虚标或整机偏轻，抗风余量虚高，一遇阵风就掉高；
      - 明显偏高（>55%）：已经用掉一半以上推力，机动、抗风、爬升全部没余量，
        电池衰减或挂载增加后直接飞不起来。
    50% 左右意味着"还有一半推力可以用于机动"，这是设计合理的标志。
"""

from __future__ import annotations

import pytest


@pytest.mark.hover
def test_throttle_in_range(m, th):
    """悬停平均油门在 [45%, 55%]。"""
    assert m.hover_found, f"{m.source}: 未检测到稳定悬停段，无法判定油门"
    assert m.throttle_mean is not None, "日志缺少 actuator_motors，无法读取油门"
    assert th.throttle_min <= m.throttle_mean <= th.throttle_max, (
        f"{m.source}: 悬停平均油门 {m.throttle_mean * 100:.1f}%，"
        f"超出标准 {th.throttle_min * 100:.0f}%~{th.throttle_max * 100:.0f}%。"
        f"{'推力余量不足，抗风与机动能力受限' if m.throttle_mean > th.throttle_max else '推力过剩或整机偏轻，悬停易漂'}。"
    )


@pytest.mark.hover
def test_throttle_peak_headroom(m, th):
    """油门峰值不应触顶（>95% 说明动力已被榨干）。"""
    assert m.throttle_peak is not None, "日志缺少 actuator_motors，无法读取油门"
    assert m.throttle_peak < 0.95, (
        f"{m.source}: 悬停段油门峰值 {m.throttle_peak * 100:.1f}%，已接近满油门。"
        f"悬停中偶发的高油门说明存在瞬时失控风险（阵风、姿态修正过量）。"
    )


@pytest.mark.hover
def test_throttle_steady(m, th):
    """油门波动不应过大（均值与峰值差 <15%，反映控制回路平稳）。"""
    assert m.throttle_peak is not None and m.throttle_mean is not None
    spread = m.throttle_peak - m.throttle_mean
    assert spread < 0.15, (
        f"{m.source}: 悬停油门波动 {spread * 100:.1f}%"
        f"（均值 {m.throttle_mean * 100:.1f}% → 峰值 {m.throttle_peak * 100:.1f}%），"
        f"超过 15% 说明高度控制不平稳，查定高环与电调响应。"
    )
