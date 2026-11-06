#!/usr/bin/env python3
"""mission.py — 飞行科目（MAVSDK 驱动）

职责：**把"飞一次悬停"写成可重复执行的代码，并且飞的时候自己看着边界。**

为什么测试岗要自己写飞行脚本？因为手工飞出来的日志没法回归：
  - 同一批参数，今天飞 3 米明天飞 2.8 米，横向比不了
  - 手抖导致的姿态抖动会被当成飞机的问题
  - 飞完没人记得当时的电量、风速、飞行模式

这里的科目是固定参数的：起飞 → 悬停 N 秒（2Hz 采样）→ 降落。
过程中持续监控 **安全包络**（高度上限 / 水平半径 / 时长 / 电量），
任一越界立即降落并记录原因 —— 自动化测试不能以"把飞机飞丢"为代价。

设计要点：**每个遥测 topic 只订阅一次，结果缓存到成员变量。**
MAVSDK 的 `telemetry.xxx()` 是异步生成器（stream），不是可以 await 的取值函数；
反复调用会持续创建 gRPC 流，越飞越漏。所以这里有 `_start_telemetry()` 统一订阅，
其余代码只读缓存。

飞完产出 MissionResult：既有飞控侧的事实（是否解锁/起飞/落地），
也有过程中采到的遥测样本，供后续与日志判读结果交叉验证。

用法：
    result = asyncio.run(HoverMission().run())
    if not result.ok:
        print(result.abort_reason)
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field

# MAVSDK 新包名为 mavsdk-grpc（模块 mavsdk_grpc），老包名 mavsdk<4 仍可用但已停止更新。
try:
    from mavsdk_grpc import System           # type: ignore
except ImportError:                          # pragma: no cover
    from mavsdk import System                # type: ignore


# ---------------------------------------------------------------- 参数与结果

@dataclass
class SafetyEnvelope:
    """安全包络。越界即中止——这是自动化飞行测试的底线，不是可选项。"""
    max_alt_m: float = 5.0
    max_radius_m: float = 15.0
    max_flight_s: float = 180.0
    min_battery_pct: float = 30.0


@dataclass
class PreflightCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Sample:
    t_s: float
    alt_m: float
    roll_deg: float
    pitch_deg: float
    yaw_deg: float
    battery_pct: float | None = None
    radius_m: float = 0.0


@dataclass
class MissionResult:
    ok: bool = False
    abort_reason: str | None = None
    preflight: list[PreflightCheck] = field(default_factory=list)
    takeoff_ok: bool = False
    land_ok: bool = False
    hover_target_s: float = 0.0
    hover_actual_s: float = 0.0
    samples: list[Sample] = field(default_factory=list)
    max_alt_m: float = 0.0
    max_radius_m: float = 0.0
    duration_s: float = 0.0
    events: list[str] = field(default_factory=list)

    @property
    def preflight_ok(self) -> bool:
        return all(c.ok for c in self.preflight)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "abort_reason": self.abort_reason,
            "preflight": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.preflight],
            "takeoff_ok": self.takeoff_ok,
            "land_ok": self.land_ok,
            "hover_target_s": round(self.hover_target_s, 1),
            "hover_actual_s": round(self.hover_actual_s, 1),
            "samples": len(self.samples),
            "max_alt_m": round(self.max_alt_m, 2),
            "max_radius_m": round(self.max_radius_m, 2),
            "duration_s": round(self.duration_s, 1),
            "events": self.events[-40:],
        }


# ---------------------------------------------------------------- 工具

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点水平距离（米）。用于判断是否飞出安全半径。"""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _battery_pct(batt) -> float | None:
    """MAVSDK 各版本字段名不同，且可能是 0~1 也可能是 0~100，统一成百分比。"""
    if batt is None:
        return None
    for attr in ("remaining_percent", "battery_remaining"):
        v = getattr(batt, attr, None)
        if v is None:
            continue
        v = float(v)
        if v <= 1.5:          # 0~1 的比例值
            v *= 100.0
        return v
    return None


# ---------------------------------------------------------------- 科目

class HoverMission:
    """起飞 → 定高悬停 N 秒 → 降落。全程监控安全包络。"""

    def __init__(
        self,
        # udp:// 在 MAVSDK 3.x 已弃用；udpin 表示"监听进入的连接"，对应 SITL 的 14540
        address: str = "udpin://0.0.0.0:14540",
        alt_m: float = 2.5,
        hover_s: float = 25.0,
        envelope: SafetyEnvelope | None = None,
        connect_timeout_s: float = 45.0,
        telemetry_timeout_s: float = 20.0,
    ):
        self.address = address
        self.alt_m = alt_m
        self.hover_s = hover_s
        self.env = envelope or SafetyEnvelope()
        self.connect_timeout_s = connect_timeout_s
        self.telemetry_timeout_s = telemetry_timeout_s

        # 遥测缓存：订阅任务持续刷新，其余代码只读这些字段
        self._alt = 0.0
        self._lat = 0.0
        self._lon = 0.0
        self._roll = 0.0
        self._pitch = 0.0
        self._yaw = 0.0
        self._batt: float | None = None
        self._home: tuple[float, float] | None = None
        self._in_air = False
        self._armed = False
        self._mode = "unknown"
        self._health = None
        self._events: list[str] = []

    # -------------------------------------------------- 主流程
    async def run(self) -> MissionResult:
        t0 = time.time()
        res = MissionResult(hover_target_s=self.hover_s)
        drone = System()
        tasks: list = []

        try:
            await drone.connect(system_address=self.address)
            if not await self._wait_connected(drone):
                res.abort_reason = f"{self.connect_timeout_s:.0f}s 内未连接到飞控（{self.address}）"
                return res

            tasks = self._start_telemetry(drone)
            if not await self._wait_telemetry():
                res.abort_reason = f"{self.telemetry_timeout_s:.0f}s 内未收到健康/位置遥测"
                return res

            res.preflight = self._preflight()
            if not res.preflight_ok:
                failed = "、".join(c.name for c in res.preflight if not c.ok)
                res.abort_reason = f"前置检查未通过：{failed}"
                return res

            res.takeoff_ok = await self._takeoff(drone)
            if res.takeoff_ok:
                await self._hover(res)
            res.land_ok = await self._land(drone)
        except Exception as e:                     # noqa: BLE001 — 编排层要拿到原因而不是栈
            res.abort_reason = f"飞行过程异常：{type(e).__name__}: {e}"
            self._events.append(res.abort_reason)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            res.events = list(self._events)
            res.duration_s = time.time() - t0

        res.ok = bool(res.takeoff_ok and res.land_ok and res.abort_reason is None and res.samples)
        if not res.ok and res.abort_reason is None:
            res.abort_reason = "飞行未采到遥测样本"
        return res

    # -------------------------------------------------- 连接
    async def _wait_connected(self, drone) -> bool:
        # core.connection_state() 同样是异步生成器（流式），要用 async for 消费
        async def wait_one() -> bool:
            async for state in drone.core.connection_state():
                return bool(state.is_connected)
            return False

        deadline = time.time() + self.connect_timeout_s
        while time.time() < deadline:
            try:
                if await asyncio.wait_for(wait_one(), timeout=2.0):
                    self._events.append(f"已连接 {self.address}")
                    return True
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(0.3)
        return False

    # -------------------------------------------------- 遥测订阅（唯一入口）
    def _start_telemetry(self, drone) -> list:
        """每个 topic 订阅一次，把最新值写进缓存。返回所有任务以便统一取消。"""
        async def position():
            async for p in drone.telemetry.position():
                self._alt = float(p.relative_altitude_m)
                self._lat, self._lon = float(p.latitude_deg), float(p.longitude_deg)

        async def attitude():
            async for a in drone.telemetry.attitude_euler():
                self._roll, self._pitch, self._yaw = (
                    float(a.roll_deg), float(a.pitch_deg), float(a.yaw_deg))

        async def battery():
            async for b in drone.telemetry.battery():
                self._batt = _battery_pct(b)

        async def home():
            async for h in drone.telemetry.home():
                self._home = (float(h.latitude_deg), float(h.longitude_deg))

        async def in_air():
            async for v in drone.telemetry.in_air():
                self._in_air = bool(v)

        async def armed():
            async for v in drone.telemetry.armed():
                self._armed = bool(v)

        async def flight_mode():
            async for v in drone.telemetry.flight_mode():
                self._mode = str(v).split(".")[-1]

        async def health():
            async for h in drone.telemetry.health():
                self._health = h

        async def status_text():
            async for s in drone.telemetry.status_text():
                self._events.append(f"[飞控] {s.text}")

        return [asyncio.ensure_future(coro()) for coro in
                (position, attitude, battery, home, in_air, armed, flight_mode, health, status_text)]

    async def _wait_telemetry(self) -> bool:
        """等缓存里出现健康与位置数据。收不到就别飞——没有位置估计的起飞是盲飞。"""
        deadline = time.time() + self.telemetry_timeout_s
        while time.time() < deadline:
            if self._health is not None and self._alt != 0.0:
                return True
            if self._health is not None and self._lat != 0.0:
                return True
            await asyncio.sleep(0.3)
        return False

    # -------------------------------------------------- 前置检查（读缓存，不阻塞）
    def _preflight(self) -> list[PreflightCheck]:
        """起飞前的放行条件。任何一项不通过都拒绝起飞。

        真实测试里这一步比飞行本身重要：带着未通过的检查起飞，
        后面所有结论都是不可信的。
        """
        checks: list[PreflightCheck] = []

        param_ok = 0 < self.alt_m < self.env.max_alt_m
        checks.append(PreflightCheck(
            "科目参数", param_ok,
            f"目标高度 {self.alt_m}m，包络上限 {self.env.max_alt_m}m"
            + ("" if param_ok else " ← 参数越界")))

        h = self._health
        checks.append(PreflightCheck("可解锁", bool(getattr(h, "is_armable", False)),
                                     f"is_armable={getattr(h, 'is_armable', None)}"))
        checks.append(PreflightCheck("位置估计", bool(getattr(h, "is_local_position_ok", False)),
                                     f"local_position_ok={getattr(h, 'is_local_position_ok', None)}"))
        checks.append(PreflightCheck("Home 点", bool(getattr(h, "is_home_position_ok", False)),
                                     f"home_position_ok={getattr(h, 'is_home_position_ok', None)}"))

        if self._batt is None:
            checks.append(PreflightCheck("电量", True, "飞控未上报电量（仿真常见），跳过"))
        else:
            checks.append(PreflightCheck("电量", self._batt >= self.env.min_battery_pct,
                                         f"{self._batt:.1f}% / 下限 {self.env.min_battery_pct:.0f}%"))

        checks.append(PreflightCheck("飞行模式", self._mode.upper() != "UNKNOWN",
                                     f"当前 {self._mode}"))

        for c in checks:
            self._events.append(f"前置检查 {c.name}: {'PASS' if c.ok else 'FAIL'} ({c.detail})")
        return checks

    # -------------------------------------------------- 起飞
    async def _takeoff(self, drone) -> bool:
        try:
            await drone.action.set_takeoff_altitude(self.alt_m)
            await drone.action.arm()
            self._events.append("已解锁")
            await drone.action.takeoff()

            # 等真正离地，而不是"发了指令就当成功"
            deadline = time.time() + 30
            while time.time() < deadline:
                if self._in_air:
                    self._events.append(f"已离地，目标 {self.alt_m}m")
                    return True
                await asyncio.sleep(0.3)
            self._events.append("起飞超时：30s 内未离地")
            return False
        except Exception as e:                     # noqa: BLE001 — 飞控异常一律视为起飞失败
            self._events.append(f"起飞异常：{e}")
            return False

    # -------------------------------------------------- 悬停 + 包络监控
    async def _hover(self, res: MissionResult) -> None:
        t0 = time.time()
        while True:
            elapsed = time.time() - t0
            if elapsed >= self.hover_s:
                break

            radius = 0.0
            if self._home:
                radius = haversine_m(self._home[0], self._home[1], self._lat, self._lon)
            res.max_alt_m = max(res.max_alt_m, self._alt)
            res.max_radius_m = max(res.max_radius_m, radius)
            res.samples.append(Sample(
                t_s=round(elapsed, 2), alt_m=round(self._alt, 3),
                roll_deg=round(self._roll, 2), pitch_deg=round(self._pitch, 2),
                yaw_deg=round(self._yaw, 2), battery_pct=self._batt,
                radius_m=round(radius, 2),
            ))

            breach = self._envelope_breach(res.max_alt_m, res.max_radius_m, elapsed)
            if breach:
                res.abort_reason = breach
                self._events.append(f"安全包络越界，强制降落：{breach}")
                break
            await asyncio.sleep(0.5)

        res.hover_actual_s = time.time() - t0

    def _envelope_breach(self, alt: float, radius: float, elapsed: float) -> str | None:
        if alt > self.env.max_alt_m + 1.0:          # 留 1m 容差，避免定高波动误触发
            return f"高度 {alt:.1f}m 超出上限 {self.env.max_alt_m:.0f}m"
        if radius > self.env.max_radius_m:
            return f"水平距离 {radius:.1f}m 超出半径 {self.env.max_radius_m:.0f}m"
        if elapsed > self.env.max_flight_s:
            return f"飞行时长超出上限 {self.env.max_flight_s:.0f}s"
        if self._batt is not None and self._batt < self.env.min_battery_pct:
            return f"电量 {self._batt:.0f}% 低于下限 {self.env.min_battery_pct:.0f}%"
        return None

    # -------------------------------------------------- 降落
    async def _land(self, drone) -> bool:
        try:
            await drone.action.land()
            deadline = time.time() + 40
            while time.time() < deadline:
                if not self._in_air:
                    self._events.append("已落地")
                    try:
                        await drone.action.disarm()
                    except Exception:
                        pass
                    return True
                await asyncio.sleep(0.5)
            self._events.append("降落超时：40s 内未落地")
            return False
        except Exception as e:                     # noqa: BLE001
            self._events.append(f"降落异常：{e}")
            return False
