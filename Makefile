PYTHON ?= python3
ULOG   ?= data/20261027-sitl-hover.ulg

.PHONY: report test sitl help

## report: 给一条飞行日志出体检报告
report:
	$(PYTHON) pytools/ulog_report.py $(ULOG)

## test: 运行飞行测试用例（M2 接入 pytest）
test:
	@echo "[M2] pytest 断言尚未接入，规划见 flytests/README.md"

## sitl: SITL 自动飞行流水线（M4）
sitl:
	@echo "[M4] MAVSDK-Python 自动飞行尚未实现，规划见 README 里程碑"

## help: 显示可用命令
help:
	@awk 'BEGIN {FS = ":.*##"} /^## /{printf "  \033[36mmake %-10s\033[0m %s\n", $$2, $$3}' $(MAKEFILE_LIST)
