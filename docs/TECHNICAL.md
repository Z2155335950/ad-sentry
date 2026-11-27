# 技术细节

这份文档记录实现层面的设计，普通使用不需要读。项目的整体介绍见 [README](../README.md)，决策过程见 [DECISIONS](../DECISIONS.md)。

## 流水线总览

```
素材 → 抽取层（probe / asr / scene / frames / ocr）
     → 工具层（反向图搜 / 名人 / BGM / logo / 商标 / 政策）
     → Verifier（低置信二次复核）
     → 授权台账校准（过期 / 超范围 → 升级等级）
     → Finding + Evidence → 飞书卡片 / 复核页
```

主干是确定性 DAG，LLM 不参与工具选择；唯一由 LLM 参与的是可选的授权书字段抽取。

## 抽取层

流程：`probe`（时长 / 分辨率 / 音轨）→ `asr`（抽 16k 单声道 + faster-whisper）→
`scene`（PySceneDetect，失败退均匀分段）→ `frames`（每镜 ≤3 帧、全片 ≤12 帧、pHash 去重、
Laplacian 清晰度优先）→ `ocr`（逐帧）。

产物落盘为 `data/extracted/<sha256>/extraction.json`，含关键帧 JPEG，通过
`GET /api/jobs/{id}/extraction` 读取。

- **ffmpeg**：优先系统安装，其次 `imageio-ffmpeg` 自带二进制。
- **OCR**：`OCR_ENGINE=auto` 依次尝试 PaddleOCR、rapidocr-onnxruntime（同一套 PP-OCR 模型），
  都不可用时该信号降级为 `unavailable`，不阻塞任务。
- **ASR**：faster-whisper `small` / `int8` / CPU。首次运行会下载权重到 `ASR_MODEL_DIR`
  （默认 `./data/models`），模型端点默认 `https://hf-mirror.com`，可改 `HF_ENDPOINT`。
- **帧预算**：`MAX_FRAMES=12`；场景阈值 `SCENE_THRESHOLD=27`、`SCENE_MIN_LEN=15`。
- 抽取层不产生 Finding；`EXTRACT_ENABLED=false` 可跳过抽取（骨架验收与单测使用）。

## 工具层

`pipeline/detect.py` 是确定性 DAG：按分镜轮流选帧 → 图搜 → 名人 → BGM，单工具失败只丢该信号。

- **版权图像**：Google Vision `WEB_DETECTION`；图库域名 full/partial match → P0，未知来源
  匹配页 → P1，仅视觉相似 → P2；证据含页面 URL、标题、匹配类型、帧时间戳。
- **BGM**：头 / 中 / 尾各 10s 窗口送 ACRCloud；识别到曲目 → P2「请核对授权」；全窗口无匹配 →
  P2「未知音乐」；未配置凭证时不产出。
- **公众人物**：本地 Haar 检测人脸 → 裁剪（外扩 20%）→ AWS Rekognition；命中 ≥0.80 → P1，
  只落姓名 / 置信度 / bbox / 时间戳，不落人脸图像。
- **运行时**：每素材预算 `BUDGET_PER_ASSET_USD=0.25`；同工具 60s 内 3 次失败熔断 60s；
  live 结果写 `ToolCache`；每次调用写 `ToolCall`。
- **mock**：`TOOLS_MODE=mock`（默认）读 `fixtures/cache/*.json`，按素材 sha256 + 分镜 / 窗口
  键控；`TOOLS_MODE=auto` 在凭证齐全时走 live。演示素材见
  [fixtures/README.md](../fixtures/README.md)。

## 词表、logo 与政策 RAG

- **商标词**：`data/watchlists/brands.json`（56 条演示子集，含别名 / 注册号 / tier）。
  OCR 逐行与 ASR 转写按词边界匹配；短词（<6 字符）只精确匹配，`bike` 不会命中 `Nike`。
  Nike 命中附 USPTO 注册号 6124779；无注册信息的品牌最高 P2，通用词只给 P3。
- **logo**：Vision `LOGO_DETECTION` + open_clip 向量库 + OCR 文本三路投票。
  ≥2 路且库内有注册信息 → P0；≥2 路无注册 → P1；单路或词表外品牌 → P2。
  CLIP 为可选依赖（`pip install -e ".[clip]"`），未安装时该路自动跳过。
- **政策 RAG**：`data/watchlists/policy_clauses.yaml`（24 条 TikTok / Meta 条款），
  rank_bm25 检索 top-k，命中只输出 P3，并附 `clause_id`、原文摘录与生效日期；
  无达标条款不产出。阈值 `POLICY_MIN_SCORE` 可调。

## 授权台账与复核页

- **授权书抽取**：PDF（PyMuPDF 文本层）、DOCX（解包 `word/document.xml`）、图片（OCR）。
  扫描版 PDF 文本层为空时渲染成图片再 OCR。标签行解析 `授权方 / 授权类型 / 地域范围 /
  媒介范围 / 生效日期 / 到期日期` 等字段；图片 OCR 置信低于阈值时关键字段进
  `needs_review`（待人工录入）。
- **LLM 可选**：`LEDGER_LLM_ENABLED=true` 且装了 `pip install -e ".[llm]"` 时用
  `claude-opus-5-5` 的 `messages.parse` 补空字段；失败自动回退规则解析。
- **状态机**：`effective | expiring_soon(≤30 天) | expired | out_of_territory |
  out_of_media | out_of_scope | needs_review`。关键字段缺失或置信 < 0.7 → `needs_review`，
  不参与判定。
- **投放上下文**：`POST /api/assets/{id}/campaign` 登记 platform / market / 起止日期。
  没有 Campaign 时地域与媒介无法核验，只按日期给状态并在界面提示补充。
- **人工挂接**：`POST /api/licenses/{id}/link` 把授权记录挂到素材；挂接后按 Campaign
  重算状态，并给授权类型覆盖到的风险类别补一条 `license` 证据。授权过期 / 地域不符 /
  媒介不符 / 范围不符时把等级抬到 P1，**只升不降**。
- **复核页**：`/review/{finding_id}?token=` 含「授权台账」面板。

## 飞书接入

- **接入方式**：默认长连接（WebSocket），本地无需公网地址；`FEISHU_LONG_CONNECTION=false`
  时改走公网回调 `POST /api/feishu/event`。两种方式共用 `app/feishu/events.py` 的同一条
  处理路径。
- **链路**：`im.message.receive_v1` → 验 Token / 签名 → `message_id` 幂等 →
  回执 → 下载资源 → 建 Job → worker 完成后推送卡片。
- **资源下载**：图片消息用 `type=image`，文件与视频消息统一用 `type=file`
  （飞书接口不接受 `media`，传了会返回 400）。
- **卡片**：复用 `pipeline/report.build_card`，链接由 `PUBLIC_BASE_URL` + HMAC 签名 token
  生成，默认 24h 有效。
- **多维表格（可选）**：配置 `FEISHU_BITABLE_APP_TOKEN` / `FEISHU_BITABLE_TABLE_ID`
  且应用有 `bitable:app` 权限时，任务完成后追加一条记录。
- **离线验收**：`FEISHU_MODE=mock` 把外发内容写到 `data/feishu_outbox.jsonl`。

## Verifier 与评测

- **Verifier**：只对 `0.4 ≤ confidence < 0.7` 的版权图像 / logo 信号做一次二次检索——
  bbox 外扩 20%（无 bbox 退化为整帧）→ 裁剪 → 2× 放大 → 同工具重搜。置信提升才合并，
  并把这一步写进证据。单素材最多 `VERIFIER_MAX_CALLS` 次。
- **消融开关**：`ABLATION_VERIFIER`、`ABLATION_PLANNER`（关闭后不做帧预算裁剪）、
  `BASELINE_VLM_ONLY`（只由评测脚本读取）。
- **评测集**：`fixtures/eval.jsonl`，11 条（2 条植入 + 9 条干净，含对抗样本 `clean_bike`）。
- **匹配规则**：类别一致，且 ref 相同 / bbox IoU ≥ 0.3 / 时间重叠 ≥ 50% 之一。
- **报告**：`make eval` 输出四组对照（纯 VLM 基线 / 无 Verifier / 完整 / 关闭 Planner），
  含召回、精确、干净集误报、调用次数、成本、p50/p90，以及 Wilson 区间与局限说明。

## 演示模式与 TTL 清理

- **演示缓存**：`DEMO_MODE=true` 时工具优先读 `fixtures/cache/<tool>/<sha256>.json`，
  缓存缺失才回落 `TOOLS_MODE` 的 mock / live。`scripts/make_demo_cache.py` 从 flat mock 生成。
- **回放**：`GET /demo` 列出五个场景，`GET /demo/replay/{id}` 打开结果页。
  场景：高风险视频全链路、证据链与授权到期、干净占位素材、AI 球鞋素材、历史素材批量巡检。
- **TTL**：上传素材默认保留 24h（`ASSET_TTL_HOURS`），清理任务每小时跑一次
  （`CLEANUP_INTERVAL_SECONDS`），删除素材记录、关联 Finding/Evidence/Detection/Job、
  素材文件、`extracted/<sha256>/` 与未被引用的旧授权文件；`fixtures/` 演示素材不删。
- **部署**：`docker compose up --build`。app 服务设 `WORKER_IN_APP=false`，worker 服务
  运行 `python -m app.workers.run`，两者共用 `./data`。

## 延迟与彩排

`make verify-8` 会启动真实服务，预热 OCR/ASR 后采样：图片 5 次、15s 视频 3 次，
再跑评测与五场景回放。参考结果（mock 工具层）：

- 图片 p50 **1.04s** / p90 1.17s（目标 <20s）
- 15s 视频 p50 **9.71s** / p90 9.88s（目标 <60s）
- 干净集 P0/P1 误报 **0/9**；植入集 P0/P1 命中 4/4

## 验收命令

| 命令 | 说明 |
| --- | --- |
| `make verify-0` … `make verify-8` | 按阶段验收，等价于对应的 `make pN` / `make eval` |
| `make p0` | 上传样例图，断言 5 秒内产出卡片 |
| `make p1` | 上传 15s 视频，断言关键帧、OCR 文本、ASR 转写 |
| `make p2` | 断言图搜 P0 / 名人 P1 / BGM P2；干净素材零 P0/P1 |
| `make p3` | 商标词 / 政策 / logo 单测 + 端到端植入命中 |
| `make p4` | 三份授权书状态 + 挂接素材 + 复核动作落库 |
| `make p5` | 飞书事件 mock 回环：验签、幂等、回执、卡片、复核链接 |
| `make eval` | 跑评测集，生成 `eval_report.md` / `eval_report.json` |
| `make demo` | 离线回放五个演示场景 |
| `make p8` | 全量单测 + 延迟 / 误报 / 彩排 |
| `make cleanup` | 手动执行一次 TTL 清理 |
| `make test` / `make lint` / `make run` | 单测 / ruff / 开发服务器 |

Windows 上 make 通过 winget 安装（`ezwinports.make` 4.4.1）；PATH 未刷新时用
`.venv` 内的 python 执行等价命令。

## 已知限制

- 工具层默认 mock；ACRCloud 已用真实凭证验证（中国区），Google Vision 与 AWS Rekognition
  仍待验证，切 `TOOLS_MODE=auto` 前先用单素材对照。
- 品牌词表是 56 条演示子集（目标 ≥500）；仅 Nike 注册号经核对，其余无注册信息、最高 P2。
  政策条款为演示摘录，对外引用前须逐条核对官方原文与生效日期。
- logo 的 CLIP 向量库未随仓库分发（需品牌 logo 图片与 `.[clip]` 依赖）；VLM 候选品牌
  复核尚未接入。
- 商标词 P0 需要「high-tier 品牌 + 注册信息 + 同品类上下文」，而 Campaign 目前没有品类
  字段，所以商标词仍最高 P1；政策信号统一 P3。
- 干净素材 `clean_ai.png` 当前只有 1 条 P3（水印触发的 AI 标注义务提示），无 P0/P1。
- 名人 live 只送第一张人脸。BGM / 版权图像的升降级依赖人工挂接授权记录；未挂接时按原
  工具规则定级。
- 授权书字段抽取针对固定标签行模板；自由格式 / 手写 / 印章文件需要 `LEDGER_LLM_ENABLED`
  或人工补录。
- 卡片为 card 1.0 结构；多维表格未配置时静默跳过。
- 复核链接指向 `PUBLIC_BASE_URL`，演示前需改成可访问的地址。
- 评测集只有 11 条（冲刺约束），n<15，precision/recall 只作计数参考。
- Verifier 在 mock 下不会提升置信（同一分镜命中同一缓存），真实收益需 live 凭证。
- ASR 首次运行需要网络下载模型；离线环境需提前准备 `ASR_MODEL_DIR`。
- `Dockerfile` / `docker-compose.yml` 未在本机实际构建运行，只做了静态校验。
- `batch_scan` 是预置结果的汇总回放，不是对历史素材库重跑流水线。
- app 与 worker 共用 SQLite（WAL），仅支持单机；多 worker 需把任务认领改为条件更新。
