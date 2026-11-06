#!/usr/bin/env python3
"""sitl.py — PX4 SITL 进程管理

职责：**把"一台仿真飞机"变成一个可以 start/stop 的对象。**

自动化飞行测试最怕的不是飞行失败，是环境不可控：
  - SITL 起来没有？什么时候算"就绪"？靠人盯着 "Ready for takeoff" 不算自动化。
  - 飞完日志在哪？PX4 按 rootfs/fs/log/<日期>/<时分秒>.ulg 落盘，取错了就读到上一次的。
  - 崩了怎么办？进程残留占着 14540 端口，下一次连接直接超时。

所以这里做三件事：
  1. start() 拉起进程，wait_ready() 读到就绪标记才返回（超时即抛错，不猜）
  2. 启动前给日志目录拍快照，停止后按"新增文件"取本次飞行的日志（不靠文件名猜）
  3. 上下文管理器保证退出时一定收尸：SIGINT → 等 5s → SIGKILL

用法：
    with SitlProcess() as sitl:
        ...  # 这里连 udp://:14540 飞
    ulog = sitl.take_log()      # 本次新产生的 .ulg
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# macOS 上 PX4 SITL 的默认编译产物目录，可用 --px4-dir 覆盖
DEFAULT_PX4_DIR = Path.home() / "PX4" / "px4" / "build" / "px4_sitl_default"

# 判定"就绪"的输出标记。两个都要看到：
#   Startup script returned successfully → 启动脚本跑完了
#   Ready for takeoff                    → 估计器收敛、可以解锁
READY_MARKERS = ("Startup script returned successfully", "Ready for takeoff")

# PX4 的输出会带 ANSI 颜色码，写日志文件前清掉，否则 grep 出来是乱码
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class SitlError(RuntimeError):
    """SITL 启动/就绪/取日志失败。"""


@dataclass
class SitlConfig:
    px4_dir: Path = DEFAULT_PX4_DIR
    model: str = "sihsim_quadx"          # 对应 SYS_AUTOSTART=10040
    ready_timeout_s: float = 90.0        # 首次编译后的冷启动会比较慢
    mavlink_port: int = 14540
    stop_grace_s: float = 5.0

    @property
    def binary(self) -> Path:
        return self.px4_dir / "bin" / "px4"

    @property
    def log_root(self) -> Path:
        return self.px4_dir / "rootfs" / "fs" / "log"

    def validate(self) -> None:
        if not self.binary.exists():
            raise SitlError(
                f"找不到 PX4 SITL 可执行文件：{self.binary}\n"
                f"  请先编译：cd {self.px4_dir.parent.parent} && make px4_sitl\n"
                f"  或用 --px4-dir 指定实际编译目录"
            )


class SitlProcess:
    """一个 PX4 SITL 实例的生命周期。"""

    def __init__(self, cfg: SitlConfig | None = None):
        self.cfg = cfg or SitlConfig()
        self._proc: subprocess.Popen | None = None
        self._lines: list[str] = []
        self._reader: threading.Thread | None = None
        self._ready = threading.Event()
        self._seen: set[str] = set()
        self._before: set[Path] = set()
        self.notes: list[str] = []          # 编排层可以把这些写进报告
        self.started_at: float | None = None

    # ------------------------------------------------------------ 启动前自愈
    def _clear_stale_links(self) -> None:
        """清掉上次非正常退出留下的 rootfs 符号链接。

        PX4 启动时会建 rootfs/etc -> build/etc，但如果这个链接已存在，
        它不会覆盖，而是直接 PX4_ERR 并退出（退出码 255，一行输出都没有）。
        表现为"昨天还能起，今天起不来"，而且报错信息只有半句，极难定位。

        rootfs/etc 是指向构建目录的符号链接，删掉是安全的：PX4 启动时会重建。
        注意**只删链接，不删 rootfs/fs**——那是历史飞行日志的存放处。
        """
        link = self.cfg.px4_dir / "rootfs" / "etc"
        if link.is_symlink():
            target = os.readlink(link)
            link.unlink()
            self.notes.append(f"清理残留符号链接 rootfs/etc → {target}")

    def _clean_empty_log_dirs(self) -> None:
        """删掉日志目录下的空日期目录。

        PX4 启动时会尝试清理空的日期目录，但经常删不掉（EPERM），
        反复在日志里打 ERROR——那个 ERROR 会被 TC-004 当成真故障一票否决。
        提前帮它清掉，日志里就只剩真问题。
        """
        root = self.cfg.log_root
        if not root.exists():
            return
        for d in root.iterdir():
            try:
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass

    # ------------------------------------------------------------ 生命周期
    def start(self) -> "SitlProcess":
        self.cfg.validate()
        self._clear_stale_links()
        self._clean_empty_log_dirs()
        self._before = self._snapshot()

        env = os.environ.copy()
        env["PX4_SIM_MODEL"] = self.cfg.model
        env.pop("PX4_SIM_SPEED_FACTOR", None)      # 别被用户环境里的加速倍率影响时序

        self._proc = subprocess.Popen(
            [str(self.cfg.binary)],
            cwd=str(self.cfg.px4_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # stdin 必须是"打开的管道"，不能是 DEVNULL：
            # PX4 的 pxh 控制台会 poll stdin，DEVNULL 永远可读（EOF），
            # 它就会不停打印 "pxh>" 提示符 —— 实测 5 秒刷出 200 万行，直接把进程拖死。
            stdin=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,               # 独立进程组，便于整组回收
        )
        self.started_at = time.time()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        return self

    def _pump(self) -> None:
        """持续读输出。既做就绪探测，也留一份日志用于失败诊断。

        就绪判定不能用"扫最近 N 行"：PX4 的输出有突发（启动脚本、故障告警），
        一旦中间夹了大量输出，标记就被冲出窗口了。改为逐行累积"见过的标记"。
        """
        assert self._proc and self._proc.stdout
        for raw in self._proc.stdout:
            line = ANSI_RE.sub("", raw.rstrip("\n"))
            stripped = line.strip()
            if stripped and stripped != "pxh>":        # 丢掉空行与纯提示符
                self._lines.append(line)
                if len(self._lines) > 3000:            # 长跑保护
                    del self._lines[:1500]

            for marker in READY_MARKERS:
                if marker in line:
                    self._seen.add(marker)
            if len(self._seen) == len(READY_MARKERS):
                self._ready.set()

    def wait_ready(self, timeout_s: float | None = None) -> float:
        """阻塞直到就绪，返回耗时（秒）。超时抛错并附最近的输出。"""
        timeout = timeout_s if timeout_s is not None else self.cfg.ready_timeout_s
        if self._ready.wait(timeout):
            return time.time() - (self.started_at or time.time())
        tail = "\n".join(self._lines[-25:]) or "(无输出)"
        raise SitlError(
            f"SITL 在 {timeout:.0f}s 内未就绪。最近输出：\n{tail}\n"
            f"  排查方向：① rootfs/etc 残留符号链接（本次已尝试自动清理）"
            f"② 端口被占用（lsof -i :{self.cfg.mavlink_port}）"
            f"③ 机型名 {self.cfg.model} 是否存在于该 PX4 版本"
        )

    def stop(self) -> None:
        if not self._proc or self._proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGINT)   # PX4 收到 SIGINT 会写盘退出
        except ProcessLookupError:
            return
        try:
            self._proc.wait(timeout=self.cfg.stop_grace_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            self._proc.wait(timeout=5)

    def __enter__(self) -> "SitlProcess":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------ 日志取回
    def _snapshot(self) -> set[Path]:
        if not self.cfg.log_root.exists():
            return set()
        return set(self.cfg.log_root.rglob("*.ulg"))

    def watch(self) -> None:
        """不启动进程，只从此刻开始监视新增日志。

        --attach 模式用：SITL 是人工起的，我们仍然要能取出"这一次"的日志。
        """
        self._before = self._snapshot()

    def latest_ulog(self) -> Path | None:
        """本次启动后新增的 .ulg。多个时取修改时间最新的一个。"""
        now = set(self.cfg.log_root.rglob("*.ulg")) if self.cfg.log_root.exists() else set()
        new = now - self._before
        if not new:
            return None
        return max(new, key=lambda p: p.stat().st_mtime)

    def take_log(self, dest: Path, settle_timeout_s: float = 20.0) -> Path:
        """等待日志落盘并复制到 dest。

        PX4 在停机时才把 ulog 完整刷盘，所以调用前应先 stop()；
        这里再做一次轮询兜底，顺便容忍文件系统延迟。
        """
        deadline = time.time() + settle_timeout_s
        src: Path | None = None
        while time.time() < deadline:
            src = self.latest_ulog()
            if src and src.stat().st_size > 0:
                break
            time.sleep(0.5)
        if src is None:
            raise SitlError(
                f"本次运行没有产生新的 .ulg（日志目录 {self.cfg.log_root}）\n"
                f"  排查方向：SDLOG_MODE 参数是否为 0（解锁即记录）、是否真的解锁起飞过"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        return dest

    # ------------------------------------------------------------ 诊断
    @property
    def output(self) -> str:
        return "\n".join(self._lines)

    def save_output(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        header = "".join(f"# {n}\n" for n in self.notes)
        path.write_text(header + self.output, encoding="utf-8")

    @property
    def alive(self) -> bool:
        return bool(self._proc and self._proc.poll() is None)


def find_running_sitl(port: int = 14540) -> bool:
    """粗探测：本机是否有进程在监听 MAVLink 端口。用于 --attach 模式的前置提示。"""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iUDP:{port}"], capture_output=True, text=True, timeout=5
        )
        return bool(out.stdout.strip())
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
