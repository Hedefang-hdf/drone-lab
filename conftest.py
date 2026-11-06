"""conftest.py — pytest 公共夹具

核心思路：**日志即测试数据**。
data/ 目录下每一个 .ulg 都会自动变成一个参数，同一套用例对它全部跑一遍。
新增一条飞行日志 = 新增一组测试，不改一行用例代码。

运行:
    pytest                        # 跑全部日志的全部用例
    pytest -m hover               # 只跑悬停类
    pytest -k "20261027"          # 只跑某条日志
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pytools.flightdata import DEFAULT, FlightData, Thresholds  # noqa: E402


def discover_logs() -> list[str]:
    """data/ 下所有 .ulg。没有任何日志时用一个占位值，让用例显式 SKIP。"""
    logs = sorted(str(p) for p in (ROOT / "data").glob("*.ulg"))
    return logs or ["<no-log>"]


@pytest.fixture(scope="session", params=discover_logs(), ids=lambda p: Path(p).stem)
def flight(request) -> FlightData:
    """一条飞行日志的只读视图（session 级：整个测试会话只加载一次）。"""
    if request.param == "<no-log>":
        pytest.skip("data/ 下没有任何 .ulg 日志")
    return FlightData(request.param)


@pytest.fixture(scope="session")
def th() -> Thresholds:
    """判定阈值。改标准只改 pytools/flightdata.py 里的 Thresholds。"""
    return DEFAULT


@pytest.fixture(scope="session")
def m(flight: FlightData):
    """当前日志的全部测量结果（惰性计算，缓存）。"""
    return flight.metrics
