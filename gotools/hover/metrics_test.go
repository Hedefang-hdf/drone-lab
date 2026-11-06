package hover

import (
	"math"
	"testing"

	"github.com/Hedefang-hdf/drone-lab/gotools/ulog"
)

const sample = "../../data/20261027-sitl-hover.ulg"

// 基准值来自 Python 侧 pytools/flightdata.py 对同一份日志的计算结果。
// 两套独立实现必须逐项吻合——本用例就是"双实现一致性"的守门人。
func TestAnalyzeMatchesPythonBaseline(t *testing.T) {
	f, err := ulog.Parse(sample)
	if err != nil {
		t.Fatalf("解析失败: %v", err)
	}
	m, checks := Analyze(f, Default())

	if !m.HoverFound {
		t.Fatal("未识别出悬停段，但样例日志是 25 秒定点悬停")
	}
	cases := []struct {
		name string
		got  float64
		want float64
		tol  float64
	}{
		{"日志时长", m.DurationS, 35.8, 0.2},
		{"悬停段", m.HoverS, 20.8, 0.3},
		{"横滚半幅", deref(m.RollHalfDeg), 0.33, 0.02},
		{"俯仰半幅", deref(m.PitchHalfDeg), 0.35, 0.02},
		{"高度漂移", deref(m.AltDriftM), 0.04, 0.01},
		{"平均高度", deref(m.AltMeanM), 2.50, 0.02},
		{"悬停油门", deref(m.ThrottleMean), 0.489, 0.005},
		{"油门峰值", deref(m.ThrottlePeak), 0.496, 0.005},
		{"角速度峰值", deref(m.GyroPeakDPS), 2.1, 0.2},
	}
	for _, c := range cases {
		if math.Abs(c.got-c.want) > c.tol {
			t.Errorf("%s = %.3f，期望 %.3f±%.3f（与 Python 侧基准不符）",
				c.name, c.got, c.want, c.tol)
		}
	}

	if !Passed(checks) {
		t.Errorf("样例日志应全部通过，实际未通过：%v", checks)
	}
	if len(m.AltitudeSeries) == 0 || len(m.AttitudeSeries) == 0 {
		t.Error("图表数据为空，报告会没有曲线")
	}
}

// 没有悬停段的日志不能"假装通过"：稳定性类判定必须是 SKIP。
func TestNoHoverMeansSkip(t *testing.T) {
	f, err := ulog.Parse(sample)
	if err != nil {
		t.Fatalf("解析失败: %v", err)
	}
	th := Default()
	th.HoverMinAltM = 100 // 不现实的门槛：任何时刻都不算悬停
	m, checks := Analyze(f, th)

	if m.HoverFound {
		t.Fatal("把门槛提到 100m 仍识别出悬停段，窗口判定逻辑有问题")
	}
	for _, c := range checks {
		if c.Name == "系统消息" {
			continue // 系统消息与悬停无关，仍应正常判定
		}
		if c.Verdict != "SKIP" {
			t.Errorf("%s = %s，无悬停段时应为 SKIP（不能默默判 PASS）", c.Name, c.Verdict)
		}
	}
}

// 负向验证：把阈值收紧到不可能达标，判定必须翻转成 FAIL。
// 只会 PASS 的判读代码等于没有判读。
func TestTightThresholdsFlip(t *testing.T) {
	f, err := ulog.Parse(sample)
	if err != nil {
		t.Fatalf("解析失败: %v", err)
	}
	th := Default()
	th.RollDeg = 0.01
	th.PitchDeg = 0.01
	th.AltDriftM = 0.001
	th.GyroDPS = 0.001
	th.ThrottleMin = 0.90 // 油门也要收紧，否则它仍会 PASS，只有 3 项翻转
	th.ThrottleMax = 0.95
	_, checks := Analyze(f, th)

	failed := 0
	for _, c := range checks {
		if c.Verdict == "FAIL" {
			failed++
		}
	}
	if failed < 4 {
		t.Errorf("收紧阈值后只有 %d 项 FAIL，至少应有 4 项翻转——判据可能没真正生效", failed)
	}
}
