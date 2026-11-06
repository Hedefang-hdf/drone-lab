#!/usr/bin/env python3
"""run_campaign.py — 无人值守飞行测试流水线

一条命令跑完一次完整的飞行测试活动：

    起 SITL → 飞（起飞/悬停/降落）→ 取本次日志 → 跑用例 → 出 HTML 报告

两种模式：
    --live            真跑仿真机（需要本机编译好 PX4 SITL + 装 mavsdk-grpc）
    --replay FILE     不飞，直接拿一条已有日志跑"取数→判读→用例→报告"下游全链路

为什么要两种模式？因为流水线本身也要能回归：
  - 飞行部分依赖硬件/仿真环境，CI 上跑不动，但它最容易出问题
  - 判读 + 用例 + 报告是纯计算，必须每次提交都验
replay 模式让下游链路可以在任何机器上（包括 GitHub Actions）秒级验证，
而 live 模式用来证明"这套东西真的驱动过一架飞机"。

产物（campaigns/<id>/）：
    flight.ulg      本次飞行的日志（含 SHA256 指纹）
    manifest.json   全部结构化结果，报告就是它的视图
    report.html     单文件报告，可直接发人
    junit.xml       pytest 原始结果
    pytest.log      用例输出
    sitl.log        SITL 控制台输出（live 模式，排障用）

用法：
    python scripts/run_campaign.py --replay data/20261027-sitl-hover.ulg
    python scripts/run_campaign.py --live --hover 25
    python scripts/run_campaign.py --live --attach      # 连已在跑的 SITL
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime
from importlib.metadata import version as pkg_version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pytools import htmlreport                      # noqa: E402
from pytools.flightdata import FlightData           # noqa: E402
from pytools.sitl import SitlConfig, SitlProcess, find_running_sitl  # noqa: E402

# 注意：MAVSDK 相关的导入放在 fly_live() 里懒加载。
# replay 模式（含 CI）不需要装 MAVSDK——编排器不该为了"可能用不到的功能"
# 把整个依赖树强加给所有调用方。

# 失败项 → 排查方向。报告里不写"请检查"，要写到"调哪个参数"。
ADVICE = {
    "姿态稳定性": "横滚/俯仰偏大通常来自 PID：P 过大表现为等幅震荡，D 不足表现为回中拖沓。"
                  "先看 <code>docs/pid-四环串级.md</code>，再用 <code>make repro</code> 验证判据灵敏度。",
    "高度保持": "定高漂移先排除气压计/激光测距噪声，再看 MPC_Z 系列参数；"
                "若在室内仿真，确认 EKF 高度源与实际一致。",
    "悬停油门": "悬停油门偏离 45%~55% 说明推重比不匹配：油门偏低＝动力过剩（感度需下调），"
                "偏高＝超重或桨效不足。",
    "振动水平": "角速度峰值超限多是机架共振、桨不平衡或减震失效。"
                "优先看 FFT 主频，再决定是换桨还是加配重。",
    "系统消息": "日志里有 ERROR 即一票否决。先按消息原文查 PX4 事件码，"
                "不要用'偶发'解释掉——错误日志是唯一不会撒谎的证据。",
}


# ---------------------------------------------------------------- 阶段计时

@contextmanager
def stage(stages: list[dict], name: str, detail: str = ""):
    """记录一段耗时。抛异常也记，状态标 FAIL —— 失败耗时同样是信息。"""
    rec = {"name": name, "status": "OK", "duration_s": 0.0, "detail": detail}
    stages.append(rec)
    t0 = time.time()
    try:
        yield rec
    except Exception:
        rec["status"] = "FAIL"
        raise
    finally:
        rec["duration_s"] = round(time.time() - t0, 3)


# ---------------------------------------------------------------- 环境与指纹

def git_info() -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            return ""
    return {"sha": run("rev-parse", "--short", "HEAD") or None,
            "dirty": bool(run("status", "--porcelain"))}


def env_info() -> dict:
    def ver(pkg: str):
        try:
            return pkg_version(pkg)
        except Exception:
            return None
    return {"python": platform.python_version(),
            "platform": f"{platform.system()} {platform.machine()}",
            "mavsdk": ver("mavsdk-grpc") or ver("mavsdk"),
            "pytest": ver("pytest")}


def file_fingerprint(path: Path) -> dict:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "size_mb": round(path.stat().st_size / 1048576, 2),
            "sha256": h.hexdigest()[:16]}


# ---------------------------------------------------------------- 下游：测量与用例

def measure(log_path: Path) -> tuple[dict, list[dict]]:
    """数据层测量 → (指标字典, 判定列表)。判定口径与用例共用 flightdata.check()。"""
    fd = FlightData(str(log_path))
    m = fd.metrics
    metrics = {
        "duration_s": round(m.duration_s, 1),
        "hover_found": m.hover_found,
        "hover_s": round(m.hover_s, 1) if m.hover_found else None,
        "hover_samples": int(m.hover_samples),      # numpy.int64 → int，否则 json 序列化失败
        "roll_half_deg": round(m.roll_half_deg, 2) if m.roll_half_deg is not None else None,
        "pitch_half_deg": round(m.pitch_half_deg, 2) if m.pitch_half_deg is not None else None,
        "alt_drift_m": round(m.alt_drift_m, 3) if m.alt_drift_m is not None else None,
        "alt_mean_m": round(m.alt_mean_m, 2) if m.alt_mean_m is not None else None,
        "throttle_mean": round(m.throttle_mean, 4) if m.throttle_mean is not None else None,
        "throttle_peak": round(m.throttle_peak, 4) if m.throttle_peak is not None else None,
        "gyro_peak_dps": round(m.gyro_peak_dps, 2) if m.gyro_peak_dps is not None else None,
        "errors": m.errors,
        "warnings": m.warnings,
    }
    checks = []
    for name, (ok, detail) in fd.check().items():
        verdict = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        checks.append({"name": name, "verdict": verdict, "detail": detail})
    return metrics, checks


def run_pytest(log_path: Path, out_dir: Path) -> dict:
    """调 pytest 跑用例，返回解析后的结果（不依赖 pytest 的 Python API）。"""
    junit = out_dir / "junit.xml"
    logfile = out_dir / "pytest.log"

    env = os.environ.copy()
    env["DRONELAB_ULOG"] = str(log_path.resolve())   # 让 conftest 认识本次日志
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "--tb=short", f"--junitxml={junit}"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    logfile.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")

    summary = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "duration_s": 0.0, "cases": []}
    if not junit.exists():
        summary["error"] = "pytest 未产出 junit.xml"
        return summary

    root = ET.parse(junit).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    for suite in suites:
        summary["duration_s"] += float(suite.get("time", 0) or 0)

    for case in root.iter("testcase"):
        name = f'{case.get("classname", "").split(".")[-1]}::{case.get("name")}'
        outcome, message = "PASS", ""
        if case.find("failure") is not None:
            outcome, message = "FAIL", case.find("failure").get("message", "")
        elif case.find("error") is not None:
            outcome, message = "FAIL", case.find("error").get("message", "")
        elif case.find("skipped") is not None:
            outcome, message = "SKIP", case.find("skipped").get("message", "")
        summary["cases"].append({
            "name": name, "outcome": outcome,
            "duration_s": round(float(case.get("time", 0) or 0), 2),
            "message": message,
        })
        summary["total"] += 1
        summary[{"PASS": "passed", "FAIL": "failed", "SKIP": "skipped"}[outcome]] += 1

    summary["duration_s"] = round(summary["duration_s"], 2)
    summary["exit_code"] = proc.returncode
    return summary


# ---------------------------------------------------------------- 飞（live 模式）

def fly_live(args, out_dir: Path, stages: list[dict], sitl: SitlProcess) -> dict:
    """跑飞行科目，返回 mission 字典。SITL 的启动/关闭由调用方负责。"""
    try:
        from pytools.mission import HoverMission, SafetyEnvelope
    except ImportError:
        raise SystemExit(
            "live 模式需要 MAVSDK：pip install -r requirements-live.txt\n"
            "（replay 模式不需要，见 --replay）")

    mission = HoverMission(
        address=args.address,
        alt_m=args.alt,
        hover_s=args.hover,
        envelope=SafetyEnvelope(max_alt_m=args.max_alt,
                                max_radius_m=args.max_radius,
                                max_flight_s=args.max_flight),
        connect_timeout_s=args.connect_timeout,
    )
    with stage(stages, "飞行科目", f"起飞 {args.alt}m / 悬停 {args.hover}s"):
        result = asyncio.run(mission.run())
    return result.as_dict()


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(
        description="无人值守飞行测试流水线：飞 → 取日志 → 跑用例 → 出报告")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="真跑 PX4 SITL")
    mode.add_argument("--replay", metavar="ULOG", help="用已有日志跑下游全链路")

    ap.add_argument("--attach", action="store_true", help="live 模式下不自己启动 SITL，连已在跑的")
    ap.add_argument("--address", default="udpin://0.0.0.0:14540")
    ap.add_argument("--alt", type=float, default=2.5, help="悬停高度（m）")
    ap.add_argument("--hover", type=float, default=25.0, help="悬停时长（s）")
    ap.add_argument("--max-alt", type=float, default=5.0, help="安全包络：高度上限")
    ap.add_argument("--max-radius", type=float, default=15.0, help="安全包络：水平半径上限")
    ap.add_argument("--max-flight", type=float, default=180.0, help="安全包络：飞行时长上限")
    ap.add_argument("--connect-timeout", type=float, default=45.0)
    ap.add_argument("--px4-dir", default=None, help="PX4 SITL 编译目录（默认 ~/PX4/px4/build/px4_sitl_default）")
    ap.add_argument("--model", default="sihsim_quadx", help="SITL 机型（PX4_SIM_MODEL）")
    ap.add_argument("--out", default=None, help="产物目录（默认 campaigns/<id>）")
    args = ap.parse_args()

    started = datetime.now()
    mode_name = "live" if args.live else "replay"
    campaign_id = f"{started:%Y%m%d-%H%M%S}-{mode_name}"
    out_dir = Path(args.out) if args.out else ROOT / "campaigns" / campaign_id
    out_dir.mkdir(parents=True, exist_ok=True)

    stages: list[dict] = []
    manifest: dict = {
        "campaign_id": campaign_id,
        "mode": mode_name,
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "git": git_info(),
        "env": env_info(),
        "sitl_model": args.model if args.live else None,
        "verdict": "FAIL",
        "exit_code": 1,
    }

    print(f"\n{'=' * 62}\n飞行测试活动 {campaign_id}\n{'=' * 62}")

    # ---------- 阶段 1：准备飞 / 加载日志 ----------
    sitl: SitlProcess | None = None
    log_path: Path | None = None
    mission_dict: dict | None = None

    if args.live:
        cfg = SitlConfig(model=args.model)
        if args.px4_dir:
            cfg.px4_dir = Path(args.px4_dir).expanduser()
        sitl = SitlProcess(cfg)

        try:
            if args.attach:
                if not find_running_sitl(cfg.mavlink_port):
                    print(f"⚠️  --attach 指定但没探测到端口 {cfg.mavlink_port} 上的进程，仍尝试连接")
                sitl.watch()
                with stage(stages, "连接已有 SITL"):
                    pass
            else:
                with stage(stages, "启动 SITL", f"{cfg.model} @ {cfg.px4_dir.name}"):
                    sitl.start()
                    ready_s = sitl.wait_ready()
                    stages[-1]["detail"] += f"，{ready_s:.1f}s 就绪"
                    print(f"  SITL 就绪（{ready_s:.1f}s）")

            try:
                mission_dict = fly_live(args, out_dir, stages, sitl)
            finally:
                if not args.attach:
                    with stage(stages, "停止 SITL"):
                        sitl.stop()

            sitl.save_output(out_dir / "sitl.log")

            with stage(stages, "取本次日志"):
                log_path = sitl.take_log(out_dir / "flight.ulg")
                print(f"  日志：{log_path.name}（{log_path.stat().st_size / 1048576:.2f} MB）")
        except Exception as e:                          # noqa: BLE001 — 编排层兜底，失败也要出报告
            stages.append({"name": "异常终止", "status": "FAIL", "duration_s": 0.0,
                           "detail": str(e)})
            manifest["abort_reason"] = str(e)
            print(f"  ✗ {e}")
            if sitl is not None:
                sitl.save_output(out_dir / "sitl.log")
    else:
        src = Path(args.replay).expanduser()
        if not src.exists():
            print(f"✗ 日志不存在：{src}")
            return 2
        with stage(stages, "加载日志"):
            dest = out_dir / "flight.ulg"
            dest.write_bytes(src.read_bytes())
            log_path = dest
        print(f"  日志：{src.name}（replay 模式，不执行飞行）")

    # ---------- 阶段 2：测量 ----------
    metrics, checks = {}, []
    if log_path is not None:
        try:
            with stage(stages, "日志测量"):
                metrics, checks = measure(log_path)
            print(f"  测量完成：{len(checks)} 项判定")
        except Exception as e:                          # noqa: BLE001
            stages.append({"name": "日志测量", "status": "FAIL", "duration_s": 0.0,
                           "detail": str(e)})
            manifest["abort_reason"] = f"日志测量失败：{e}"
            print(f"  ✗ 日志测量失败：{e}")

    # ---------- 阶段 3：用例 ----------
    tests: dict = {}
    if log_path is not None:
        with stage(stages, "执行测试用例"):
            tests = run_pytest(log_path, out_dir)
        print(f"  用例：{tests.get('passed', 0)} 通过 / {tests.get('failed', 0)} 失败 / "
              f"{tests.get('skipped', 0)} 跳过（{tests.get('duration_s', 0):.2f}s）")

    # ---------- 阶段 4：判定与报告 ----------
    failed_checks = [c["name"] for c in checks if c["verdict"] == "FAIL"]
    all_skip = checks and all(c["verdict"] == "SKIP" for c in checks)
    mission_failed = bool(mission_dict and not mission_dict.get("ok"))

    if log_path is None:
        verdict = "FAIL"
    elif mission_failed or failed_checks or tests.get("failed") or all_skip:
        verdict = "FAIL"
    else:
        verdict = "PASS"

    advice_parts = []
    if all_skip:
        advice_parts.append("<b>未检测到稳定悬停段</b>：日志里没有满足"
                            "「高度 &gt; 0.8m 且垂直速度 &lt; 0.3m/s」的窗口。"
                            "确认飞机真的悬停过，或检查是否只记录了地面待机。")
    for name in failed_checks:
        if name in ADVICE:
            advice_parts.append(f"<b>{name}</b>：{ADVICE[name]}")
    if mission_dict and not mission_dict.get("ok"):
        advice_parts.append(f"<b>飞行中止</b>：{mission_dict.get('abort_reason')}。"
                            "前置检查不通过时不要绕过——带病起飞的数据不可信。")
    if tests.get("failed"):
        advice_parts.append("<b>用例失败</b>：报告用例明细里的失败信息已附排查方向，"
                            "先按方向定位，再决定是改代码还是改标准。")

    manifest.update({
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "verdict": verdict,
        "exit_code": 0 if verdict == "PASS" else 1,
        "mission": mission_dict,
        "log": file_fingerprint(log_path) if log_path else None,
        "metrics": metrics,
        "checks": checks,
        "tests": tests,
        "stages": stages,
        "advice": "".join(f"<div style='margin-bottom:8px'>{a}</div>" for a in advice_parts),
        "abort_reason": manifest.get("abort_reason"),
    })

    with stage(stages, "生成报告"):
        htmlreport.write_manifest(manifest, out_dir / "manifest.json")
        html_report = htmlreport.write(manifest, out_dir / "report.html")
    manifest["stages"] = stages                    # 报告包含生成报告本身的耗时
    htmlreport.write_manifest(manifest, out_dir / "manifest.json")

    # ---------- 控制台汇总 ----------
    print(f"\n{'-' * 62}")
    for c in checks:
        print(f"  [{c['verdict']:<4}] {c['name']:<8} {c['detail']}")
    print(f"{'-' * 62}")
    print(f"  判定：{verdict}    产物：{out_dir}")
    print(f"  报告：{html_report}")
    print(f"{'=' * 62}\n")
    return manifest["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
