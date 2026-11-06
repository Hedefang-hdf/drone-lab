"""TC-003 振动水平 —— IMU 角速度

判定口径：悬停段三轴角速度峰值 <30 °/s。

为什么用角速度而不是加速度：
    角速度直接反映"机体在抖"。桨不平衡、电机轴承磨损、机架共振，
    在陀螺仪上表现为高频尖峰；而加速度计受重力和机动影响，噪声大。
    PX4 Flight Review 的振动判读也是以陀螺为主。

超过 30 °/s 的典型后果：
    姿态估计被噪声污染 → EKF 抖动 → 控制输出抖动 → 越抖越抖（正反馈）。
    再往上会触发 PX4 的振动补偿，甚至直接判"高振动"限制起飞。
"""

from __future__ import annotations

import pytest


@pytest.mark.vibration
def test_gyro_peak(m, th):
    """角速度峰值在 30 °/s 以内。"""
    assert m.hover_found, f"{m.source}: 未检测到稳定悬停段，无法判定振动"
    assert m.gyro_peak_dps is not None, "日志缺少 sensor_combined，无法读取陀螺数据"
    assert m.gyro_peak_dps <= th.gyro_dps, (
        f"{m.source}: 角速度峰值 {m.gyro_peak_dps:.1f} °/s，超出标准 {th.gyro_dps:.0f} °/s。"
        f"排查顺序：①桨叶是否平衡/变形 ②电机轴承异响 ③机架螺丝松动 "
        f"④飞控减震棉是否硬化 ⑤PID 的 D 项过大（D 项放大高频噪声）。"
    )


@pytest.mark.vibration
def test_gyro_not_oscillating(m, th):
    """峰值与标准之间要有余量：>20 °/s 视为振动偏大（预警而非不合格）。"""
    assert m.gyro_peak_dps is not None, "日志缺少 sensor_combined"
    if m.gyro_peak_dps > th.gyro_dps * 0.66:
        pytest.xfail(
            f"{m.source}: 角速度峰值 {m.gyro_peak_dps:.1f} °/s，"
            f"已达标准的 {m.gyro_peak_dps / th.gyro_dps * 100:.0f}%，属于振动偏大预警区。"
            f"尚未判 FAIL，但建议在下次飞行前做桨平衡检查。"
        )
