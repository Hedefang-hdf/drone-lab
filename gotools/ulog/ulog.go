// Package ulog 实现 PX4 ULog 飞行日志的纯 Go 解析。
//
// 为什么自己写解析器：Python 侧有 pyulog，但那份实现要拖一整个 Python 环境。
// Go 可以直接吃二进制流，编出来是一个可执行文件——同事拿到日志双击就能出报告。
//
// 文件结构（PX4 规范）：
//
//	文件头 16 字节: 7 字节魔数 "ULog" 0x01 0x12 0x35、1 字节版本、8 字节起始时间戳(µs)
//	之后是消息流，每条: <2 字节长度 LE><1 字节类型><长度字节的载荷>
//
// 各消息的载荷布局（本解析器用到的）：
//
//	F 格式  : "消息名:类型 字段名;类型 字段名;..."（**类型在前，字段名在后**）
//	A 订阅  : multi_id(1) + msg_id(2, LE) + 名称（零结尾）
//	D 数据  : msg_id(2, LE) + 按格式紧凑排列的字段
//	I 信息  : key_len(1) + key[key_len] + 值字节
//	L 消息  : 等级(1, ASCII '0'..'7') + 时间戳(8, LE) + 正文
//	C 丢包  : duration(1)
//
// 注意 msg_id 由文件显式给出，不能按 'A' 的出现顺序自增——早期实现踩过这个坑。
package ulog

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"math"
	"os"
	"sort"
	"strings"
)

// magic 是 ULog 文件头的 7 字节魔数：'U' 'L' 'o' 'g' 0x01 0x12 0x35
var magic = []byte{0x55, 0x4C, 0x6F, 0x67, 0x01, 0x12, 0x35}

// builtinSize 是内置类型的字节宽度。ULog 数据紧凑排列（无对齐填充），
// 按声明顺序逐个读取即可。
var builtinSize = map[string]int{
	"int8_t": 1, "uint8_t": 1, "char": 1, "bool": 1,
	"int16_t": 2, "uint16_t": 2,
	"int32_t": 4, "uint32_t": 4, "float": 4,
	"int64_t": 8, "uint64_t": 8, "double": 8,
}

// levelNames 是 PX4 的日志等级：字节里存的是 ASCII 数字 '0'..'7'。
var levelNames = map[byte]byte{
	'0': 'E', '1': 'E', '2': 'E', '3': 'E', // EMERG/ALERT/CRIT/ERROR
	'4': 'W',                               // WARNING
	'5': 'I', '6': 'I',                     // NOTICE/INFO
	'7': 'D',                               // DEBUG
}

// Field 是格式定义中的一个字段，例如 `float[4] q`。
type Field struct {
	Name string // 字段名，如 q、control
	Type string // 基础类型或嵌套格式名，不含数组维度
	Dims []int  // 数组维度，空表示标量
}

// Format 是一条消息的格式定义。
type Format struct {
	Name   string
	Fields []Field
}

// Topic 是一个订阅：某个格式的第 multiID 个实例，对应文件里的 msgID。
type Topic struct {
	MsgID   uint16
	Name    string
	MultiID uint8
	Format  *Format
}

// Column 保存某一列的全部采样值（时间戳为微秒，float64 可精确表示）。
type Column []float64

// Data 是一个订阅收到的全部数据，按列存储。
type Data struct {
	Name    string
	MultiID uint8
	Cols    map[string]Column
	Rows    int
}

// LoggedMessage 是飞控写入日志的系统消息。
type LoggedMessage struct {
	Level       byte    // 'E' 错误、'W' 警告、'I' 信息、'D' 调试
	TimestampUS float64 // 记录时刻（相对文件起始，µs）
	Message     string
}

// File 是一份解析完成的日志。
type File struct {
	Path        string
	StartTimeUS float64
	Formats     map[string]*Format
	Topics      []*Topic          // 按 msgID 索引，缺失位置为 nil
	Data        map[string]*Data  // 主题名 → 数据
	Messages    []LoggedMessage
	Info        map[string]string
	Dropouts    int
}

// Parse 解析一个 .ulg 文件。
func Parse(path string) (*File, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	f, err := ParseBytes(path, raw)
	return f, err
}

// ParseBytes 解析内存中的 ULog 数据（便于测试）。
func ParseBytes(name string, raw []byte) (*File, error) {
	if len(raw) < 16 {
		return nil, fmt.Errorf("文件太短（%d 字节），不是 ULog", len(raw))
	}
	if !bytes.Equal(raw[:7], magic) {
		return nil, fmt.Errorf("魔数不匹配，不是 ULog 文件（前 7 字节 % X）", raw[:7])
	}

	f := &File{
		Path:        name,
		Formats:     make(map[string]*Format),
		Data:        make(map[string]*Data),
		Info:        make(map[string]string),
		StartTimeUS: float64(binary.LittleEndian.Uint64(raw[8:16])),
	}

	pos := 16
	for pos+3 <= len(raw) {
		size := int(binary.LittleEndian.Uint16(raw[pos : pos+2]))
		typ := raw[pos+2]
		pos += 3
		if pos+size > len(raw) {
			return nil, fmt.Errorf("偏移 %d 处消息越界（声明 %d 字节，剩余 %d）", pos, size, len(raw)-pos)
		}
		if err := f.handle(typ, raw[pos:pos+size]); err != nil {
			return nil, fmt.Errorf("偏移 %d 处解析 %q 消息失败: %w", pos, typ, err)
		}
		pos += size
	}
	return f, nil
}

func (f *File) handle(typ byte, payload []byte) error {
	switch typ {
	case 'F':
		name, fields := parseFormat(payload)
		if name != "" {
			f.Formats[name] = &Format{Name: name, Fields: fields}
		}

	case 'A': // multi_id + msg_id + 名称
		if len(payload) < 4 {
			return nil
		}
		multi := payload[0]
		msgID := binary.LittleEndian.Uint16(payload[1:3])
		name := cstring(payload[3:])
		t := &Topic{MsgID: msgID, Name: name, MultiID: multi}
		t.Format = f.Formats[formatName(name)]
		f.ensureTopicSlot(msgID)
		f.Topics[msgID] = t
		if t.Format != nil {
			if _, seen := f.Data[name]; !seen {
				f.Data[name] = &Data{Name: name, MultiID: multi, Cols: map[string]Column{}}
			}
		}

	case 'D': // msg_id + 数据
		if len(payload) < 2 {
			return nil
		}
		id := binary.LittleEndian.Uint16(payload[:2])
		if int(id) >= len(f.Topics) {
			return nil
		}
		t := f.Topics[id]
		if t == nil || t.Format == nil {
			return nil
		}
		d := f.Data[t.Name]
		if d == nil || d.MultiID != t.MultiID {
			return nil // 非首个实例，跳过
		}
		return f.decodeRow(t.Format, payload[2:], d)

	case 'L': // 等级 + 时间戳 + 正文
		if len(payload) < 9 {
			return nil
		}
		lvl, ok := levelNames[payload[0]]
		if !ok {
			lvl = '?'
		}
		ts := float64(binary.LittleEndian.Uint64(payload[1:9]))
		f.Messages = append(f.Messages, LoggedMessage{
			Level:       lvl,
			TimestampUS: ts - f.StartTimeUS,
			Message:     strings.TrimRight(cstring(payload[9:]), "\t"),
		})

	case 'I': // key_len + key + value
		key, val := parseInfo(payload)
		if key != "" {
			f.Info[key] = val
		}

	case 'C':
		f.Dropouts++
	}
	return nil
}

func (f *File) ensureTopicSlot(id uint16) {
	for int(id) >= len(f.Topics) {
		f.Topics = append(f.Topics, nil)
	}
}

// ---------------------------------------------------------------- 格式解析

// parseFormat 解析 "消息名:类型 字段名;类型 字段名;..."。
// 注意第一个字段紧跟在消息名后面，容易被漏掉（漏掉会导致整条数据错位）。
func parseFormat(payload []byte) (string, []Field) {
	s := cstring(payload)
	colon := strings.IndexByte(s, ':')
	if colon < 0 {
		return "", nil
	}
	name := s[:colon]
	rest := s[colon+1:]

	var fields []Field
	for _, entry := range strings.Split(rest, ";") {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		sp := strings.LastIndexByte(entry, ' ')
		if sp < 0 {
			continue
		}
		ftype, fname := entry[:sp], strings.TrimSpace(entry[sp+1:])
		base, dims := splitDims(strings.TrimSpace(ftype))
		fields = append(fields, Field{Name: fname, Type: base, Dims: dims})
	}
	return name, fields
}

// splitDims 把 "float[4]" 拆成 ("float", [4])。
func splitDims(t string) (string, []int) {
	var dims []int
	for {
		open := strings.LastIndexByte(t, '[')
		if open < 0 || !strings.HasSuffix(t, "]") {
			break
		}
		n := 0
		if _, err := fmt.Sscanf(t[open+1:len(t)-1], "%d", &n); err != nil || n <= 0 {
			break
		}
		dims = append([]int{n}, dims...)
		t = t[:open]
	}
	return t, dims
}

// formatName 取订阅名对应的格式名：多实例订阅可能写成 "xxx.0"，剥掉后缀。
func formatName(topic string) string {
	if i := strings.IndexByte(topic, '.'); i > 0 {
		return topic[:i]
	}
	return topic
}

// ---------------------------------------------------------------- 数据解码

// decodeRow 按格式把一条数据消息解成一行，追加到各列。
func (f *File) decodeRow(fmtDef *Format, payload []byte, d *Data) error {
	rd := bytes.NewReader(payload)
	if err := f.decodeFields(fmtDef.Fields, "", rd, d); err != nil {
		return err
	}
	d.Rows++
	return nil
}

func (f *File) decodeFields(fields []Field, prefix string, rd *bytes.Reader, d *Data) error {
	for _, fl := range fields {
		// 填充字段（_padding0 之类）只存在于格式定义里，**数据消息中不写入**。
		// 这是 ULog 的一个关键细节：填充只服务于内存对齐，不落盘。
		// 如果按格式声明去读它们，后面的字段会整体错位（实测 vehicle_attitude
		// 声明 56 字节、实际 49 字节，差的 7 字节正是 padding）。
		if strings.HasPrefix(fl.Name, "_") {
			continue
		}
		elems := 1
		for _, dim := range fl.Dims {
			elems *= dim
		}

		// 嵌套结构：数据里是扁平拼接的，按子格式递归展开，字段名用 a.b 连接
		if _, isBuiltin := builtinSize[fl.Type]; !isBuiltin {
			sub, ok := f.Formats[fl.Type]
			if !ok {
				return fmt.Errorf("字段 %s 的嵌套类型 %q 未定义", prefix+fl.Name, fl.Type)
			}
			for i := 0; i < elems; i++ {
				name := prefix + fl.Name
				if len(fl.Dims) > 0 {
					name = fmt.Sprintf("%s[%d]", prefix+fl.Name, i)
				}
				if err := f.decodeFields(sub.Fields, name+".", rd, d); err != nil {
					return err
				}
			}
			continue
		}

		for i := 0; i < elems; i++ {
			name := prefix + fl.Name
			if len(fl.Dims) > 0 {
				name = fmt.Sprintf("%s[%d]", prefix+fl.Name, i)
			}
			v, err := readBuiltin(fl.Type, rd)
			if err != nil {
				return fmt.Errorf("读取字段 %s 失败: %w", name, err)
			}
			if fl.Type == "char" {
				continue // 字符串不参与数值判读，但必须消费字节
			}
			d.Cols[name] = append(d.Cols[name], v)
		}
	}
	return nil
}

func readBuiltin(typ string, rd *bytes.Reader) (float64, error) {
	switch typ {
	case "int8_t":
		b, err := rd.ReadByte()
		return float64(int8(b)), err
	case "uint8_t", "bool", "char":
		b, err := rd.ReadByte()
		return float64(b), err
	case "int16_t":
		var v int16
		return float64(v), binary.Read(rd, binary.LittleEndian, &v)
	case "uint16_t":
		var v uint16
		return float64(v), binary.Read(rd, binary.LittleEndian, &v)
	case "int32_t":
		var v int32
		return float64(v), binary.Read(rd, binary.LittleEndian, &v)
	case "uint32_t":
		var v uint32
		return float64(v), binary.Read(rd, binary.LittleEndian, &v)
	case "float":
		var v float32
		return float64(v), binary.Read(rd, binary.LittleEndian, &v)
	case "int64_t":
		var v int64
		return float64(v), binary.Read(rd, binary.LittleEndian, &v)
	case "uint64_t":
		var v uint64
		return float64(v), binary.Read(rd, binary.LittleEndian, &v)
	case "double":
		var v float64
		return v, binary.Read(rd, binary.LittleEndian, &v)
	}
	return 0, fmt.Errorf("不支持的类型 %q", typ)
}

// ---------------------------------------------------------------- 辅助

// cstring 读以 0 结尾的字符串。
func cstring(b []byte) string {
	if i := bytes.IndexByte(b, 0); i >= 0 {
		b = b[:i]
	}
	return string(b)
}

// parseInfo 解析信息消息：key_len + key + 值。
func parseInfo(payload []byte) (string, string) {
	if len(payload) < 2 {
		return "", ""
	}
	keyLen := int(payload[0])
	if 1+keyLen > len(payload) {
		return "", ""
	}
	key := string(payload[1 : 1+keyLen])
	val := payload[1+keyLen:]

	// 键名形如 "char[40] ver_sw" / "uint32_t ver_data_format"：取最后一个空格之后
	name := key
	typ := ""
	if sp := strings.LastIndexByte(key, ' '); sp > 0 {
		typ, name = strings.TrimSpace(key[:sp]), strings.TrimSpace(key[sp+1:])
	}
	base, dims := splitDims(typ)
	if len(dims) > 0 && base == "char" {
		return name, cstring(val) // 定长字符串
	}

	switch base {
	case "char":
		return name, cstring(val)
	case "uint8_t", "bool":
		if len(val) >= 1 {
			return name, fmt.Sprintf("%d", val[0])
		}
	case "int8_t":
		if len(val) >= 1 {
			return name, fmt.Sprintf("%d", int8(val[0]))
		}
	case "uint16_t":
		if len(val) >= 2 {
			return name, fmt.Sprintf("%d", binary.LittleEndian.Uint16(val))
		}
	case "int16_t":
		if len(val) >= 2 {
			return name, fmt.Sprintf("%d", int16(binary.LittleEndian.Uint16(val)))
		}
	case "uint32_t":
		if len(val) >= 4 {
			return name, fmt.Sprintf("%d", binary.LittleEndian.Uint32(val))
		}
	case "int32_t":
		if len(val) >= 4 {
			return name, fmt.Sprintf("%d", int32(binary.LittleEndian.Uint32(val)))
		}
	case "float":
		if len(val) >= 4 {
			return name, fmt.Sprintf("%g", math.Float32frombits(binary.LittleEndian.Uint32(val)))
		}
	case "uint64_t":
		if len(val) >= 8 {
			return name, fmt.Sprintf("%d", binary.LittleEndian.Uint64(val))
		}
	case "int64_t":
		if len(val) >= 8 {
			return name, fmt.Sprintf("%d", int64(binary.LittleEndian.Uint64(val)))
		}
	}
	return name, ""
}

// ---------------------------------------------------------------- 查询辅助

// Column 返回某列数据（不存在时返回 nil）。
func (d *Data) Column(name string) Column { return d.Cols[name] }

// TimestampUS 返回该主题的时间戳列（绝对 µs）。
func (d *Data) TimestampUS() Column {
	for _, k := range []string{"timestamp", "timestamp_sample"} {
		if c, ok := d.Cols[k]; ok {
			return c
		}
	}
	return nil
}

// TopicNames 返回解析出的主题名（排序，便于稳定输出）。
func (f *File) TopicNames() []string {
	names := make([]string, 0, len(f.Data))
	for n := range f.Data {
		names = append(names, n)
	}
	sort.Strings(names)
	return names
}

// Errors 返回 ERROR 级消息。
func (f *File) Errors() []LoggedMessage { return f.byLevel('E') }

// Warnings 返回 WARNING 级消息。
func (f *File) Warnings() []LoggedMessage { return f.byLevel('W') }

func (f *File) byLevel(level byte) []LoggedMessage {
	var out []LoggedMessage
	for _, m := range f.Messages {
		if m.Level == level {
			out = append(out, m)
		}
	}
	return out
}
