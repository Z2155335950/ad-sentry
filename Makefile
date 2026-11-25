ifeq ($(wildcard .venv/Scripts/python.exe),)
PYTHON ?= python
else
PYTHON ?= .venv/Scripts/python.exe
endif

.PHONY: p0 p1 p2 p3 p4 p5 p7 p8 eval verify-0 verify-1 verify-2 verify-3 verify-4 verify-5 verify-6 verify-7 verify-8 demo cleanup test lint run

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

# Phase 4 验收：授权书状态（有效/过期/地域不符）+ 复核动作落库
p4:
	$(PYTHON) -m pytest tests/test_ledger_status.py tests/test_ledger_extract.py \
		tests/test_ledger_gate.py tests/test_ledger_api.py -q
	$(PYTHON) scripts/p4_check.py

verify-4: p4

# Phase 5 验收：飞书事件 → 建任务 → 回执 + 卡片（mock 回环）
p5:
	$(PYTHON) -m pytest tests/test_feishu_events.py tests/test_feishu_client.py \
		tests/test_feishu_api.py -q
	$(PYTHON) scripts/p5_check.py

verify-5: p5

# Phase 6 验收：Verifier / 指标单测 + 评测报告与消融表
eval:
	$(PYTHON) -m pytest tests/test_agent_verifier.py tests/test_eval_metrics.py -q
	$(PYTHON) -m app.eval.run_eval

verify-6: eval

# Phase 7 验收：TTL 清理（记录 + 文件）+ 部署产物 + 五场景离线回放
p7:
	$(PYTHON) -m pytest tests/test_cleanup.py tests/test_demo_api.py -q
	$(PYTHON) scripts/p7_check.py

verify-7: p7 demo

# 演示入口：离线回放五个场景并打印现场 URL
demo:
	$(PYTHON) scripts/demo_check.py

# Phase 8 验收：全量单测 + 延迟达标 + 干净集误报 + 五场景彩排
p8:
	$(PYTHON) -m pytest -q
	$(PYTHON) scripts/p8_check.py

verify-8: p8

# 手动清理一次过期素材
cleanup:
	$(PYTHON) -m app.workers.cleanup --once

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

run:
	$(PYTHON) -m uvicorn app.main:app --reload --port 8000
