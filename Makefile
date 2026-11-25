ifeq ($(wildcard .venv/Scripts/python.exe),)
PYTHON ?= python
else
PYTHON ?= .venv/Scripts/python.exe
endif

.PHONY: p0 verify-0 test lint run

# Phase 0 验收：上传样例图，5 秒内产出 fixture 卡片
p0:
	$(PYTHON) scripts/p0_check.py

# 阶段验收入口：make verify-N（N=0 对应 Phase 0，与 make p0 等价）
verify-0: p0

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

run:
	$(PYTHON) -m uvicorn app.main:app --reload --port 8000
