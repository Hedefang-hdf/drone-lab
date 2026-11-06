#!/usr/bin/env python3
"""ulog_report.py — PX4 .ulg 飞行日志体检报告

读取一条 PX4 飞行日志（.ulg），自动定位"稳定悬停段"，
只对悬停段做质量判定并输出报告。判定不通过时以非零码退出，
方便后续接入 CI / pytest。

设计要点：判读前必须先分段。整段日志混着起飞爬升与降落，
直接对全程统计会把起降过程误判为"高度漂移"——先选段，再判读。

用法:
    python3 ulog_report.py <file.ulg>
    make report ULOG=data/20261027-sitl-hover.ulg
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field

try:
    from pyulog import ULog
except ImportError:
    print("缺少依赖：请先执行  pip install pyulog")
    sys.exit(2)


# ---------------------------------------------------------------- 判定标准
# 阈值参考 PX4 Flight Review 公开判读标准，做了面向悬停场景的简化。

TH_ROLL_DEG = 1.0            # 悬停段横滚角幅度
TH_PITCH_DEG = 1.0           # 悬停段俯仰角幅度
TH_ALT_DRIFT_M = 0.5         # 悬停高度漂移
TH_THROTTLE = (0.45, 0.55)   # 悬停油门区间（归一化），50% 留一半余量
TH_GYRO_DPS = 30.0           # 角速度峰值，超出视为异常振动/震荡

HOVER_MIN_ALT_M = 0.8        # 高于此值视为"在空中"
HOVER_MAX_VZ_MS = 0.3        # 垂直速度小于此值视为"稳定悬停"
HOVER_MIN_SAMPLES = 10       # 悬停段最少采样点，太少则无法判读


@dataclass
class CheckResult:
    name: str
    value: str
    verdict: str          # PASS / FAIL / SKIP
    detail: str = ""

    @property
    def icon(self) -> str:
        return {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[self.verdict]


@dataclass
class Report:
    source: str
    duration_s: float = 0.0
    hover_s: float = 0.0
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.verdict != "FAIL" for c in self.checks)

    def render(self) -> str:
        lines = [
            "=" * 62,
            f"飞行日志体检报告  {self.source}",
            f"日志时长 {self.duration_s:.1f} s ｜ 稳定悬停段 {self.hover_s:.1f} s",
            "=" * 62,
        ]
        for c in self.checks:
            lines.append(f"{c.icon} {c.name:<14} {c.value:<28} {c.detail}")
        verdict = "总体判定：PASS ✓" if self.passed else "总体判定：FAIL ✗（存在未达标项）"
        lines += ["-" * 62, verdict]
        return "\n".join(lines)


# ---------------------------------------------------------------- 工具函数

def quat_to_euler_deg(w: float, x: float, y: float, z: float) -> tuple[float, float, float]:
    """四元数 → (roll, pitch, yaw)，单位度。"""
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def find_topic(ulog: ULog, name: str):
    for d in ulog.data_list:
        if d.name == name:
            return d
    return None


def level_str(m) -> str:
    s = getattr(m, "log_level_str", None)
    return s() if callable(s) else str(s)


# ---------------------------------------------------------------- 主分析

def analyze(path: str) -> Report:
    ulog = ULog(path)
    report = Report(source=path)

    t0 = ulog.start_timestamp
    t1 = max((d.data["timestamp"][-1] for d in ulog.data_list), default=t0)
    report.duration_s = (t1 - t0) / 1e6

    # ---- 第一步：定位稳定悬停段（在空中 + 垂直速度近零）----
    pos = find_topic(ulog, "vehicle_local_position")
    window = None
    if pos is not None:
        pt = pos.data["timestamp"]
        z = pos.data["z"]        # NED，负值表示离地
        vz = pos.data.get("vz")
        if vz is not None:
            mask = (z < -HOVER_MIN_ALT_M) & (abs(vz) < HOVER_MAX_VZ_MS)
            if int(mask.sum()) >= HOVER_MIN_SAMPLES:
                window = (pt[mask][0], pt[mask][-1])
                report.hover_s = (window[1] - window[0]) / 1e6

    def in_window(ts) -> list[bool]:
        if window is None:
            return [True] * len(ts)     # 无悬停段则退化为全程（各项会单独说明）
        return [(window[0] <= t <= window[1]) for t in ts]

    def skip(name: str, why: str) -> None:
        report.checks.append(CheckResult(name, "-", "SKIP", why))

    # ---- 姿态：四元数 → 欧拉角（仅悬停段）----
    att = find_topic(ulog, "vehicle_attitude")
    if att is None:
        skip("姿态稳定性", "日志缺少 vehicle_attitude")
    elif window is None:
        skip("姿态稳定性", "未检测到稳定悬停段")
    else:
        q = att.data
        sel = in_window(q["timestamp"])
        rolls, pitches = [], []
        for keep, i in zip(sel, range(len(q["timestamp"]))):
            if not keep:
                continue
            r, p, _ = quat_to_euler_deg(
                q["q[0]"][i], q["q[1]"][i], q["q[2]"][i], q["q[3]"][i])
            rolls.append(r)
            pitches.append(p)
        roll_span = max(rolls) - min(rolls)
        pitch_span = max(pitches) - min(pitches)
        ok = roll_span / 2 <= TH_ROLL_DEG and pitch_span / 2 <= TH_PITCH_DEG
        report.checks.append(CheckResult(
            "姿态稳定性",
            f"roll ±{roll_span / 2:.2f}° pitch ±{pitch_span / 2:.2f}°",
            "PASS" if ok else "FAIL",
            f"标准 ±{TH_ROLL_DEG:.0f}°以内"))

    # ---- 高度（仅悬停段内）----
    if pos is None:
        skip("高度保持", "日志缺少 vehicle_local_position")
    elif window is None:
        skip("高度保持", "未检测到稳定悬停段")
    else:
        z = pos.data["z"]
        sel = in_window(pos.data["timestamp"])
        zs = [v for keep, v in zip(sel, z) if keep]
        drift = max(zs) - min(zs)
        hover_alt = -sum(zs) / len(zs)
        ok = drift <= TH_ALT_DRIFT_M
        report.checks.append(CheckResult(
            "高度保持",
            f"漂移 {drift:.2f} m（均高 {hover_alt:.2f} m）",
            "PASS" if ok else "FAIL",
            f"标准 {TH_ALT_DRIFT_M:.1f} m 以内"))

    # ---- 油门（仅悬停段）----
    mot = find_topic(ulog, "actuator_motors")
    if mot is None:
        skip("悬停油门", "日志缺少 actuator_motors")
    elif window is None:
        skip("悬停油门", "未检测到稳定悬停段")
    else:
        sel = in_window(mot.data["timestamp"])
        m = [v for keep, v in zip(sel, mot.data["control[0]"]) if keep]
        mean_throttle = sum(m) / len(m)
        peak = max(m)
        lo, hi = TH_THROTTLE
        ok = lo <= mean_throttle <= hi
        report.checks.append(CheckResult(
            "悬停油门",
            f"均值 {mean_throttle * 100:.1f}% 峰值 {peak * 100:.1f}%",
            "PASS" if ok else "FAIL",
            f"标准 {lo * 100:.0f}%~{hi * 100:.0f}%"))

    # ---- 振动（仅悬停段）----
    imu = find_topic(ulog, "sensor_combined")
    if imu is None:
        skip("振动水平", "日志缺少 sensor_combined")
    elif window is None:
        skip("振动水平", "未检测到稳定悬停段")
    else:
        sel = in_window(imu.data["timestamp"])
        peak = 0.0
        for axis in ("gyro_rad[0]", "gyro_rad[1]", "gyro_rad[2]"):
            vals = [abs(v) for keep, v in zip(sel, imu.data[axis]) if keep]
            if vals:
                peak = max(peak, max(vals))
        dps = math.degrees(peak)
        ok = dps <= TH_GYRO_DPS
        report.checks.append(CheckResult(
            "振动水平",
            f"角速度峰值 {dps:.1f} °/s",
            "PASS" if ok else "FAIL",
            f"标准 {TH_GYRO_DPS:.0f} °/s 以内"))

    # ---- 系统消息（全程，故障零容忍）----
    # PX4 日志级别（字符 '1'=ERROR，'2'=WARNING）
    errors = [m for m in ulog.logged_messages if level_str(m) == "ERROR"]
    warnings = [m for m in ulog.logged_messages if level_str(m) == "WARNING"]
    cr = CheckResult(
        "系统消息",
        f"ERROR {len(errors)} 个 / WARNING {len(warnings)} 个",
        "PASS" if not errors else "FAIL",
        "标准：无 ERROR")
    for m in errors:
        cr.detail += f"  ← {m.message}"
    report.checks.append(cr)

    return report


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    report = analyze(sys.argv[1])
    print(report.render())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
