VENV   ?= .venv
PYTHON ?= $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
ULOG   ?= data/20261027-sitl-hover.ulg

.PHONY: report test test-hover test-safety test-all repro sitl help

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

## sitl: SITL 自动飞行流水线（M4）
sitl:
	@echo "[M4] MAVSDK-Python 自动飞行尚未实现，规划见 README 里程碑"

## help: 显示可用命令
help:
	@awk 'BEGIN {FS = ":.*##"} /^## /{printf "  \033[36mmake %-12s\033[0m %s\n", $$2, $$3}' $(MAKEFILE_LIST)
