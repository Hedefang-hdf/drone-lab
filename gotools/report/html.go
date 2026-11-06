// Package report 把判读结果渲染成单文件 HTML。
//
// 图表用手写 SVG 而不是引 Chart.js：报告要在没有网络的机器上打开，
// 也要能直接塞进邮件——外部依赖一律不要。
package report

import (
	"fmt"
	"html"
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/Hedefang-hdf/drone-lab/gotools/hover"
	"github.com/Hedefang-hdf/drone-lab/gotools/ulog"
)

const css = `
:root{--bg:#f6f7f9;--card:#fff;--ink:#1b1f24;--muted:#6a737d;--line:#e3e6ea;
--pass:#1a7f37;--pass-bg:#e9f6ec;--fail:#c9252d;--fail-bg:#fdeceb;--skip:#8a6d00;
--skip-bg:#fdf5d9;--accent:#0b5cad;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:32px 24px 64px}
h1{font-size:24px;margin:0 0 4px;letter-spacing:-.2px}
h2{font-size:16px;margin:32px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.sub{color:var(--muted);font-size:13px;font-family:var(--mono)}
.banner{display:flex;align-items:center;gap:14px;padding:20px 24px;border-radius:12px;margin:20px 0 8px;border:1px solid var(--line);background:var(--card)}
.banner.pass{border-color:#bfe3c8;background:var(--pass-bg)}
.banner.fail{border-color:#f3c2c0;background:var(--fail-bg)}
.verdict{font-size:22px;font-weight:700;letter-spacing:.4px}
.banner.pass .verdict{color:var(--pass)}.banner.fail .verdict{color:var(--fail)}
.banner .why{color:var(--muted);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:12px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{font-size:12px;color:var(--muted);margin-bottom:6px}
.card .v{font-size:20px;font-weight:650;font-family:var(--mono);letter-spacing:-.5px}
.card .u{font-size:12px;color:var(--muted);font-weight:400;margin-left:2px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:14px}
th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#fafbfc;font-size:12px;color:var(--muted);font-weight:600}
tr:last-child td{border-bottom:none}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:650;font-family:var(--mono)}
.tag.pass{color:var(--pass);background:var(--pass-bg)}
.tag.fail{color:var(--fail);background:var(--fail-bg)}
.tag.skip{color:var(--skip);background:var(--skip-bg)}
.detail{color:var(--muted);font-size:13px}
.mono{font-family:var(--mono);font-size:13px}
.chart{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 8px;margin:12px 0}
.meta{font-size:13px;color:var(--muted);font-family:var(--mono);line-height:1.9;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;overflow-x:auto}
.meta b{color:var(--ink);font-weight:600}
pre{background:#12161c;color:#d6dee8;padding:14px 16px;border-radius:10px;overflow:auto;font-family:var(--mono);font-size:12.5px;max-height:300px}
footer{margin-top:40px;color:var(--muted);font-size:12px;text-align:center;font-family:var(--mono)}
`

// Options 控制报告渲染。
type Options struct {
	Title  string
	Source string
	Tool   string
}

// Write 渲染并写出 HTML。
func Write(path string, f *ulog.File, m *hover.Metrics, checks []hover.CheckResult, opt Options) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(Render(f, m, checks, opt)), 0o644)
}

// Render 生成完整 HTML。
func Render(f *ulog.File, m *hover.Metrics, checks []hover.CheckResult, opt Options) string {
	passed := hover.Passed(checks)
	var b strings.Builder

	b.WriteString("<!DOCTYPE html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">\n")
	b.WriteString("<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n")
	fmt.Fprintf(&b, "<title>%s · %s</title>\n<style>%s</style></head><body><div class=\"wrap\">\n",
		esc(opt.Title), esc(filepath.Base(opt.Source)), css)

	fmt.Fprintf(&b, "<h1>%s</h1>\n<div class=\"sub\">%s · %s</div>\n",
		esc(opt.Title), esc(filepath.Base(opt.Source)), time.Now().Format("2006-01-02 15:04:05"))

	cls, word, why := "fail", "FAIL", buildWhy(checks, m)
	if passed {
		cls, word = "pass", "PASS"
	}
	fmt.Fprintf(&b, "<div class=\"banner %s\"><div class=\"verdict\">%s</div><div class=\"why\">%s</div></div>\n",
		cls, word, esc(why))

	// 指标卡片
	b.WriteString("<div class=\"cards\">\n")
	card := func(k, v, u string) {
		if v == "" {
			v = "—"
			u = ""
		}
		if u != "" {
			u = fmt.Sprintf("<span class=\"u\">%s</span>", esc(u))
		}
		fmt.Fprintf(&b, "<div class=\"card\"><div class=\"k\">%s</div><div class=\"v\">%s%s</div></div>\n", esc(k), esc(v), u)
	}
	card("日志时长", fmt.Sprintf("%.1f", m.DurationS), "s")
	if m.HoverFound {
		card("稳定悬停", fmt.Sprintf("%.1f", m.HoverS), "s")
	} else {
		card("稳定悬停", "", "")
	}
	card("横滚 / 俯仰", num2(m.RollHalfDeg)+" / "+num2(m.PitchHalfDeg), "°")
	card("高度漂移", num2(m.AltDriftM), "m")
	card("悬停油门", pct(m.ThrottleMean), "%")
	card("角速度峰值", num1(m.GyroPeakDPS), "°/s")
	b.WriteString("</div>\n")

	// 判定表
	b.WriteString("<h2>判定明细</h2>\n<table><thead><tr><th>检查项</th><th>结论</th><th>实测 / 标准</th></tr></thead><tbody>\n")
	for _, c := range checks {
		fmt.Fprintf(&b, "<tr><td>%s</td><td>%s</td><td class=\"detail\">%s</td></tr>\n",
			esc(c.Name), tag(c.Verdict), esc(c.Detail))
	}
	b.WriteString("</tbody></table>\n")

	// 图表
	if len(m.AltitudeSeries) > 2 {
		b.WriteString("<h2>悬停段高度</h2>\n")
		b.WriteString(chartCard(m.AltitudeSeries, "高度 (m)", "#0b5cad"))
	}
	if len(m.AttitudeSeries) > 2 {
		b.WriteString("<h2>悬停段姿态</h2>\n")
		b.WriteString(attitudeCard(m.AttitudeSeries))
	}

	// 系统消息
	allMsgs := f.Messages
	if len(allMsgs) > 0 {
		b.WriteString("<h2>系统消息</h2>\n<pre>")
		for _, msg := range allMsgs {
			fmt.Fprintf(&b, "[%c] %s\n", msg.Level, esc(msg.Message))
		}
		b.WriteString("</pre>\n")
	}

	// 元信息
	b.WriteString("<h2>可追溯信息</h2>\n<div class=\"meta\">\n")
	meta := func(k, v string) {
		if v == "" {
			return
		}
		fmt.Fprintf(&b, "<div><b>%s</b>　%s</div>\n", esc(k), esc(v))
	}
	meta("日志文件", filepath.Base(opt.Source))
	meta("解析工具", opt.Tool)
	meta("文件起始时间戳", fmt.Sprintf("%.0f µs", f.StartTimeUS))
	meta("固件版本", f.Info["ver_sw"])
	meta("硬件型号", firstNonEmpty(f.Info["ver_hw"], f.Info["sys_name"]))
	meta("机架类型", f.Info["sys_mcu"])
	meta("订阅主题数", fmt.Sprintf("%d", len(f.Topics)))
	meta("解析主题数", fmt.Sprintf("%d", len(f.Data)))
	meta("丢包次数", fmt.Sprintf("%d", f.Dropouts))
	b.WriteString("</div>\n")

	fmt.Fprintf(&b, "<footer>drone-lab · ulog2html（纯 Go 实现，零外部依赖）· %s</footer>\n", esc(opt.Tool))
	b.WriteString("</div></body></html>\n")
	return b.String()
}

// ---------------------------------------------------------------- 图表（手写 SVG）

// maxPoints 是单条曲线的最大点数。原始日志采样率 200~500Hz，
// 直接画会有上万段线，文件大且浏览器卡；等距抽稀到 600 点肉眼无差别。
const maxPoints = 600

func decimateAltitude(pts []hover.Point) []hover.Point {
	if len(pts) <= maxPoints {
		return pts
	}
	stride := float64(len(pts)-1) / float64(maxPoints-1)
	out := make([]hover.Point, 0, maxPoints)
	for i := 0; i < maxPoints; i++ {
		out = append(out, pts[int(float64(i)*stride)])
	}
	return out
}

func decimateAttitude(pts []hover.AttPoint) []hover.AttPoint {
	if len(pts) <= maxPoints {
		return pts
	}
	stride := float64(len(pts)-1) / float64(maxPoints-1)
	out := make([]hover.AttPoint, 0, maxPoints)
	for i := 0; i < maxPoints; i++ {
		out = append(out, pts[int(float64(i)*stride)])
	}
	return out
}

// chartCard 画一条高度曲线。
func chartCard(pts []hover.Point, label, color string) string {
	return svgChart(decimateAltitude(pts), label, color)
}

func svgChart(pts []hover.Point, label, color string) string {
	const w, h = 920, 220
	const padL, padR, padT, padB = 56, 16, 16, 30
	innerW, innerH := float64(w-padL-padR), float64(h-padT-padB)

	tMin, tMax := pts[0].T, pts[len(pts)-1].T
	vMin, vMax := math.Inf(1), math.Inf(-1)
	for _, p := range pts {
		vMin = math.Min(vMin, p.Alt)
		vMax = math.Max(vMax, p.Alt)
	}
	if vMax-vMin < 0.1 { // 避免零跨度导致除零，同时给曲线留一点视觉空间
		c := (vMax + vMin) / 2
		vMin, vMax = c-0.05, c+0.05
	}
	pad := (vMax - vMin) * 0.08
	vMin -= pad
	vMax += pad

	x := func(t float64) float64 {
		if tMax-tMin == 0 {
			return padL
		}
		return padL + (t-tMin)/(tMax-tMin)*innerW
	}
	y := func(v float64) float64 {
		return padT + innerH - (v-vMin)/(vMax-vMin)*innerH
	}

	var b strings.Builder
	fmt.Fprintf(&b, "<div class=\"chart\"><svg viewBox=\"0 0 %d %d\" width=\"100%%\" height=\"%d\" role=\"img\" aria-label=\"%s\">\n", w, h, h, esc(label))
	// 网格与坐标
	for i := 0; i <= 4; i++ {
		gy := padT + innerH*float64(i)/4
		gv := vMax - (vMax-vMin)*float64(i)/4
		fmt.Fprintf(&b, "<line x1=\"%d\" y1=\"%.1f\" x2=\"%d\" y2=\"%.1f\" stroke=\"#eef0f3\"/>\n", padL, gy, w-padR, gy)
		fmt.Fprintf(&b, "<text x=\"%d\" y=\"%.1f\" font-size=\"11\" fill=\"#6a737d\" text-anchor=\"end\" font-family=\"monospace\">%.2f</text>\n",
			padL-8, gy+4, gv)
	}
	fmt.Fprintf(&b, "<line x1=\"%d\" y1=\"%.1f\" x2=\"%d\" y2=\"%.1f\" stroke=\"#d9dde3\"/>\n", padL, padT+innerH, w-padR, padT+innerH)

	// 曲线
	var path strings.Builder
	for i, p := range pts {
		if i == 0 {
			fmt.Fprintf(&path, "M%.1f,%.1f", x(p.T), y(p.Alt))
		} else {
			fmt.Fprintf(&path, " L%.1f,%.1f", x(p.T), y(p.Alt))
		}
	}
	fmt.Fprintf(&b, "<path d=\"%s\" fill=\"none\" stroke=\"%s\" stroke-width=\"1.6\"/>\n", path.String(), color)

	// 时间轴
	fmt.Fprintf(&b, "<text x=\"%d\" y=\"%d\" font-size=\"11\" fill=\"#6a737d\" font-family=\"monospace\">0s</text>\n", padL, h-8)
	fmt.Fprintf(&b, "<text x=\"%d\" y=\"%d\" font-size=\"11\" fill=\"#6a737d\" text-anchor=\"end\" font-family=\"monospace\">%.1fs</text>\n",
		w-padR, h-8, tMax)
	fmt.Fprintf(&b, "<text x=\"%d\" y=\"%d\" font-size=\"11\" fill=\"#6a737d\" font-family=\"monospace\">%s</text>\n",
		padL, padT+4, esc(label))
	b.WriteString("</svg></div>\n")
	return b.String()
}

// attitudeCard 把横滚与俯仰画在同一张图上。
func attitudeCard(raw []hover.AttPoint) string {
	const w, h = 920, 220
	pts := decimateAttitude(raw)
	const padL, padR, padT, padB = 56, 16, 16, 30
	innerW, innerH := float64(w-padL-padR), float64(h-padT-padB)

	tMax := pts[len(pts)-1].T
	vMax := 0.05
	for _, p := range pts {
		vMax = math.Max(vMax, math.Max(math.Abs(p.Roll), math.Abs(p.Pitch)))
	}
	vMax *= 1.15

	x := func(t float64) float64 {
		if tMax == 0 {
			return padL
		}
		return padL + t/tMax*innerW
	}
	y := func(v float64) float64 {
		return padT + innerH/2 - v/vMax*(innerH/2)
	}

	var b strings.Builder
	fmt.Fprintf(&b, "<div class=\"chart\"><svg viewBox=\"0 0 %d %d\" width=\"100%%\" height=\"%d\" role=\"img\" aria-label=\"姿态曲线\">\n", w, h, h)
	for i := -2; i <= 2; i++ {
		gv := vMax * float64(i) / 2
		gy := y(gv)
		fmt.Fprintf(&b, "<line x1=\"%d\" y1=\"%.1f\" x2=\"%d\" y2=\"%.1f\" stroke=\"%s\"/>\n",
			padL, gy, w-padR, gy, map[bool]string{true: "#d9dde3", false: "#eef0f3"}[i == 0])
		fmt.Fprintf(&b, "<text x=\"%d\" y=\"%.1f\" font-size=\"11\" fill=\"#6a737d\" text-anchor=\"end\" font-family=\"monospace\">%+.1f</text>\n",
			padL-8, gy+4, gv)
	}

	draw := func(get func(hover.AttPoint) float64, color string) {
		var path strings.Builder
		for i, p := range pts {
			if i == 0 {
				fmt.Fprintf(&path, "M%.1f,%.1f", x(p.T), y(get(p)))
			} else {
				fmt.Fprintf(&path, " L%.1f,%.1f", x(p.T), y(get(p)))
			}
		}
		fmt.Fprintf(&b, "<path d=\"%s\" fill=\"none\" stroke=\"%s\" stroke-width=\"1.4\" opacity=\".9\"/>\n", path.String(), color)
	}
	draw(func(p hover.AttPoint) float64 { return p.Roll }, "#c9252d")
	draw(func(p hover.AttPoint) float64 { return p.Pitch }, "#0b5cad")

	fmt.Fprintf(&b, "<text x=\"%d\" y=\"%d\" font-size=\"11\" fill=\"#6a737d\" font-family=\"monospace\">横滚（红）/ 俯仰（蓝），单位 °</text>\n",
		padL, padT+4)
	fmt.Fprintf(&b, "<text x=\"%d\" y=\"%d\" font-size=\"11\" fill=\"#6a737d\" font-family=\"monospace\">0s</text>\n", padL, h-8)
	fmt.Fprintf(&b, "<text x=\"%d\" y=\"%d\" font-size=\"11\" fill=\"#6a737d\" text-anchor=\"end\" font-family=\"monospace\">%.1fs</text>\n",
		w-padR, h-8, tMax)
	b.WriteString("</svg></div>\n")
	return b.String()
}

// ---------------------------------------------------------------- 小工具

func buildWhy(checks []hover.CheckResult, m *hover.Metrics) string {
	if !m.HoverFound {
		return "未检测到稳定悬停段，稳定性类判据无法判读"
	}
	var failed []string
	for _, c := range checks {
		if c.Verdict == "FAIL" {
			failed = append(failed, c.Name)
		}
	}
	if len(failed) == 0 {
		return fmt.Sprintf("%d 项判定全部通过", len(checks))
	}
	return "未通过：" + strings.Join(failed, "、")
}

func tag(v string) string {
	cls := "skip"
	switch v {
	case "PASS":
		cls = "pass"
	case "FAIL":
		cls = "fail"
	}
	return fmt.Sprintf("<span class=\"tag %s\">%s</span>", cls, esc(v))
}

func esc(s string) string { return html.EscapeString(s) }

func num2(p *float64) string {
	if p == nil {
		return ""
	}
	return fmt.Sprintf("%.2f", *p)
}

func num1(p *float64) string {
	if p == nil {
		return ""
	}
	return fmt.Sprintf("%.1f", *p)
}

func pct(p *float64) string {
	if p == nil {
		return ""
	}
	return fmt.Sprintf("%.1f", *p*100)
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}
