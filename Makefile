ifeq ($(wildcard .venv/Scripts/python.exe),)
PYTHON ?= python
else
PYTHON ?= .venv/Scripts/python.exe
endif

.PHONY: p0 p1 verify-0 verify-1 test lint run

# Phase 0 验收：上传样例图，5 秒内产出 fixture 卡片
p0:
	$(PYTHON) scripts/p0_check.py

# Phase 1 验收：15 秒视频输出关键帧、OCR 文本、ASR 转写 JSON
p1:
	$(PYTHON) scripts/p1_check.py

# 阶段验收入口：make verify-N（N 对应 Phase N）
verify-0: p0
verify-1: p1

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

run:
	$(PYTHON) -m uvicorn app.main:app --reload --port 8000
