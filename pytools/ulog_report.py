#!/usr/bin/env python3
"""ulog_report.py — PX4 .ulg 飞行日志体检报告

读取一条 PX4 飞行日志（.ulg），自动定位"稳定悬停段"，
只对悬停段做质量判定并输出报告。判定不通过时以非零码退出，
方便后续接入 CI / pytest。

本脚本是 **展示层**：所有测量由 pytools/flightdata.py 完成，
是同一份数据在 flytests/ 里被 pytest 断言时的唯一来源。

设计要点：判读前必须先分段。整段日志混着起飞爬升与降落，
直接对全程统计会把起降过程误判为"高度漂移"——先选段，再判读。

用法:
    python3 ulog_report.py <file.ulg>
    make report ULOG=data/20261027-sitl-hover.ulg
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

try:
    from flightdata import DEFAULT, FlightData, Thresholds
except ImportError:  # 允许从仓库根目录以 pytools.ulog_report 形式导入
    from pytools.flightdata import DEFAULT, FlightData, Thresholds


# 兼容旧接口：阈值常量仍可从本模块导入
TH_ROLL_DEG = DEFAULT.roll_deg
TH_PITCH_DEG = DEFAULT.pitch_deg
TH_ALT_DRIFT_M = DEFAULT.alt_drift_m
TH_THROTTLE = (DEFAULT.throttle_min, DEFAULT.throttle_max)
TH_GYRO_DPS = DEFAULT.gyro_dps


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


def analyze(path: str, thresholds: Thresholds = DEFAULT) -> Report:
    """测量 → 判定 → 渲染。测量逻辑全部位于 FlightData.metrics。"""
    fd = FlightData(path, thresholds)
    m = fd.metrics
    report = Report(source=path, duration_s=m.duration_s, hover_s=m.hover_s)

    def add(name: str, value: str, ok: bool | None, detail: str) -> None:
        if ok is None:
            report.checks.append(CheckResult(name, "-", "SKIP", detail))
        else:
            report.checks.append(CheckResult(name, value, "PASS" if ok else "FAIL", detail))

    # 姿态
    if m.roll_half_deg is None:
        add("姿态稳定性", "", None, "日志缺少 vehicle_attitude 或未检测到稳定悬停段")
    else:
        add("姿态稳定性",
            f"roll ±{m.roll_half_deg:.2f}° pitch ±{m.pitch_half_deg:.2f}°",
            m.roll_half_deg <= thresholds.roll_deg and m.pitch_half_deg <= thresholds.pitch_deg,
            f"标准 ±{thresholds.roll_deg:.0f}°以内")

    # 高度
    if m.alt_drift_m is None:
        add("高度保持", "", None, "日志缺少 vehicle_local_position 或未检测到稳定悬停段")
    else:
        add("高度保持",
            f"漂移 {m.alt_drift_m:.2f} m（均高 {m.alt_mean_m:.2f} m）",
            m.alt_drift_m <= thresholds.alt_drift_m,
            f"标准 {thresholds.alt_drift_m:.1f} m 以内")

    # 油门
    if m.throttle_mean is None:
        add("悬停油门", "", None, "日志缺少 actuator_motors 或未检测到稳定悬停段")
    else:
        add("悬停油门",
            f"均值 {m.throttle_mean * 100:.1f}% 峰值 {m.throttle_peak * 100:.1f}%",
            thresholds.throttle_min <= m.throttle_mean <= thresholds.throttle_max,
            f"标准 {thresholds.throttle_min * 100:.0f}%~{thresholds.throttle_max * 100:.0f}%")

    # 振动
    if m.gyro_peak_dps is None:
        add("振动水平", "", None, "日志缺少 sensor_combined 或未检测到稳定悬停段")
    else:
        add("振动水平",
            f"角速度峰值 {m.gyro_peak_dps:.1f} °/s",
            m.gyro_peak_dps <= thresholds.gyro_dps,
            f"标准 {thresholds.gyro_dps:.0f} °/s 以内")

    # 系统消息（全程，故障零容忍）
    detail = "标准：无 ERROR" + "".join(f"  ← {e}" for e in m.errors)
    add("系统消息",
        f"ERROR {len(m.errors)} 个 / WARNING {len(m.warnings)} 个",
        len(m.errors) == 0, detail)

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
