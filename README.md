# 素材哨兵 AdSentry

面向出海广告投放的**上线前侵权风险信号筛查助手**。设计师 / 投放把图片或短视频丢进飞书群，系统返回带证据链的风险卡片（P0–P3），点击进入 Web 复核页人工确认或驳回。

> 产品红线：只输出风险信号与技术证据，不输出「侵权」判定。P0/P1 必须至少有一条参照物证据（reference / clause / license）。

## 当前进度

- **Phase 0 ✅** 骨架、配置、DB、上传、fixture 假卡片、复核页最小版。
- **Phase 1 ✅** 素材层 + 抽取层：ffmpeg 元数据与音轨、分镜、关键帧、OCR、ASR、pHash；
  产物落盘为 `data/extracted/<sha256>/extraction.json`（含关键帧 JPEG），
  通过 `GET /api/jobs/{id}/extraction` 读取。

进展与未完成项见 [PROGRESS.md](PROGRESS.md)，决策记录见 [DECISIONS.md](DECISIONS.md)。

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows Git Bash
python scripts/make_fixture_image.py              # 生成测试图（一次性）
python scripts/make_fixture_video.py              # 生成 15s 测试视频（一次性，含口播）
```

启动服务：

```bash
python -m uvicorn app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/> 上传 `fixtures/media/sample_image.png` 或 `sample_15s.mp4`。

## 验收命令

| 命令 | 说明 |
| --- | --- |
| `make verify-0` | 阶段验收入口（Phase 0，等价 `make p0`） |
| `make verify-1` | 阶段验收入口（Phase 1，等价 `make p1`） |
| `make p0` | 启动临时服务，上传样例图，断言 5 秒内产出 fixture 卡片 |
| `make p1` | 上传 15s 视频，断言关键帧、OCR 文本、ASR 转写 JSON |
| `make test` | pytest 全量单测 |
| `make lint` | ruff 检查 |
| `make run` | 开发服务器 |

Windows 上 make 通过 winget 安装（`ezwinports.make` 4.4.1）；PATH 未刷新的 shell 用
等价命令：`python scripts/p0_check.py`、`python scripts/p1_check.py`、`python -m pytest`
（先激活 `.venv`）。

## 抽取层（Phase 1）

流程：`probe`（时长/分辨率/音轨）→ `asr`（抽 16k 单声道 + faster-whisper）→
`scene`（PySceneDetect，失败退均匀分段）→ `frames`（每镜 ≤3 帧、全片 ≤12 帧、pHash 去重、
Laplacian 清晰度优先）→ `ocr`（逐帧）。

- **ffmpeg**：优先系统安装，其次 `imageio-ffmpeg` 自带二进制，无需手动装。
- **OCR**：`OCR_ENGINE=auto` 依次尝试 PaddleOCR、rapidocr-onnxruntime（同一套 PP-OCR 模型），
  都不可用时该信号降级为 `unavailable`，不阻塞任务。
- **ASR**：faster-whisper `small` / `int8` / CPU。首次运行会下载权重到 `ASR_MODEL_DIR`
  （默认 `./data/models`）。模型端点默认 `https://hf-mirror.com`，可改 `HF_ENDPOINT`。
- **帧预算**：`MAX_FRAMES=12`；场景阈值 `SCENE_THRESHOLD=27`、`SCENE_MIN_LEN=15`。
- 抽取层不产生 Finding；风险信号仍由后续工具层（Phase 2+）产出。`EXTRACT_ENABLED=false`
  可跳过抽取（Phase 0 验收与单测使用）。

## 配置

所有键见 [.env.example](.env.example)。密钥只放 `.env`，不进仓库。凭证清单见 PROMPT.md §4 与实施记录。

## 已知限制

- 检测链路尚未接入：当前风险信号来自 `fixtures/fake_findings.json`，用于跑通
  「上传 → 任务 → 卡片 → 复核页」骨架；抽取结果已可供 Phase 2 工具层消费。
- ASR 首次运行需要网络下载模型；离线环境需提前准备 `ASR_MODEL_DIR`。
- 飞书接入、真实工具适配器、评测、演示模式在后续 Phase 实现。
- Windows 上 make 通过 winget 安装（ezwinports.make 4.4.1）；未刷新 PATH 的 shell 用
  `.venv` 内的 python 执行等价命令。

## 免责声明

本工具输出为基于公开信息的风险提示与技术证据，不构成法律意见；上线前请结合授权文件与法务意见自行判断。
