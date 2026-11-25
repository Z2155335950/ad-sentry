# 素材哨兵 AdSentry

面向出海广告投放的**上线前侵权风险信号筛查助手**。设计师 / 投放把图片或短视频丢进飞书群，系统返回带证据链的风险卡片（P0–P3），点击进入 Web 复核页人工确认或驳回。

> 产品红线：只输出风险信号与技术证据，不输出「侵权」判定。P0/P1 必须至少有一条参照物证据（reference / clause / license）。

## 当前进度

Phase 0：骨架、配置、DB、上传、fixture 假卡片。详见 [PROGRESS.md](PROGRESS.md)，决策记录见 [DECISIONS.md](DECISIONS.md)。

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows Git Bash
python scripts/make_fixture_image.py              # 生成测试图（一次性）
```

启动服务：

```bash
python -m uvicorn app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/> 上传 `fixtures/media/sample_image.png`，5 秒内应看到 P1 卡片。

## 验收命令

| 命令 | 说明 |
| --- | --- |
| `make verify-0` | 阶段验收入口（Phase 0，等价 `make p0`） |
| `make p0` | 启动临时服务，上传样例图，断言 5 秒内产出 fixture 卡片 |
| `make test` | pytest 全量单测 |
| `make lint` | ruff 检查 |
| `make run` | 开发服务器 |

Windows 上 make 通过 winget 安装（`ezwinports.make` 4.4.1）；PATH 未刷新的 shell 用
等价命令：`python scripts/p0_check.py`、`python -m pytest`（先激活 `.venv`）。

## 配置

所有键见 [.env.example](.env.example)。密钥只放 `.env`，不进仓库。凭证清单见 PROMPT.md §4 与实施记录。

## 已知限制

- 检测链路尚未接入：当前流水线写入 `fixtures/fake_findings.json` 的 fixture 结果，用于跑通「上传 → 任务 → 卡片 → 复核页」骨架。
- 飞书接入、真实工具适配器、评测、演示模式在后续 Phase 实现。
- Windows 上 make 通过 winget 安装（ezwinports.make 4.4.1）；未刷新 PATH 的 shell 用 `.venv` 内的 python 执行等价命令。

## 免责声明

本工具输出为基于公开信息的风险提示与技术证据，不构成法律意见；上线前请结合授权文件与法务意见自行判断。
