"""conftest.py — pytest 公共夹具

核心思路：**日志即测试数据**。
data/ 目录下每一个 .ulg 都会自动变成一个参数，同一套用例对它全部跑一遍。
新增一条飞行日志 = 新增一组测试，不改一行用例代码。

流水线（scripts/run_campaign.py）飞出来的日志不在 data/ 里，
通过环境变量 DRONELAB_ULOG 指路（多个用系统路径分隔符隔开），用例代码不用改：
    DRONELAB_ULOG=campaigns/xxx/flight.ulg pytest

运行:
    pytest                        # 跑全部日志的全部用例
    pytest -m hover               # 只跑悬停类
    pytest -k "20261027"          # 只跑某条日志
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pytools.flightdata import DEFAULT, FlightData, Thresholds  # noqa: E402


def discover_logs() -> list[str]:
    """待测日志清单。

    优先级：环境变量 DRONELAB_ULOG（流水线注入）> data/ 目录扫描。
    没有任何日志时用一个占位值，让用例显式 SKIP 而不是假装通过。
    """
    override = os.environ.get("DRONELAB_ULOG", "").strip()
    if override:
        return [p for p in override.split(os.pathsep) if p.strip()]
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
