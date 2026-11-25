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
- **Phase 4 ✅** 授权台账：授权书字段抽取、七态状态机、Campaign 投放上下文、人工挂接素材、
  复核页台账面板；授权过期/地域不符等会把相关风险抬到 P1（只升不降）。
- **Phase 5 ✅** 飞书接入：长连接（WebSocket）收图片/视频/文件 → 回执 → 建任务 →
  风险卡片；`message_id` 幂等、签名与解密、可选多维表格留档；`FEISHU_MODE=mock` 可离线验收。
- **Phase 6 ✅** Verifier 二次检索（低置信 → 裁剪放大重搜）、消融开关、评测集与
  `make eval` 报告：11 条用例（干净 9 条），完整链路 P0/P1 召回 4/4、干净集误报 0/9。
- **Phase 7 ✅** 演示模式：五场景离线回放、按素材键控的 demo 缓存、TTL 清理
  （素材 / 抽取产物 / 孤儿授权文件）、Docker Compose（app + worker）。
- **Phase 8 ✅（离线）** 延迟验收：图片 p50 1.07s、15s 视频 p50 10.29s；干净集误报 0/9；
  用户占位素材接入演示；实时凭证与 Docker 验证待有条件的环境。

进展与未完成项见 [PROGRESS.md](PROGRESS.md)，决策记录见 [DECISIONS.md](DECISIONS.md)。

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows Git Bash
.venv/Scripts/python -m pip install -e ".[feishu]"  # 可选：飞书长连接（lark-oapi）
python scripts/make_fixture_image.py              # 生成测试图（一次性）
python scripts/make_fixture_video.py              # 生成 15s 测试视频（一次性，含口播）
python scripts/make_p2_fixtures.py                # 生成 Phase 2 风险演示视频 + mock（一次性）
python scripts/make_p4_fixtures.py                # 生成三份合成授权书 PDF（一次性）
python scripts/make_eval_set.py                   # 生成评测集与干净样本（一次性）
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
| `make verify-4` | 阶段验收入口（Phase 4：台账单测 + `p4_check.py`） |
| `make verify-5` | 阶段验收入口（Phase 5：飞书单测 + `p5_check.py`） |
| `make verify-6` | 阶段验收入口（Phase 6：等价 `make eval`） |
| `make verify-7` | 阶段验收入口（Phase 7：清理/演示单测 + 五场景离线回放） |
| `make verify-8` | 阶段验收入口（Phase 8：全量单测 + 延迟/误报/彩排） |
| `make p0` | 启动临时服务，上传样例图，断言 5 秒内产出 fixture 卡片 |
| `make p1` | 上传 15s 视频，断言关键帧、OCR 文本、ASR 转写 JSON |
| `make p2` | 上传植入素材，断言图搜 P0 / 名人 P1 / BGM P2；干净素材零 P0/P1 |
| `make p3` | 商标词 / 政策 / logo 单测 + 上传 15s 视频，断言 logo P0、商标词 P1、干净素材零误报 |
| `make p4` | 三份授权书状态（有效 / 过期 / 地域不符）+ 挂接素材 + 复核动作落库 |
| `make p5` | 飞书事件 mock 回环：验签、幂等、回执、卡片、复核链接可打开 |
| `make eval` | 跑评测集，生成 `eval_report.md` / `eval_report.json` 与消融表 |
| `make demo` | 离线回放五个演示场景，打印现场 URL 与预期结果 |
| `make p8` | 跑全量单测，再测图片/视频 p50、干净集误报与五场景彩排 |
| `make cleanup` | 手动执行一次 TTL 清理 |
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

## 授权台账与复核页（Phase 4）

- **授权书抽取**：PDF（PyMuPDF 文本层）、DOCX（解包 `word/document.xml`）、图片（OCR）。
  扫描版 PDF 文本层为空时渲染成图片再 OCR。标签行解析 `授权方 / 授权类型 / 地域范围 /
  媒介范围 / 生效日期 / 到期日期` 等字段；图片 OCR 的置信低于阈值，关键字段自动进
  `needs_review`（待人工录入）。
- **LLM 可选**：`LEDGER_LLM_ENABLED=true` 且装了 `pip install -e ".[llm]"` 时用
  `claude-opus-5-5` 的 `messages.parse` 补空字段；失败自动回退规则解析，不影响主路径。
- **状态机**：`effective | expiring_soon(≤30 天) | expired | out_of_territory |
  out_of_media | out_of_scope | needs_review`。关键字段（授权类型 / 地域 / 起止日期）
  缺失或置信 < 0.7 → `needs_review`，不参与判定。
- **投放上下文**：`POST /api/assets/{id}/campaign` 登记 platform / market / 起止日期。
  **没有 Campaign 时地域与媒介无法核验**，只按日期给状态并在界面提示补充投放信息。
  地域支持别名归一（`SEA` / 东南亚 / 新加坡 等）。
- **人工挂接**：`POST /api/licenses/{id}/link` 把授权记录挂到素材；挂接后按 Campaign
  重算状态，并给授权类型覆盖到的风险类别补一条 `license` 证据。授权过期 / 地域不符 /
  媒介不符 / 范围不符时把等级抬到 P1，**只升不降**——授权有效不会抹掉其他工具的发现。
- **复核页**：`/review/{finding_id}?token=` 增加「授权台账」面板，可查看已挂接记录的
  状态与原因、挂接未关联记录、登记或修改投放信息。
- **验收素材**：`fixtures/licenses/` 三份合成授权书（已过期 / 地域不含东南亚 / 有效），
  由 `scripts/make_p4_fixtures.py` 生成。

## 飞书接入（Phase 5）

- **接入方式**：默认长连接（WebSocket），本地无需公网地址；`FEISHU_LONG_CONNECTION=false`
  时改走公网回调 `POST /api/feishu/event`（需 ngrok 之类的隧道）。两种方式共用
  `app/feishu/events.py` 的同一条处理路径。
- **链路**：`im.message.receive_v1` → 验 Token / 签名 → `message_id` 幂等 →
  回执「已接收，预计 45 秒内返回风险卡片」→ 下载资源 → 建 Job → worker 完成后推送卡片。
- **消息类型**：图片（`image_key`）、文件与视频（`file_key`）；其他类型回一句话提示后忽略。
- **卡片**：复用 `pipeline/report.build_card`，含等级色、信号摘要、「查看证据链」按钮；
  链接由 `PUBLIC_BASE_URL` + HMAC 签名 token 生成，默认 24h 有效。
- **多维表格（可选）**：配置 `FEISHU_BITABLE_APP_TOKEN` / `FEISHU_BITABLE_TABLE_ID`
  且应用有 `bitable:app` 权限时，任务完成后追加一条记录。
- **离线验收**：`FEISHU_MODE=mock` 把外发内容写到 `data/feishu_outbox.jsonl`，
  `make p5` 据此断言回执、卡片与复核链接。
- **飞书侧配置**（缺一项都收不到消息）：权限 `im:resource` / `im:message` /
  `im:message:send_as_bot`；事件订阅 `im.message.receive_v1`；开启机器人能力；
  创建版本并发布；把机器人拉进测试群。
- **依赖**：长连接需要 `pip install -e ".[feishu]"`（`lark-oapi`）；公网回调不需要。

## Verifier 与评测（Phase 6）

- **Verifier**（唯一 Agent 环节）：只对 `0.4 ≤ confidence < 0.7` 的版权图像 / logo 信号做
  一次二次检索——bbox 外扩 20%（无 bbox 退化为整帧）→ 裁剪 → 2× 放大 → 同工具重搜。
  置信提升才合并，并把这一步写进证据。单素材最多 `VERIFIER_MAX_CALLS` 次。
- **消融开关**：`ABLATION_VERIFIER`、`ABLATION_PLANNER`（关闭后不做帧预算裁剪）、
  `BASELINE_VLM_ONLY`（只由评测脚本读取）。主干仍是确定性 DAG，LLM 不参与工具选择。
- **评测集**：`fixtures/eval.jsonl`，11 条（2 条植入 + 9 条干净，含对抗样本 `clean_bike`）。
  冲刺模式限制 ≤12 条；逐条产生方式见 `scripts/make_eval_set.py`。
- **匹配规则**：类别一致，且 ref 相同 / bbox IoU ≥ 0.3 / 时间重叠 ≥ 50% 之一。
- **报告**：`make eval` 输出四组对照（纯 VLM 基线 / 无 Verifier / 完整 / 关闭 Planner），
  含召回、精确、干净集误报、调用次数、成本、p50/p90，以及 Wilson 区间与局限说明。
- 评测集与抽取缓存都可离线复现；报告文件本身已加入 `.gitignore`。

## 演示模式与 TTL 清理（Phase 7）

- **演示缓存**：`DEMO_MODE=true` 时工具优先读 `fixtures/cache/<tool>/<sha256>.json`
  （按素材 + 分镜/窗口键控），缓存缺失才回落 `TOOLS_MODE` 的 mock / live。
  `scripts/make_demo_cache.py` 从现有 flat mock 生成该目录。
- **回放**：`GET /demo` 列出五个场景，`GET /demo/replay/{id}` 打开结果页，
  加 `?format=json` 取结构化结果。场景：高风险视频全链路、证据链与授权到期、
  干净占位素材（用户提供，复检通过）、AI 球鞋素材（真实 P3 + 单路 mock logo P2）、
  历史素材批量巡检。
  回放数据幂等写入数据库，卡片与复核页直接可用；`DEMO_MODE=false` 时接口返回 404。
- **现场**：`DEMO_MODE=true make run`（或写进 `.env`）；`make demo` 会离线验证五个场景
  并打印 `PUBLIC_BASE_URL` 下的现场 URL。
- **TTL**：上传素材默认保留 24h（`ASSET_TTL_HOURS`），清理任务每小时跑一次
  （`CLEANUP_INTERVAL_SECONDS`），删除素材记录、关联 Finding/Evidence/Detection/Job、
  素材文件、`extracted/<sha256>/` 与未被引用的旧授权文件；`fixtures/` 演示素材不删。
- **部署**：`docker compose up --build`。app 服务设 `WORKER_IN_APP=false`，worker 服务
  运行 `python -m app.workers.run`，两者共用 `./data`。

## 延迟与彩排（Phase 8）

`make verify-8` 会启动真实服务，预热 OCR/ASR 后采样：图片 5 次、15s 视频 3 次，
再跑评测与五场景回放。当前结果：

- 图片 p50 **1.04s** / p90 1.17s（目标 <20s）；
- 15s 视频 p50 **9.71s** / p90 9.88s（目标 <60s）；
- 干净集 P0/P1 误报 **0/9**；植入集 P0/P1 命中 4/4。

彩排顺序建议：先 `make demo` 离线过一遍，再 `DEMO_MODE=true make run`，按
`/demo` 列出的顺序打开五个场景。用户素材中 AURA 海报仅作占位；Nike 球鞋图的
logo 命中是单路 mock（演示数据，最高 P2）。

## 配置

所有键见 [.env.example](.env.example)。密钥只放 `.env`，不进仓库。凭证清单见 PROMPT.md §4 与实施记录。

## 已知限制

- 工具层默认 mock；ACRCloud 已用真实凭证验证（中国区），Google Vision 与 AWS Rekognition
  仍待验证，切 `TOOLS_MODE=auto` 前先用单素材对照。
- 品牌词表是 56 条演示子集（PROMPT 目标 ≥500）；仅 Nike 注册号经核对，其余无注册信息、
  最高 P2。政策条款为演示摘录，对外引用前须逐条核对官方原文与生效日期。
- logo 的 CLIP 向量库未随仓库分发（需品牌 logo 图片与 `.[clip]` 依赖）；
  VLM 候选品牌复核尚未接入。
- 商标词 P0 需要「high-tier 品牌 + 注册信息 + 同品类上下文」，而 Campaign 目前只有
  platform / market / 起止日期、没有品类字段，所以商标词仍最高 P1；政策信号统一 P3。
- 干净素材 `clean_ai.png` 当前只有 1 条 P3（水印「豆包AI生成」触发的 AI 标注义务提示），
  无 P0/P1；`TOOLS_ENABLED=false` 时才回退 `fixtures/fake_findings.json`（骨架验收与单测使用）。
- 名人 live 只送第一张人脸。BGM / 版权图像的升降级依赖人工把授权记录挂到素材
  （Phase 4 已支持）；未挂接时仍按原工具规则定级。
- 授权书的字段抽取目前针对固定标签行模板；自由格式 / 手写 / 印章文件需要
  `LEDGER_LLM_ENABLED` 或人工补录。
- 飞书长连接尚未用真实凭证验证（`FEISHU_APP_ID` / `FEISHU_APP_SECRET` 未配置），
  `make p5` 走 mock 回环；多维表格未配置；卡片为 card 1.0 结构。
- 复核链接指向 `PUBLIC_BASE_URL`，演示前需改成评委可访问的地址。
- 评测集只有 11 条（冲刺约束），整体 n<15，precision/recall 只作计数参考，附 Wilson 区间。
- Verifier 在 mock 下不会提升置信（同一分镜命中同一缓存），真实收益需 live 凭证；
  纯 VLM 基线是缓存结果，不是实时调用。
- ASR 首次运行需要网络下载模型；离线环境需提前准备 `ASR_MODEL_DIR`。
- 本机无 Docker，`Dockerfile` / `docker-compose.yml` 未实际构建运行；只做了静态校验。
- 录屏未产出：本环境不能录屏；`make demo` 与 `/demo` 提供可复现的彩排路径。
- 用户提供的 AURA 海报仅作演示占位，不对外声称品牌归属；`干扰1.png` 的 Nike logo
  命中是单路 mock（演示数据，最高 P2），真实 Vision 结果需凭证到位后复核。
- 实时凭证：Feishu 与 ACRCloud 已配置；Anthropic、Google Vision、AWS Rekognition
  仍缺失，无法完成真实 API 对照。
- `batch_scan` 是预置结果的汇总回放，不是对历史素材库重跑流水线；演示素材无 TTL。
- app 与 worker 共用 SQLite（WAL），仅支持单机；多 worker 需把任务认领改为条件更新。
- Windows 上 make 通过 winget 安装（ezwinports.make 4.4.1）；未刷新 PATH 的 shell 用
  `.venv` 内的 python 执行等价命令。

## 免责声明

本工具输出为基于公开信息的风险提示与技术证据，不构成法律意见；上线前请结合授权文件与法务意见自行判断。
