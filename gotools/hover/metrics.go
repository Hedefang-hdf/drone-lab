// Package hover 是飞行日志的判读层：先测量，再判定。
//
// 这里刻意与 Python 侧（pytools/flightdata.py）保持**同一套口径**：
// 同样的悬停窗口定义、同样的 5 秒收敛跳过、同样的阈值。
// 目的不是重复实现，而是交叉验证——两套独立实现算同一份日志，
// 数字必须对得上；对不上就说明至少有一边理解错了协议。
package hover

import (
	"fmt"
	"math"

	"github.com/Hedefang-hdf/drone-lab/gotools/ulog"
)

// Thresholds 是判定阈值，与 pytools/flightdata.py 的 Thresholds 一一对应。
type Thresholds struct {
	RollDeg         float64
	PitchDeg        float64
	AltDriftM       float64
	ThrottleMin     float64
	ThrottleMax     float64
	GyroDPS         float64
	HoverMinAltM    float64
	HoverMaxVzMs    float64
	HoverMinSamples int
	SettleSkipS     float64
}

// Default 返回默认阈值。
func Default() Thresholds {
	return Thresholds{
		RollDeg: 1.0, PitchDeg: 1.0,
		AltDriftM:   0.5,
		ThrottleMin: 0.45, ThrottleMax: 0.55,
		GyroDPS:         30.0,
		HoverMinAltM:    0.8,
		HoverMaxVzMs:    0.3,
		HoverMinSamples: 10,
		SettleSkipS:     5.0,
	}
}

// Metrics 是一条日志的全部测量结果。字段为 nil 表示无法测量。
type Metrics struct {
	Source          string
	DurationS       float64
	HoverFound      bool
	HoverStartS     float64
	HoverEndS       float64
	HoverS          float64
	HoverSamples    int
	RollHalfDeg     *float64
	PitchHalfDeg    *float64
	AltDriftM       *float64
	AltMeanM        *float64
	ThrottleMean    *float64
	ThrottlePeak    *float64
	GyroPeakDPS     *float64
	Errors          []string
	Warnings        []string
	MissingTopics   []string
	WindowStartUS   float64
	WindowEndUS     float64
	AltitudeSeries  []Point // 悬停窗口内的高度曲线（供图表使用）
	AttitudeSeries  []AttPoint
}

// Point 是图表用的一个采样点（时间相对悬停起点的秒数 / 高度米）。
type Point struct {
	T   float64
	Alt float64
}

// AttPoint 是姿态曲线采样点。
type AttPoint struct {
	T     float64
	Roll  float64
	Pitch float64
}

// CheckResult 是一项判定。
type CheckResult struct {
	Name    string
	Detail  string
	Verdict string // PASS / FAIL / SKIP
}

var requiredTopics = []string{
	"vehicle_attitude", "vehicle_local_position", "actuator_motors", "sensor_combined",
}

// Analyze 测量并判定一条日志。
func Analyze(f *ulog.File, th Thresholds) (*Metrics, []CheckResult) {
	m := &Metrics{Source: f.Path}

	// 日志时长：所有主题的最后一个时间戳 − 文件头起始时间戳
	var lastTS float64 = f.StartTimeUS
	for _, d := range f.Data {
		if ts := d.TimestampUS(); len(ts) > 0 {
			if v := ts[len(ts)-1]; v > lastTS {
				lastTS = v
			}
		}
	}
	m.DurationS = (lastTS - f.StartTimeUS) / 1e6

	for _, t := range requiredTopics {
		if _, ok := f.Data[t]; !ok {
			m.MissingTopics = append(m.MissingTopics, t)
		}
	}
	for _, e := range f.Errors() {
		m.Errors = append(m.Errors, e.Message)
	}
	for _, w := range f.Warnings() {
		m.Warnings = append(m.Warnings, w.Message)
	}

	pos := f.Data["vehicle_local_position"]
	if pos != nil {
		findHoverWindow(pos, m, th)
	}

	if m.HoverFound {
		measureAttitude(f, m, th)
		measureAltitude(pos, m)
		measureThrottle(f.Data["actuator_motors"], m)
		measureVibration(f.Data["sensor_combined"], m)
	}

	return m, judge(m, th)
}

// findHoverWindow 定位"稳定悬停段"：高度 > 0.8m 且垂直速度 < 0.3m/s，
// 然后跳过开头 5 秒的定高收敛段——收敛过程会被误算成高度漂移（详见 docs/ulog-判读.md）。
func findHoverWindow(pos *ulog.Data, m *Metrics, th Thresholds) {
	ts := pos.TimestampUS()
	z := pos.Column("z")
	vz := pos.Column("vz")
	if ts == nil || z == nil || vz == nil {
		return
	}
	n := min3(len(ts), len(z), len(vz))

	first, last := -1, -1
	count := 0
	for i := 0; i < n; i++ {
		if z[i] < -th.HoverMinAltM && math.Abs(vz[i]) < th.HoverMaxVzMs {
			count++
			if first < 0 {
				first = i
			}
			last = i
		}
	}
	if count < th.HoverMinSamples || first < 0 {
		return
	}

	startUS := ts[first] + th.SettleSkipS*1e6
	endUS := ts[last]
	inWindow := 0
	for i := 0; i < n; i++ {
		if ts[i] >= startUS && ts[i] <= endUS {
			inWindow++
		}
	}
	if inWindow < th.HoverMinSamples {
		return
	}

	m.HoverFound = true
	m.WindowStartUS = startUS
	m.WindowEndUS = endUS
	m.HoverStartS = (startUS - pos.TimestampUS()[0]) / 1e6
	m.HoverEndS = (endUS - pos.TimestampUS()[0]) / 1e6
	m.HoverS = (endUS - startUS) / 1e6
	m.HoverSamples = inWindow
}

func (m *Metrics) inWindow(ts float64) bool {
	return ts >= m.WindowStartUS && ts <= m.WindowEndUS
}

// quatToEulerDeg 四元数 → (roll, pitch)，单位度。
func quatToEulerDeg(w, x, y, z float64) (float64, float64) {
	roll := math.Atan2(2*(w*x+y*z), 1-2*(x*x+y*y))
	pitchArg := 2 * (w*y - z*x)
	pitchArg = math.Max(-1, math.Min(1, pitchArg))
	pitch := math.Asin(pitchArg)
	return roll * 180 / math.Pi, pitch * 180 / math.Pi
}

func measureAttitude(f *ulog.File, m *Metrics, th Thresholds) {
	att := f.Data["vehicle_attitude"]
	if att == nil {
		return
	}
	ts := att.TimestampUS()
	qw, qx, qy, qz := att.Column("q[0]"), att.Column("q[1]"), att.Column("q[2]"), att.Column("q[3]")
	if ts == nil || qw == nil || qx == nil || qy == nil || qz == nil {
		return
	}
	n := min4(len(ts), len(qw), len(qx), len(qy))

	rmin, rmax := math.Inf(1), math.Inf(-1)
	pmin, pmax := math.Inf(1), math.Inf(-1)
	for i := 0; i < n; i++ {
		if !m.inWindow(ts[i]) {
			continue
		}
		r, p := quatToEulerDeg(qw[i], qx[i], qy[i], qz[i])
		rmin, rmax = math.Min(rmin, r), math.Max(rmax, r)
		pmin, pmax = math.Min(pmin, p), math.Max(pmax, p)
		m.AttitudeSeries = append(m.AttitudeSeries, AttPoint{
			T: (ts[i] - m.WindowStartUS) / 1e6, Roll: r, Pitch: p,
		})
	}
	if !math.IsInf(rmin, 0) {
		roll := (rmax - rmin) / 2
		pitch := (pmax - pmin) / 2
		m.RollHalfDeg = &roll
		m.PitchHalfDeg = &pitch
	}
}

func measureAltitude(pos *ulog.Data, m *Metrics) {
	ts := pos.TimestampUS()
	z := pos.Column("z")
	if ts == nil || z == nil {
		return
	}
	n := min3(len(ts), len(z), len(z))
	zmin, zmax := math.Inf(1), math.Inf(-1)
	sum, cnt := 0.0, 0
	for i := 0; i < n; i++ {
		if !m.inWindow(ts[i]) {
			continue
		}
		zmin, zmax = math.Min(zmin, z[i]), math.Max(zmax, z[i])
		sum += z[i]
		cnt++
		m.AltitudeSeries = append(m.AltitudeSeries, Point{T: (ts[i] - m.WindowStartUS) / 1e6, Alt: -z[i]})
	}
	if cnt > 0 {
		drift := zmax - zmin
		mean := -sum / float64(cnt)
		m.AltDriftM = &drift
		m.AltMeanM = &mean
	}
}

func measureThrottle(mot *ulog.Data, m *Metrics) {
	if mot == nil {
		return
	}
	ts := mot.TimestampUS()
	ctrl := mot.Column("control[0]")
	if ts == nil || ctrl == nil {
		return
	}
	n := min3(len(ts), len(ctrl), len(ctrl))
	sum, cnt := 0.0, 0
	peak := math.Inf(-1)
	for i := 0; i < n; i++ {
		if !m.inWindow(ts[i]) {
			continue
		}
		sum += ctrl[i]
		cnt++
		if ctrl[i] > peak {
			peak = ctrl[i]
		}
	}
	if cnt > 0 {
		mean := sum / float64(cnt)
		m.ThrottleMean = &mean
		m.ThrottlePeak = &peak
	}
}

func measureVibration(imu *ulog.Data, m *Metrics) {
	if imu == nil {
		return
	}
	ts := imu.TimestampUS()
	if ts == nil {
		return
	}
	peak := 0.0
	found := false
	for _, axis := range []string{"gyro_rad[0]", "gyro_rad[1]", "gyro_rad[2]"} {
		col := imu.Column(axis)
		if col == nil {
			continue
		}
		found = true
		n := min3(len(ts), len(col), len(col))
		for i := 0; i < n; i++ {
			if !m.inWindow(ts[i]) {
				continue
			}
			if v := math.Abs(col[i]); v > peak {
				peak = v
			}
		}
	}
	if found {
		dps := peak * 180 / math.Pi
		m.GyroPeakDPS = &dps
	}
}

func judge(m *Metrics, th Thresholds) []CheckResult {
	var out []CheckResult
	if !m.HoverFound {
		for _, name := range []string{"姿态稳定性", "高度保持", "悬停油门", "振动水平"} {
			out = append(out, CheckResult{Name: name, Detail: "未检测到稳定悬停段", Verdict: "SKIP"})
		}
	} else {
		out = append(out, verdict("姿态稳定性",
			m.RollHalfDeg != nil && m.PitchHalfDeg != nil &&
				*m.RollHalfDeg <= th.RollDeg && *m.PitchHalfDeg <= th.PitchDeg,
			fmt.Sprintf("roll ±%.2f° pitch ±%.2f° / 标准 ±%.0f°",
				deref(m.RollHalfDeg), deref(m.PitchHalfDeg), th.RollDeg)))

		out = append(out, verdict("高度保持",
			m.AltDriftM != nil && *m.AltDriftM <= th.AltDriftM,
			fmt.Sprintf("漂移 %.2f m（均高 %.2f m）/ 标准 %.1f m",
				deref(m.AltDriftM), deref(m.AltMeanM), th.AltDriftM)))

		out = append(out, verdict("悬停油门",
			m.ThrottleMean != nil && *m.ThrottleMean >= th.ThrottleMin && *m.ThrottleMean <= th.ThrottleMax,
			fmt.Sprintf("均值 %.1f%% 峰值 %.1f%% / 标准 %.0f%%~%.0f%%",
				deref(m.ThrottleMean)*100, deref(m.ThrottlePeak)*100,
				th.ThrottleMin*100, th.ThrottleMax*100)))

		out = append(out, verdict("振动水平",
			m.GyroPeakDPS != nil && *m.GyroPeakDPS <= th.GyroDPS,
			fmt.Sprintf("角速度峰值 %.1f °/s / 标准 %.0f °/s", deref(m.GyroPeakDPS), th.GyroDPS)))
	}

	out = append(out, verdict("系统消息", len(m.Errors) == 0,
		fmt.Sprintf("ERROR %d 个 / WARNING %d 个", len(m.Errors), len(m.Warnings))))
	return out
}

func verdict(name string, ok bool, detail string) CheckResult {
	v := "PASS"
	if !ok {
		v = "FAIL"
	}
	return CheckResult{Name: name, Detail: detail, Verdict: v}
}

func deref(p *float64) float64 {
	if p == nil {
		return 0
	}
	return *p
}

func min3(a, b, c int) int { return minInt(minInt(a, b), c) }
func min4(a, b, c, d int) int { return minInt(minInt(a, b), minInt(c, d)) }

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// Summary 生成一行摘要，与 Python 版格式一致。
func (m *Metrics) Summary() string {
	if !m.HoverFound {
		return fmt.Sprintf("未检测到稳定悬停段（日志 %.1fs）", m.DurationS)
	}
	return fmt.Sprintf("悬停 %.1fs ｜ 姿态 ±%.2f°/±%.2f° ｜ 高度漂移 %.2fm ｜ 油门 %.1f%% ｜ 角速度 %.1f°/s",
		m.HoverS, deref(m.RollHalfDeg), deref(m.PitchHalfDeg), deref(m.AltDriftM),
		deref(m.ThrottleMean)*100, deref(m.GyroPeakDPS))
}

// Passed 表示所有判定都不是 FAIL。
func Passed(checks []CheckResult) bool {
	for _, c := range checks {
		if c.Verdict == "FAIL" {
			return false
		}
	}
	return true
}
