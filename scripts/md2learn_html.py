#!/usr/bin/env python3
"""把 docs/learn 下的 Markdown 转成自包含 HTML（内联 SVG，无外部依赖）。

用法：
    python3 scripts/md2learn_html.py

产物：docs/learn/<同名>.html
- 图片引用 ![alt](figures/x.svg) 会被替换为内联 SVG（给每个 id 加 figN- 前缀，避免同页 id 冲突）
- 输出单文件 HTML，可直接放 GitHub Pages / 双击本地打开 / 手机上读
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "docs" / "learn"

CSS = """
:root{
  --bg:#f6f7f9; --card:#ffffff; --ink:#1b1f24; --muted:#6a737d; --line:#e3e6ea;
  --accent:#0b5cad; --accent-bg:#eaf2fc; --code:#eef1f4;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.85 -apple-system,BlinkMacSystemFont,"Helvetica Neue","PingFang SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.wrap{max-width:820px;margin:0 auto;padding:40px 22px 100px}
article{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:44px 52px 56px;box-shadow:0 1px 2px rgba(27,31,36,.04)}
h1{font-size:30px;line-height:1.35;letter-spacing:-.3px;margin:0 0 22px;
  padding-bottom:18px;border-bottom:2px solid var(--line)}
h2{font-size:22px;letter-spacing:-.2px;margin:52px 0 16px;padding-top:6px;
  color:#11161c;scroll-margin-top:20px}
h3{font-size:17.5px;margin:34px 0 12px;color:#1b1f24}
p{margin:0 0 16px}
strong{font-weight:600;color:#0d1117}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(11,92,173,.28)}
a:hover{border-bottom-color:var(--accent)}
hr{border:0;border-top:1px solid var(--line);margin:44px 0}
blockquote{margin:22px 0;padding:14px 20px;background:var(--accent-bg);
  border-left:3px solid var(--accent);border-radius:0 8px 8px 0;color:#22303f}
blockquote p:last-child{margin-bottom:0}
ul,ol{margin:0 0 16px;padding-left:1.5em}
li{margin:6px 0}
code{font-family:var(--mono);font-size:.88em;background:var(--code);
  padding:1.5px 6px;border-radius:5px;color:#24292f}
pre{background:#f6f8fa;border:1px solid var(--line);border-radius:9px;
  padding:16px 18px;overflow-x:auto;margin:18px 0;line-height:1.6}
pre code{background:none;padding:0;font-size:13.5px}
table{width:100%;border-collapse:collapse;margin:20px 0;font-size:14.5px;display:block;overflow-x:auto}
table thead th{background:#f2f5f8;font-weight:600;text-align:left}
th,td{border:1px solid var(--line);padding:9px 13px;vertical-align:top}
tbody tr:nth-child(even){background:#fafbfc}
figure{margin:26px 0;padding:18px 16px 14px;background:#fcfdfe;
  border:1px solid var(--line);border-radius:11px;text-align:center}
figure svg{max-width:100%;height:auto;display:block;margin:0 auto}
figcaption{margin-top:12px;font-size:13px;color:var(--muted)}
details{margin:16px 0;padding:12px 18px;background:#f8fafc;
  border:1px solid var(--line);border-radius:9px}
details summary{cursor:pointer;font-weight:600;color:var(--accent);outline:none}
details[open] summary{margin-bottom:10px}
.lead{color:var(--muted)}
.footer{margin-top:34px;padding-top:18px;border-top:1px solid var(--line);
  color:var(--muted);font-size:13px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
@media (max-width:680px){
  .wrap{padding:16px 10px 60px}
  article{padding:26px 18px 34px;border-radius:10px}
  h1{font-size:24px}
  h2{font-size:19px;margin-top:38px}
  body{font-size:15.5px}
}
@media print{
  body{background:#fff}
  article{border:0;box-shadow:none;padding:0}
  h2{page-break-after:avoid}
  figure,table,pre{page-break-inside:avoid}
}
"""

FIG_RE = re.compile(r"!\[([^\]]*)\]\((figures/[^)]+)\)")
# <details> 内的 Markdown 需要单独转换：Python-Markdown 默认把块级 HTML 内部当原始文本
DETAILS_RE = re.compile(r"<details>\s*(<summary>.*?</summary>)?(.*?)</details>", re.S)


def _md() -> "markdown.Markdown":
    return markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "sane_lists", "md_in_html"]
    )


def inline_svg(svg_path: Path, idx: int) -> str:
    """读 SVG，去掉 XML 声明，给 id 加前缀避免同页冲突，返回可内联的字符串。"""
    raw = svg_path.read_text(encoding="utf-8")
    raw = re.sub(r"<\?xml[^>]*\?>", "", raw).strip()
    raw = re.sub(r"<!DOCTYPE[^>]*>", "", raw).strip()

    prefix = f"fig{idx}-"
    ids = re.findall(r'\sid="([^"]+)"', raw)
    for old in sorted(set(ids), key=len, reverse=True):
        new = prefix + old
        raw = raw.replace(f'id="{old}"', f'id="{new}"')
        raw = raw.replace(f"url(#{old})", f"url(#{new})")
        raw = raw.replace(f'href="#{old}"', f'href="#{new}"')
    return raw


def convert(md_path: Path) -> Path:
    text = md_path.read_text(encoding="utf-8")

    # 网页版不需要「用 VS Code 打开」这句，换成对当前页的说明
    text = text.replace(
        "用 VS Code 打开本文件按 `Cmd+Shift+V`（Windows 是 `Ctrl+Shift+V`）就能边读边看图，不用装插件。",
        "**本页是网页版**，七张图已直接内联，往下读即可；源文件是 Markdown，在 VS Code 里按 `Cmd+Shift+V` 也能同样看图。",
    )

    # 0. <details> 折叠块：内部内容单独跑一遍 markdown，否则有序列表会塌成一段
    details: list[str] = []

    def stash_details(m: re.Match) -> str:
        summary = (m.group(1) or "<summary>展开</summary>").strip()
        inner = m.group(2).strip()
        inner_html = _md().convert(inner) if inner else ""
        details.append(f"<details>\n{summary}\n{inner_html}\n</details>")
        return f"\n\n@@DET{len(details) - 1}@@\n\n"

    text = DETAILS_RE.sub(stash_details, text)

    # 1. 再把图抽出来，占位，避免 markdown 把 SVG 当行内 HTML 处理
    figures: list[str] = []

    def stash(m: re.Match) -> str:
        alt, rel = m.group(1), m.group(2)
        svg_path = SRC_DIR / rel
        if not svg_path.exists():
            print(f"  ! 缺图 {rel}", file=sys.stderr)
            return f"<p><em>（缺图：{rel}）</em></p>"
        figures.append(f"<figure>{inline_svg(svg_path, len(figures) + 1)}"
                       f"<figcaption>{html.escape(alt)}</figcaption></figure>")
        return f"\n\n@@FIG{len(figures) - 1}@@\n\n"

    text = FIG_RE.sub(stash, text)

    body = _md().convert(text)

    body = re.sub(r"<p>@@FIG(\d+)@@</p>", lambda m: figures[int(m.group(1))], body)
    body = re.sub(r"@@FIG(\d+)@@", lambda m: figures[int(m.group(1))], body)
    body = re.sub(r"<p>@@DET(\d+)@@</p>", lambda m: details[int(m.group(1))], body)
    body = re.sub(r"@@DET(\d+)@@", lambda m: details[int(m.group(1))], body)

    # 标题取首行 H1
    m = re.search(r"^#\s+(.+)$", md_path.read_text(encoding="utf-8"), re.M)
    title = m.group(1).strip() if m else md_path.stem

    out = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · drone-lab</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<article>
{body}
<div class="footer">
  <span>drone-lab · 学习笔记</span>
  <span><a href="../../">← 返回实验室首页</a></span>
</div>
</article>
</div></body></html>
"""
    out_path = md_path.with_suffix(".html")
    out_path.write_text(out, encoding="utf-8")
    return out_path


def main() -> int:
    mds = sorted(p for p in SRC_DIR.glob("*.md") if p.name != "README.md")
    if not mds:
        print("没找到 md 文件", file=sys.stderr)
        return 1
    for p in mds:
        out = convert(p)
        kb = out.stat().st_size / 1024
        print(f"✓ {p.name} → {out.name}  ({kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
