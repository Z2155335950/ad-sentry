# 素材哨兵 AdSentry

面向出海广告投放的**上线前侵权风险信号筛查助手**。设计师 / 投放把图片或短视频丢进飞书群，系统返回带证据链的风险卡片（P0–P3），点击进入 Web 复核页人工确认或驳回。

> 产品红线：只输出风险信号与技术证据，不输出「侵权」判定。P0/P1 必须至少有一条参照物证据（reference / clause / license）。

## 当前进度

- **Phase 0 ✅** 骨架、配置、DB、上传、fixture 假卡片、复核页最小版。
- **Phase 1 ✅** 素材层 + 抽取层：ffmpeg 元数据与音轨、分镜、关键帧、OCR、ASR、pHash；
  产物落盘为 `data/extracted/<sha256>/extraction.json`（含关键帧 JPEG），
  通过 `GET /api/jobs/{id}/extraction` 读取。
- **Phase 2 ✅** 工具层：反向图搜、ACRCloud BGM、AWS Rekognition 公众人物；统一预算 / 熔断 /
  缓存 / `ToolCall` 记账；默认 mock，凭证齐全后 `TOOLS_MODE=auto` 接真。
- **Phase 3 ✅** logo 三路投票（Vision / CLIP / OCR）、离线商标词表、平台政策 BM25 RAG；
  `data/watchlists/` 提供品牌词表、竞品词表与 24 条 TikTok/Meta 政策条款。

进展与未完成项见 [PROGRESS.md](PROGRESS.md)，决策记录见 [DECISIONS.md](DECISIONS.md)。

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows Git Bash
python scripts/make_fixture_image.py              # 生成测试图（一次性）
python scripts/make_fixture_video.py              # 生成 15s 测试视频（一次性，含口播）
python scripts/make_p2_fixtures.py                # 生成 Phase 2 风险演示视频 + mock（一次性）
```

启动服务：

```bash
python -m uvicorn app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/> 上传 `fixtures/media/risk_ad_10s.mp4`（Phase 2 演示，默认 mock，
可看到 P0/P1/P2 三类信号）；骨架验收用 `make p0`（该检查会关闭工具层）。

## 验收命令

| 命令 | 说明 |
| --- | --- |
| `make verify-0` | 阶段验收入口（Phase 0，等价 `make p0`） |
| `make verify-1` | 阶段验收入口（Phase 1，等价 `make p1`） |
| `make verify-2` | 阶段验收入口（Phase 2，等价 `make p2`） |
| `make verify-3` | 阶段验收入口（Phase 3：三个模块单测 + `p3_check.py`） |
| `make p0` | 启动临时服务，上传样例图，断言 5 秒内产出 fixture 卡片 |
| `make p1` | 上传 15s 视频，断言关键帧、OCR 文本、ASR 转写 JSON |
| `make p2` | 上传植入素材，断言图搜 P0 / 名人 P1 / BGM P2；干净素材零 P0/P1 |
| `make p3` | 商标词 / 政策 / logo 单测 + 上传 15s 视频，断言 logo P0、商标词 P1、干净素材零误报 |
| `make test` | pytest 全量单测 |
| `make lint` | ruff 检查 |
| `make run` | 开发服务器 |

Windows 上 make 通过 winget 安装（`ezwinports.make` 4.4.1）；PATH 未刷新的 shell 用
等价命令：`python scripts/p0_check.py`、`python scripts/p1_check.py`、`python scripts/p2_check.py`、
`python -m pytest`（先激活 `.venv`）。

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

## 工具层（Phase 2）

`pipeline/detect.py` 是确定性 DAG：按分镜轮流选帧 → 图搜 → 名人 → BGM，单工具失败只丢该信号。

- **版权图像**：Google Vision `WEB_DETECTION`；图库域名 full/partial match → P0，未知来源
  匹配页 → P1，仅视觉相似 → P2；证据含页面 URL、标题、匹配类型、帧时间戳。
- **BGM**：头/中/尾各 10s 窗口送 ACRCloud；识别到曲目 → P2「请核对授权」；全窗口无匹配 →
  P2「未知音乐」；未配置凭证时不产出。
- **公众人物**：本地 Haar 检测人脸 → 裁剪（外扩 20%）→ AWS Rekognition；命中 ≥0.80 → P1，
  只落姓名 / 置信度 / bbox / 时间戳，不落人脸图像。
- **运行时**：每素材预算 `BUDGET_PER_ASSET_USD=0.25`；同工具 60s 内 3 次失败熔断 60s；
  live 结果写 `ToolCache`；每次调用写 `ToolCall`。
- **mock**：`TOOLS_MODE=mock`（默认）读 `fixtures/cache/*.json`，按素材 sha256 + 分镜/窗口
  键控；`TOOLS_MODE=auto` 在凭证齐全时走 live。演示素材见
  [fixtures/README.md](fixtures/README.md)。

## 词表、logo 与政策 RAG（Phase 3）

- **商标词**：`data/watchlists/brands.json`（56 条演示子集，含别名 / 注册号 / tier）。
  OCR 逐行与 ASR 转写按词边界匹配；短词（<6 字符）只精确匹配，`bike` 不会命中 `Nike`。
  Nike 命中附 USPTO 注册号 6124779；无注册信息的品牌最高 P2，通用词只给 P3。
- **logo**：Vision `LOGO_DETECTION` + open_clip 向量库 + OCR 文本三路投票。
  ≥2 路且库内有注册信息 → P0；≥2 路无注册 → P1；单路或词表外品牌 → P2。
  CLIP 为可选依赖（`pip install -e ".[clip]"`），未安装时该路自动跳过。
- **政策 RAG**：`data/watchlists/policy_clauses.yaml`（24 条 TikTok / Meta 条款），
  rank_bm25 检索 top-k，命中只输出 P3，并附 `clause_id`、原文摘录与生效日期；
  无达标条款不产出。阈值 `POLICY_MIN_SCORE` 可调。
- **演示 mock**：`scripts/make_p3_fixtures.py` 为 `sample_15s.mp4` 写入第 2 镜的
  Vision / CLIP 命中（NIKE），与真实 OCR 命中构成三路证据。

## 配置

所有键见 [.env.example](.env.example)。密钥只放 `.env`，不进仓库。凭证清单见 PROMPT.md §4 与实施记录。

## 已知限制

- 工具层默认 mock；三个 live 适配器尚未用真实凭证验证，切 `TOOLS_MODE=auto` 前先用单素材对照。
- 品牌词表是 56 条演示子集（PROMPT 目标 ≥500）；仅 Nike 注册号经核对，其余无注册信息、
  最高 P2。政策条款为演示摘录，对外引用前须逐条核对官方原文与生效日期。
- logo 的 CLIP 向量库未随仓库分发（需品牌 logo 图片与 `.[clip]` 依赖）；VLM 复核在 Phase 6。
- 商标词 P0 需要 Campaign 上下文（Phase 4 接入），当前最高 P1；政策信号统一 P3。
- 干净素材 `clean_ai.png` 当前只有 1 条 P3（水印「豆包AI生成」触发的 AI 标注义务提示），
  无 P0/P1；`TOOLS_ENABLED=false` 时才回退 `fixtures/fake_findings.json`（骨架验收与单测使用）。
- 名人 live 只送第一张人脸；无授权台账，BGM 命中统一 P2（Phase 4 接入 license 后才升降级）。
- ASR 首次运行需要网络下载模型；离线环境需提前准备 `ASR_MODEL_DIR`。
- 飞书接入、logo/商标/政策、评测、演示模式在后续 Phase 实现。
- Windows 上 make 通过 winget 安装（ezwinports.make 4.4.1）；未刷新 PATH 的 shell 用
  `.venv` 内的 python 执行等价命令。

## 免责声明

本工具输出为基于公开信息的风险提示与技术证据，不构成法律意见；上线前请结合授权文件与法务意见自行判断。
