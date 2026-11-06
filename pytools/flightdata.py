#!/usr/bin/env python3
"""flightdata.py — 飞行日志数据层

职责：**只负责"测量"，不负责"判定"。**

把 .ulg 读进来、定位稳定悬停段、算出所有可量化的指标，
统一交给 HoverMetrics。判定标准（阈值）由调用方提供：
  - pytools/ulog_report.py  → 生成人看的体检报告
  - flytests/test_*.py      → 生成机器判定的 PASS/FAIL

这样做的好处：同一条日志、同一份测量，报告和用例永不失同步。
面试可讲点：这是"测量与判定分离"，测试代码最忌讳把断言散落在数据读取里。

用法:
    from pytools.flightdata import FlightData
    fd = FlightData("data/20261027-sitl-hover.ulg")
    m = fd.metrics
    print(m.roll_half_deg, m.throttle_mean)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from pyulog import ULog


# ---------------------------------------------------------------- 判定阈值
# 参考 PX4 Flight Review 公开判读标准，做了面向悬停场景的简化。
# 全部集中在此处：改标准只改这一处，报告与用例同时生效。

@dataclass(frozen=True)
class Thresholds:
    roll_deg: float = 1.0           # 悬停段横滚角允许偏差（±）
    pitch_deg: float = 1.0          # 悬停段俯仰角允许偏差（±）
    alt_drift_m: float = 0.5        # 悬停高度漂移上限
    throttle_min: float = 0.45      # 悬停油门下限（归一化）
    throttle_max: float = 0.55      # 悬停油门上限
    gyro_dps: float = 30.0          # 角速度峰值上限，超出视为异常振动/震荡
    hover_min_alt_m: float = 0.8    # 高于此值视为"在空中"
    hover_max_vz_ms: float = 0.3    # 垂直速度小于此值视为"稳定悬停"
    hover_min_samples: int = 10     # 悬停段最少采样点

    # 日志完整性：判读所需的必备 topic
    required_topics: tuple = (
        "vehicle_attitude",
        "vehicle_local_position",
        "actuator_motors",
        "sensor_combined",
    )


DEFAULT = Thresholds()


# ---------------------------------------------------------------- 测量结果

@dataclass
class HoverMetrics:
    """一条日志的全部可测量结果。字段缺失时为 None，由调用方决定 SKIP 还是 FAIL。"""
    source: str
    duration_s: float = 0.0
    hover_found: bool = False
    hover_start_s: float | None = None
    hover_end_s: float | None = None
    hover_s: float = 0.0
    hover_samples: int = 0

    roll_half_deg: float | None = None      # 横滚角幅度的一半（即 ±x°）
    pitch_half_deg: float | None = None
    alt_drift_m: float | None = None
    alt_mean_m: float | None = None
    throttle_mean: float | None = None
    throttle_peak: float | None = None
    gyro_peak_dps: float | None = None

    errors: list = field(default_factory=list)     # [(level, message)]
    warnings: list = field(default_factory=list)
    missing_topics: list = field(default_factory=list)

    def summary(self) -> str:
        if not self.hover_found:
            return f"未检测到稳定悬停段（日志 {self.duration_s:.1f}s）"
        return (f"悬停 {self.hover_s:.1f}s ｜ 姿态 ±{self.roll_half_deg:.2f}°/±{self.pitch_half_deg:.2f}° "
                f"｜ 高度漂移 {self.alt_drift_m:.2f}m ｜ 油门 {self.throttle_mean * 100:.1f}% "
                f"｜ 角速度 {self.gyro_peak_dps:.1f}°/s")


# ---------------------------------------------------------------- 工具函数

def quat_to_euler_deg(w: float, x: float, y: float, z: float) -> tuple[float, float, float]:
    """四元数 → (roll, pitch, yaw)，单位度。"""
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def level_str(m) -> str:
    s = getattr(m, "log_level_str", None)
    return s() if callable(s) else str(s)


# ---------------------------------------------------------------- 数据层

class FlightData:
    """一条 .ulg 日志的只读视图。测量结果惰性计算并缓存。"""

    def __init__(self, path: str, thresholds: Thresholds = DEFAULT):
        self.path = str(path)
        self.th = thresholds
        self.ulog = ULog(self.path)
        self._metrics: HoverMetrics | None = None
        self._window: tuple[int, int] | None = None
        self._window_ready = False

    # ---- 基础访问 ----
    def topic(self, name: str):
        for d in self.ulog.data_list:
            if d.name == name:
                return d
        return None

    @property
    def duration_s(self) -> float:
        t0 = self.ulog.start_timestamp
        t1 = max((d.data["timestamp"][-1] for d in self.ulog.data_list), default=t0)
        return (t1 - t0) / 1e6

    # ---- 第一步永远是分段：定位稳定悬停窗口 ----
    @property
    def hover_window(self) -> tuple[int, int] | None:
        if self._window_ready:
            return self._window
        self._window_ready = True

        pos = self.topic("vehicle_local_position")
        if pos is None:
            return None
        z = pos.data["z"]           # NED，负值表示离地
        vz = pos.data.get("vz")
        if vz is None:
            return None

        mask = (z < -self.th.hover_min_alt_m) & (abs(vz) < self.th.hover_max_vz_ms)
        if int(mask.sum()) < self.th.hover_min_samples:
            return None

        ts = pos.data["timestamp"]
        self._window = (int(ts[mask][0]), int(ts[mask][-1]))
        return self._window

    def in_window(self, ts) -> list[bool]:
        w = self.hover_window
        if w is None:
            return [True] * len(ts)     # 无悬停段则退化为全程，由调用方标注 SKIP
        return [(w[0] <= t <= w[1]) for t in ts]

    # ---- 全部测量 ----
    @property
    def metrics(self) -> HoverMetrics:
        if self._metrics is not None:
            return self._metrics

        m = HoverMetrics(source=self.path, duration_s=self.duration_s)
        w = self.hover_window
        if w is not None:
            m.hover_found = True
            m.hover_start_s = (w[0] - self.ulog.start_timestamp) / 1e6
            m.hover_end_s = (w[1] - self.ulog.start_timestamp) / 1e6
            m.hover_s = (w[1] - w[0]) / 1e6
            m.hover_samples = sum(self.in_window(self.topic("vehicle_local_position").data["timestamp"]))

        # 缺失 topic 记录（供日志完整性用例使用）
        m.missing_topics = [t for t in self.th.required_topics if self.topic(t) is None]

        # 姿态
        att = self.topic("vehicle_attitude")
        if att is not None and w is not None:
            q = att.data
            rolls, pitches = [], []
            for keep, i in zip(self.in_window(q["timestamp"]), range(len(q["timestamp"]))):
                if not keep:
                    continue
                r, p, _ = quat_to_euler_deg(q["q[0]"][i], q["q[1]"][i], q["q[2]"][i], q["q[3]"][i])
                rolls.append(r)
                pitches.append(p)
            if rolls:
                m.roll_half_deg = (max(rolls) - min(rolls)) / 2
                m.pitch_half_deg = (max(pitches) - min(pitches)) / 2

        # 高度
        pos = self.topic("vehicle_local_position")
        if pos is not None and w is not None:
            zs = [v for keep, v in zip(self.in_window(pos.data["timestamp"]), pos.data["z"]) if keep]
            if zs:
                m.alt_drift_m = max(zs) - min(zs)
                m.alt_mean_m = -sum(zs) / len(zs)

        # 油门
        mot = self.topic("actuator_motors")
        if mot is not None and w is not None:
            ms = [v for keep, v in zip(self.in_window(mot.data["timestamp"]), mot.data["control[0]"]) if keep]
            if ms:
                m.throttle_mean = sum(ms) / len(ms)
                m.throttle_peak = max(ms)

        # 振动（取三轴角速度峰值）
        imu = self.topic("sensor_combined")
        if imu is not None and w is not None:
            peak = 0.0
            for axis in ("gyro_rad[0]", "gyro_rad[1]", "gyro_rad[2]"):
                vals = [abs(v) for keep, v in zip(self.in_window(imu.data["timestamp"]), imu.data[axis]) if keep]
                if vals:
                    peak = max(peak, max(vals))
            m.gyro_peak_dps = math.degrees(peak)

        # 系统消息（全程，不受悬停段限制）
        for msg in self.ulog.logged_messages:
            lv = level_str(msg)
            if lv == "ERROR":
                m.errors.append(msg.message)
            elif lv == "WARNING":
                m.warnings.append(msg.message)

        self._metrics = m
        return m

    # ---- 阈值判定辅助（供用例与报告共用，保证口径一致）----
    def check(self) -> dict[str, tuple[bool | None, str]]:
        """返回 {检查项: (是否通过 | None=SKIP, 说明)}。"""
        m, th = self.metrics, self.th
        out: dict[str, tuple[bool | None, str]] = {}

        if not m.hover_found:
            for k in ("姿态稳定性", "高度保持", "悬停油门", "振动水平"):
                out[k] = (None, "未检测到稳定悬停段")
        else:
            # 注意：pyulog 的数据是 numpy 数组，比较结果是 numpy.bool_，
            # 它和 Python 的 True/False 在 `is` 比较下并不相等。
            # 统一转成原生 bool，避免下游（pytest、CI、脚本）踩坑。
            out["姿态稳定性"] = (
                bool(m.roll_half_deg <= th.roll_deg and m.pitch_half_deg <= th.pitch_deg),
                f"roll ±{m.roll_half_deg:.2f}° pitch ±{m.pitch_half_deg:.2f}° / 标准 ±{th.roll_deg:.0f}°",
            )
            out["高度保持"] = (
                bool(m.alt_drift_m <= th.alt_drift_m),
                f"漂移 {m.alt_drift_m:.2f} m（均高 {m.alt_mean_m:.2f} m）/ 标准 {th.alt_drift_m:.1f} m",
            )
            out["悬停油门"] = (
                bool(th.throttle_min <= m.throttle_mean <= th.throttle_max),
                f"均值 {m.throttle_mean * 100:.1f}% 峰值 {m.throttle_peak * 100:.1f}% / "
                f"标准 {th.throttle_min * 100:.0f}%~{th.throttle_max * 100:.0f}%",
            )
            out["振动水平"] = (
                bool(m.gyro_peak_dps <= th.gyro_dps),
                f"角速度峰值 {m.gyro_peak_dps:.1f} °/s / 标准 {th.gyro_dps:.0f} °/s",
            )

        out["系统消息"] = (
            bool(len(m.errors) == 0),
            f"ERROR {len(m.errors)} 个 / WARNING {len(m.warnings)} 个",
        )
        return out
