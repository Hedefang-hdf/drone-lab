VENV   ?= .venv
PYTHON ?= $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
ULOG   ?= data/20261027-sitl-hover.ulg
HOVER  ?= 25

.PHONY: report test test-hover test-safety test-all repro campaign campaign-replay sitl ulog2html test-go help

## report: 给一条飞行日志出人读的体检报告
report:
	$(PYTHON) pytools/ulog_report.py $(ULOG)

## test: 跑飞行测试用例（全部日志 × 全部用例）
test:
	$(PYTHON) -m pytest

## test-hover: 只跑悬停稳定性与动力类用例
test-hover:
	$(PYTHON) -m pytest -m hover

## test-safety: 只跑安全类用例（一票否决项）
test-safety:
	$(PYTHON) -m pytest -m safety

## test-all: 跑全部并输出到 reports/pytest.txt
test-all:
	@mkdir -p reports
	$(PYTHON) -m pytest 2>&1 | tee reports/pytest.txt

## repro: 负向验证——收紧阈值，确认用例真的能抓到问题
repro:
	$(PYTHON) scripts/negative_check.py

## sitl: 手动启动 PX4 SITL（调试用，正常请用 make campaign）
sitl:
	cd ~/PX4/px4/build/px4_sitl_default && PX4_SIM_MODEL=sihsim_quadx ./bin/px4

## campaign: 无人值守飞行测试流水线 —— 起 SITL → 飞 → 取日志 → 跑用例 → 出报告
campaign:
	$(PYTHON) scripts/run_campaign.py --live --hover $(HOVER)

## campaign-replay: 不飞，用已有日志跑下游全链路（CI 上跑的就是这个）
campaign-replay:
	$(PYTHON) scripts/run_campaign.py --replay $(ULOG)

## ulog2html: Go 版报告生成器 —— 纯 Go 解析 .ulg，出单文件 HTML（零依赖二进制）
ulog2html:
	cd gotools && go build -o bin/ulog2html ./cmd/ulog2html
	cd gotools && ./bin/ulog2html ../$(ULOG) -o ../reports/$(basename $(notdir $(ULOG))).html

## test-go: 跑 Go 单元测试（含与 Python 侧数值基准的交叉验证）
test-go:
	cd gotools && go test ./...

## help: 显示可用命令
help:
	@awk 'BEGIN {FS = ":.*##"} /^## /{printf "  \033[36mmake %-12s\033[0m %s\n", $$2, $$3}' $(MAKEFILE_LIST)
