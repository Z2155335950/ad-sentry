ifeq ($(wildcard .venv/Scripts/python.exe),)
PYTHON ?= python
else
PYTHON ?= .venv/Scripts/python.exe
endif

.PHONY: p0 p1 p2 p3 verify-0 verify-1 verify-2 verify-3 test lint run

# Phase 0 验收：上传样例图，5 秒内产出 fixture 卡片
p0:
	$(PYTHON) scripts/p0_check.py

# Phase 1 验收：15 秒视频输出关键帧、OCR 文本、ASR 转写 JSON
p1:
	$(PYTHON) scripts/p1_check.py

# Phase 2 验收：植入素材命中图搜 / 名人 / BGM，干净素材零 P0/P1
p2:
	$(PYTHON) scripts/p2_check.py

# Phase 3 验收：logo / 商标词 / 政策 RAG 单测 + 端到端植入命中
p3:
	$(PYTHON) -m pytest tests/test_trademark.py tests/test_policy.py tests/test_tools_logo.py -q
	$(PYTHON) scripts/p3_check.py

# 阶段验收入口：make verify-N（N 对应 Phase N）
verify-0: p0
verify-1: p1
verify-2: p2
verify-3: p3

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

run:
	$(PYTHON) -m uvicorn app.main:app --reload --port 8000
