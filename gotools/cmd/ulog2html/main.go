// Command ulog2html 把 PX4 的 .ulg 飞行日志转成一份自包含的 HTML 报告。
//
// 纯 Go 实现，不依赖 Python/pyulog：编出来是一个可执行文件，拖到任何机器上都能跑。
//
// 用法：
//
//	ulog2html data/20261027-sitl-hover.ulg              # 输出同名 .html
//	ulog2html data/xxx.ulg -o reports/xxx.html
//	ulog2html data/xxx.ulg -quiet                        # 只输出文件路径
//
// 退出码：判定全部通过 0，有 FAIL 1，参数/解析错误 2。
// 退出码可以直接串进流水线：`ulog2html x.ulg || echo "体检没过"`
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/Hedefang-hdf/drone-lab/gotools/hover"
	"github.com/Hedefang-hdf/drone-lab/gotools/report"
	"github.com/Hedefang-hdf/drone-lab/gotools/ulog"
)

const version = "0.1.0"

func main() {
	out := flag.String("o", "", "输出文件路径（默认 reports/<日志名>.html）")
	quiet := flag.Bool("quiet", false, "只输出产物路径")
	showVer := flag.Bool("version", false, "打印版本")
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "ulog2html %s — PX4 .ulg → HTML 报告（纯 Go，零依赖）\n\n", version)
		fmt.Fprintf(os.Stderr, "用法: ulog2html <file.ulg> [-o out.html] [-quiet]\n\n")
		flag.PrintDefaults()
	}
	// Go 的 flag 包遇到第一个非 flag 参数就停止解析，
	// 而大家习惯把文件写在前面（`ulog2html x.ulg -o y.html`），所以先把参数归位。
	flag.CommandLine.Parse(reorderArgs(os.Args[1:]))

	if *showVer {
		fmt.Println("ulog2html", version)
		return
	}
	if flag.NArg() < 1 {
		flag.Usage()
		os.Exit(2)
	}

	in := flag.Arg(0)
	f, err := ulog.Parse(in)
	if err != nil {
		fmt.Fprintf(os.Stderr, "解析失败: %v\n", err)
		os.Exit(2)
	}

	th := hover.Default()
	m, checks := hover.Analyze(f, th)

	dest := *out
	if dest == "" {
		base := strings.TrimSuffix(filepath.Base(in), filepath.Ext(in))
		dest = filepath.Join("reports", base+".html")
	}

	opt := report.Options{
		Title:  "飞行日志体检报告",
		Source: in,
		Tool:   "ulog2html " + version,
	}
	if err := report.Write(dest, f, m, checks, opt); err != nil {
		fmt.Fprintf(os.Stderr, "写报告失败: %v\n", err)
		os.Exit(2)
	}

	if *quiet {
		fmt.Println(dest)
	} else {
		fmt.Printf("日志时长 %.1f s ｜ 稳定悬停段 %.1f s\n", m.DurationS, m.HoverS)
		for _, c := range checks {
			fmt.Printf("[%-4s] %-8s %s\n", c.Verdict, c.Name, c.Detail)
		}
		fmt.Printf("报告：%s\n", dest)
	}

	if !hover.Passed(checks) {
		os.Exit(1)
	}
}

// reorderArgs 把 -flag 提到位置参数前面，支持 `ulog2html x.ulg -o y.html` 这种写法。
func reorderArgs(args []string) []string {
	takesValue := map[string]bool{"-o": true}
	var flags, positional []string
	for i := 0; i < len(args); i++ {
		a := args[i]
		if strings.HasPrefix(a, "-") && a != "-" {
			flags = append(flags, a)
			if takesValue[a] && i+1 < len(args) {
				i++
				flags = append(flags, args[i])
			}
			continue
		}
		positional = append(positional, a)
	}
	return append(flags, positional...)
}
