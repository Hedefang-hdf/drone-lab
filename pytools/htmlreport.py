#!/usr/bin/env python3
"""htmlreport.py — 一次飞行测试活动的单文件 HTML 报告

输入是一个 manifest（dict），输出是一段自包含的 HTML：不引外部 CSS/JS/字体，
可以直接扔进邮件、上传到制品库、或者发给同事用浏览器打开。

设计取向：**先给结论，再给证据，最后给元信息。**
  1. 顶部一条横幅：这次活动整体 PASS 还是 FAIL
  2. 关键指标卡片：一眼看到悬停时长、姿态、漂移、油门、角速度
  3. 判定明细 + 用例明细：每条都有"实测 / 标准"，不写模糊话
  4. 流水线阶段耗时：哪一步慢、哪一步挂了
  5. 元信息：日志指纹、git sha、环境版本 —— 报告要能被追溯

用法：
    from pytools.htmlreport import render
    Path("report.html").write_text(render(manifest), encoding="utf-8")
"""

from __future__ import annotations

import html
import json
from pathlib import Path

CSS = """
:root{
  --bg:#f6f7f9; --card:#ffffff; --ink:#1b1f24; --muted:#6a737d; --line:#e3e6ea;
  --pass:#1a7f37; --pass-bg:#e9f6ec; --fail:#c9252d; --fail-bg:#fdeceb;
  --skip:#8a6d00; --skip-bg:#fdf5d9; --accent:#0b5cad; --accent-bg:#eaf2fc;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;}
.wrap{max-width:1000px;margin:0 auto;padding:32px 24px 64px}
h1{font-size:24px;margin:0 0 4px;letter-spacing:-.2px}
h2{font-size:16px;margin:32px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line);color:var(--ink)}
.sub{color:var(--muted);font-size:13px;font-family:var(--mono)}
.banner{display:flex;align-items:center;gap:14px;padding:20px 24px;border-radius:12px;margin:20px 0 8px;
  border:1px solid var(--line);background:var(--card)}
.banner.pass{border-color:#bfe3c8;background:var(--pass-bg)}
.banner.fail{border-color:#f3c2c0;background:var(--fail-bg)}
.verdict{font-size:22px;font-weight:700;letter-spacing:.4px}
.banner.pass .verdict{color:var(--pass)}
.banner.fail .verdict{color:var(--fail)}
.banner .why{color:var(--muted);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:12px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{font-size:12px;color:var(--muted);margin-bottom:6px}
.card .v{font-size:20px;font-weight:650;font-family:var(--mono);letter-spacing:-.5px}
.card .u{font-size:12px;color:var(--muted);font-weight:400;margin-left:2px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
  border-radius:10px;overflow:hidden;font-size:14px}
th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#fafbfc;font-size:12px;color:var(--muted);font-weight:600;letter-spacing:.3px}
tr:last-child td{border-bottom:none}
td.num,th.num{font-family:var(--mono);white-space:nowrap}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:650;
  font-family:var(--mono);letter-spacing:.3px}
.tag.pass{color:var(--pass);background:var(--pass-bg)}
.tag.fail{color:var(--fail);background:var(--fail-bg)}
.tag.skip{color:var(--skip);background:var(--skip-bg)}
.detail{color:var(--muted);font-size:13px}
.mono{font-family:var(--mono);font-size:13px}
.stage{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px dashed var(--line)}
.stage:last-child{border-bottom:none}
.stage .name{width:150px;flex:none;font-size:13px}
.stage .bar{flex:1;height:8px;background:#eef0f3;border-radius:4px;overflow:hidden}
.stage .bar i{display:block;height:100%;background:var(--accent);border-radius:4px}
.stage .bar i.fail{background:var(--fail)}
.stage .t{width:76px;flex:none;text-align:right;font-family:var(--mono);font-size:12px;color:var(--muted)}
pre{background:#12161c;color:#d6dee8;padding:14px 16px;border-radius:10px;overflow:auto;
  font-family:var(--mono);font-size:12.5px;line-height:1.55;max-height:340px}
.meta{font-size:13px;color:var(--muted);font-family:var(--mono);line-height:1.9;
  background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;overflow-x:auto}
.meta b{color:var(--ink);font-weight:600}
.advice{background:var(--accent-bg);border:1px solid #c9dcf3;border-radius:10px;padding:14px 18px;font-size:14px}
.advice b{color:var(--accent)}
footer{margin-top:40px;color:var(--muted);font-size:12px;text-align:center;font-family:var(--mono)}
@media print{body{background:#fff}.card,table,.meta,.advice{border-color:#ccc}}
"""

VERDICT_CLASS = {"PASS": "pass", "FAIL": "fail", "SKIP": "skip"}


def _e(v) -> str:
    """转义，顺便把 None / 空值统一成 —"""
    if v is None or v == "":
        return "—"
    return html.escape(str(v))


def _tag(verdict: str) -> str:
    cls = VERDICT_CLASS.get(str(verdict).upper(), "skip")
    return f'<span class="tag {cls}">{_e(verdict)}</span>'


def _card(label: str, value, unit: str = "") -> str:
    if value is None:
        return f'<div class="card"><div class="k">{_e(label)}</div><div class="v">—</div></div>'
    unit_html = f'<span class="u">{_e(unit)}</span>' if unit else ""
    return (f'<div class="card"><div class="k">{_e(label)}</div>'
            f'<div class="v">{_e(value)}{unit_html}</div></div>')


def render(m: dict) -> str:
    verdict = str(m.get("verdict", "FAIL")).upper()
    vcls = "pass" if verdict == "PASS" else "fail"
    mt = m.get("metrics") or {}
    tests = m.get("tests") or {}
    checks = m.get("checks") or []
    stages = m.get("stages") or []
    mission = m.get("mission") or {}
    log = m.get("log") or {}
    git = m.get("git") or {}
    env = m.get("env") or {}

    # ---- 顶部结论
    why = []
    if verdict == "PASS":
        why.append(f"{tests.get('passed', 0)} 项断言全部通过")
    else:
        if tests.get("failed"):
            why.append(f"{tests.get('failed')} 项断言失败")
        failed_checks = [c.get("name") for c in checks if str(c.get("verdict")).upper() == "FAIL"]
        if failed_checks:
            why.append("体检未通过：" + "、".join(failed_checks))
        if m.get("abort_reason"):
            why.append("飞行中止：" + str(m["abort_reason"]))
    why_text = "；".join(why) or "—"

    parts: list[str] = []
    parts.append(f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>飞行测试报告 · {_e(m.get('campaign_id'))}</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>飞行测试报告</h1>
<div class="sub">{_e(m.get('campaign_id'))} · 模式 {_e(m.get('mode'))} · {_e(m.get('started_at'))}</div>

<div class="banner {vcls}">
  <div class="verdict">{'PASS' if verdict == 'PASS' else 'FAIL'}</div>
  <div class="why">{_e(why_text)}</div>
</div>""")

    # ---- 关键指标
    parts.append('<div class="cards">')
    parts.append(_card("日志时长", mt.get("duration_s"), "s"))
    parts.append(_card("稳定悬停", mt.get("hover_s"), "s"))
    parts.append(_card("横滚 / 俯仰",
                       (f'{mt.get("roll_half_deg"):.2f} / {mt.get("pitch_half_deg"):.2f}'
                        if mt.get("roll_half_deg") is not None else None), "°"))
    parts.append(_card("高度漂移", (f'{mt["alt_drift_m"]:.2f}' if mt.get("alt_drift_m") is not None else None), "m"))
    parts.append(_card("悬停油门", (f'{mt["throttle_mean"] * 100:.1f}' if mt.get("throttle_mean") is not None else None), "%"))
    parts.append(_card("角速度峰值", (f'{mt["gyro_peak_dps"]:.1f}' if mt.get("gyro_peak_dps") is not None else None), "°/s"))
    parts.append('</div>')

    # ---- 判定明细
    parts.append("<h2>体检判定</h2>")
    if checks:
        rows = "".join(
            f"<tr><td>{_e(c.get('name'))}</td><td>{_tag(c.get('verdict', 'SKIP'))}</td>"
            f"<td class='detail'>{_e(c.get('detail'))}</td></tr>"
            for c in checks
        )
        parts.append(f"<table><thead><tr><th>检查项</th><th>结论</th>"
                     f"<th>实测 / 标准</th></tr></thead><tbody>{rows}</tbody></table>")
    else:
        parts.append('<p class="detail">无判定数据。</p>')

    # ---- 用例明细
    parts.append("<h2>测试用例</h2>")
    cases = tests.get("cases") or []
    if cases:
        rows = "".join(
            f"<tr><td class='mono'>{_e(c.get('name'))}</td><td>{_tag(c.get('outcome', 'SKIP'))}</td>"
            f"<td class='num'>{_e(c.get('duration_s'))}</td>"
            f"<td class='detail'>{_e((c.get('message') or '').strip()[:300])}</td></tr>"
            for c in cases
        )
        summary = (f"共 {tests.get('total', 0)} 项：通过 {tests.get('passed', 0)}、"
                   f"失败 {tests.get('failed', 0)}、跳过 {tests.get('skipped', 0)}，"
                   f"耗时 {tests.get('duration_s', 0):.2f}s")
        parts.append(f'<p class="detail">{_e(summary)}</p>')
        parts.append(f"<table><thead><tr><th>用例</th><th>结果</th><th class='num'>耗时(s)</th>"
                     f"<th>失败信息</th></tr></thead><tbody>{rows}</tbody></table>")
    else:
        parts.append('<p class="detail">未执行用例（或解析 junit 失败）。</p>')

    # ---- 失败建议
    if verdict != "PASS" and m.get("advice"):
        parts.append("<h2>排查建议</h2>")
        parts.append(f'<div class="advice">{m["advice"]}</div>')

    # ---- 流水线阶段
    parts.append("<h2>流水线阶段</h2>")
    if stages:
        total = max((s.get("duration_s") or 0) for s in stages) or 1.0
        rows = []
        for s in stages:
            d = s.get("duration_s") or 0.0
            pct = max(2.0, min(100.0, d / total * 100))
            cls = "" if str(s.get("status")).upper() in ("OK", "PASS") else "fail"
            rows.append(
                f'<div class="stage"><div class="name">{_e(s.get("name"))}</div>'
                f'<div class="bar"><i class="{cls}" style="width:{pct:.1f}%"></i></div>'
                f'<div class="t">{d:.2f}s</div></div>'
            )
        parts.append("".join(rows))
        parts.append('<p class="detail">阶段耗时按最长一段归一化，便于看出瓶颈。</p>')
    else:
        parts.append('<p class="detail">无阶段数据。</p>')

    # ---- 飞行科目
    if mission:
        parts.append("<h2>飞行科目</h2>")
        pf = mission.get("preflight") or []
        if pf:
            rows = "".join(
                f"<tr><td>{_e(c.get('name'))}</td><td>{_tag('PASS' if c.get('ok') else 'FAIL')}</td>"
                f"<td class='detail'>{_e(c.get('detail'))}</td></tr>" for c in pf
            )
            parts.append("<table><thead><tr><th>放行检查</th><th>结论</th>"
                         f"<th>说明</th></tr></thead><tbody>{rows}</tbody></table>")
        cards = ['<div class="cards">']
        cards.append(_card("起飞", "成功" if mission.get("takeoff_ok") else "失败"))
        cards.append(_card("降落", "成功" if mission.get("land_ok") else "失败"))
        cards.append(_card("悬停目标", mission.get("hover_target_s"), "s"))
        cards.append(_card("悬停实际", mission.get("hover_actual_s"), "s"))
        cards.append(_card("最大高度", mission.get("max_alt_m"), "m"))
        cards.append(_card("最大水平距离", mission.get("max_radius_m"), "m"))
        cards.append(_card("遥测样本", mission.get("samples"), "点"))
        cards.append('</div>')
        parts.append("".join(cards))
        events = mission.get("events") or []
        if events:
            parts.append("<h2>飞控事件流</h2>")
            parts.append(f"<pre>{_e(chr(10).join(events))}</pre>")

    # ---- 元信息
    parts.append("<h2>可追溯信息</h2>")
    meta_rows = [
        ("日志文件", log.get("path")),
        ("日志大小", f"{log.get('size_mb')} MB" if log.get("size_mb") is not None else None),
        ("日志 SHA256", log.get("sha256")),
        ("代码版本", f"{git.get('sha')}{'（有未提交改动）' if git.get('dirty') else ''}"),
        ("运行模式", m.get("mode")),
        ("SITL 机型", m.get("sitl_model")),
        ("Python", env.get("python")),
        ("平台", env.get("platform")),
        ("MAVSDK", env.get("mavsdk")),
        ("pytest", env.get("pytest")),
        ("生成时间", m.get("finished_at")),
    ]
    meta_html = "".join(f"<div><b>{_e(k)}</b>　{_e(v)}</div>" for k, v in meta_rows if v not in (None, ""))
    parts.append(f'<div class="meta">{meta_html}</div>')

    parts.append(f"""<footer>drone-lab · 飞行测试流水线自动生成 · 退出码 {_e(m.get('exit_code'))}</footer>
</div></body></html>""")
    return "".join(parts)


def write(m: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render(m), encoding="utf-8")
    return p


def _json_default(o):
    """pyulog / numpy 会把标量包成 numpy 类型，json 不认识它们，统一降级。

    这是被真机日志咬过一次的地方：hover_samples 是 numpy.int64，
    直接 dumps 会抛 TypeError，而它只在有悬停段的日志上才出现。
    """
    if hasattr(o, "item"):
        return o.item()
    return str(o)


def write_manifest(m: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2, default=_json_default),
                 encoding="utf-8")
    return p
