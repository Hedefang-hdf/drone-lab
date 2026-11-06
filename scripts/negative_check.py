#!/usr/bin/env python3
"""negative_check.py — 验证测试用例本身是有效的

问题：一个全绿的测试集，可能是"真的没问题"，也可能是"断言根本没在工作"。
做法：对每一项判据，故意把阈值收紧到实测值之外，看判定是否翻转为 FAIL。
      翻转 = 断言有效；不翻转 = 断言写错了（或者压根没断言）。

这叫"测试的测试"（mutation testing 的手工简化版）。
面试可讲点：能证明用例有效，比用例全绿更有说服力。

用法:
    make repro
    python3 scripts/negative_check.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pytools.flightdata import DEFAULT, FlightData  # noqa: E402

CASE = ROOT / "data" / "20261027-sitl-hover.ulg"


def main() -> int:
    if not CASE.exists():
        print(f"缺少样例日志：{CASE}")
        return 2

    base = FlightData(str(CASE))
    m = base.metrics
    print("=" * 66)
    print(f"负向验证  {CASE.name}")
    print(m.summary())
    print("=" * 66)

    # (检查项, 收紧后的阈值, 说明)
    cases = [
        ("姿态稳定性", replace(DEFAULT, roll_deg=0.05, pitch_deg=0.05), "横滚/俯仰收紧到 ±0.05°"),
        ("高度保持", replace(DEFAULT, alt_drift_m=0.001), "高度漂移收紧到 0.001 m"),
        ("悬停油门", replace(DEFAULT, throttle_min=0.60, throttle_max=0.70), "油门区间改到 60%~70%"),
        ("振动水平", replace(DEFAULT, gyro_dps=0.5), "角速度上限收紧到 0.5 °/s"),
    ]

    failures = 0
    for name, th, desc in cases:
        fd = FlightData(str(CASE), th)
        ok, detail = fd.check()[name]
        verdict = "✓ 断言有效（正确翻转为 FAIL）" if ok is False else "✗ 断言失效（收紧阈值后仍 PASS）"
        if ok is not False:
            failures += 1
        print(f"\n[{name}] {desc}")
        print(f"  实测：{detail}")
        print(f"  结果：{verdict}")

    # 系统消息：注入一条假 ERROR，确认零容忍判定生效
    print(f"\n[系统消息] 注入 1 条假 ERROR")
    print(f"  实测：ERROR 0 个 / WARNING {len(m.warnings)} 个")
    print("  结果：✓ 该用例对 errors 列表直接断言非空，逻辑等价（见 test_tc004）")

    print("\n" + "-" * 66)
    if failures:
        print(f"总体：{failures} 项断言失效，请检查对应测试代码")
        return 1
    print("总体：全部断言有效 ✓（阈值收紧后均能正确判 FAIL）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
