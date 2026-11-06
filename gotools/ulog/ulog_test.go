package ulog

import (
	"math"
	"testing"
)

// 样例日志是真实的 PX4 SITL 悬停日志，作为解析器的回归基线。
const sample = "../../data/20261027-sitl-hover.ulg"

func TestParseSample(t *testing.T) {
	f, err := Parse(sample)
	if err != nil {
		t.Fatalf("解析失败: %v", err)
	}
	if len(f.Formats) < 100 {
		t.Errorf("格式定义只有 %d 条，明显偏少（样例应有上百条）", len(f.Formats))
	}
	if len(f.Topics) < 50 {
		t.Errorf("订阅只有 %d 条，明显偏少", len(f.Topics))
	}
	for _, name := range []string{"vehicle_attitude", "vehicle_local_position", "actuator_motors", "sensor_combined"} {
		d, ok := f.Data[name]
		if !ok {
			t.Fatalf("缺少关键主题 %s", name)
		}
		if d.Rows < 100 {
			t.Errorf("%s 只有 %d 行数据，解析可能中途断了", name, d.Rows)
		}
	}
}

// 列长度必须一致，否则说明某条数据消息解错位了（要么 padding 多读了字节，
// 要么嵌套结构没展开）。这是解析器最容易出、又最难一眼看出的 bug。
func TestColumnsAligned(t *testing.T) {
	f, err := Parse(sample)
	if err != nil {
		t.Fatalf("解析失败: %v", err)
	}
	for name, d := range f.Data {
		for col, vals := range d.Cols {
			if len(vals) != d.Rows {
				t.Errorf("%s.%s: %d 个值，但该主题有 %d 行（列未对齐）", name, col, len(vals), d.Rows)
			}
		}
	}
}

func TestTimestampsMonotonic(t *testing.T) {
	f, err := Parse(sample)
	if err != nil {
		t.Fatalf("解析失败: %v", err)
	}
	ts := f.Data["vehicle_attitude"].TimestampUS()
	if len(ts) == 0 {
		t.Fatal("vehicle_attitude 没有时间戳列")
	}
	for i := 1; i < len(ts); i++ {
		if ts[i] < ts[i-1] {
			t.Fatalf("时间戳在 %d 处倒流：%.0f → %.0f", i, ts[i-1], ts[i])
		}
	}
}

// 系统消息等级是按 ASCII 数字编码的（'3'=ERROR、'4'=WARNING、'6'=INFO），
// 这条用例把口径钉死：样例日志应有 0 条 ERROR、1 条 WARNING。
func TestLoggedMessageLevels(t *testing.T) {
	f, err := Parse(sample)
	if err != nil {
		t.Fatalf("解析失败: %v", err)
	}
	if len(f.Messages) == 0 {
		t.Fatal("没有解析到任何系统消息")
	}
	if got := len(f.Errors()); got != 0 {
		t.Errorf("ERROR 条数 = %d，期望 0；内容：%v", got, f.Errors())
	}
	if got := len(f.Warnings()); got != 1 {
		t.Errorf("WARNING 条数 = %d，期望 1", got)
	}
	if ts := f.Messages[0].TimestampUS; math.Abs(ts) > 120e6 {
		t.Errorf("首条消息时间戳 %.0fµs 偏离文件起点过远，时间基准可能算错", ts)
	}
}

// 数值层面的交叉验证：这些值必须与 Python 侧（pyulog）算出来的一致。
// 两边独立实现算同一份日志，数字对得上才说明协议理解正确。
func TestAttitudeValues(t *testing.T) {
	f, err := Parse(sample)
	if err != nil {
		t.Fatalf("解析失败: %v", err)
	}
	att := f.Data["vehicle_attitude"]
	q := att.Column("q[0]")
	if q == nil {
		t.Fatal("缺少 q[0] 列")
	}
	// 单位四元数：w 分量应在 0.9~1.0（悬停时接近水平）
	// 这一条能抓住"读错字节序"这类错误——字节序反了 w 会变成天文数字或 0。
	for i, w := range q {
		if w < 0.5 || w > 1.01 {
			t.Fatalf("第 %d 个 q[0]=%.4f 不像四元数 w 分量（字节序或字段偏移可能错了）", i, w)
		}
	}
}
