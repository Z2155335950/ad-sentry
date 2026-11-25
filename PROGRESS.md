# PROGRESS

> 每个 Phase 结束时更新：完成项 / 未完成 / 阻塞 / 下一步。冲突时以 PROMPT.md 为准。

## 演示素材

- `fixtures/media/` 清单与来源见 [fixtures/README.md](fixtures/README.md)。
- 用户提供 `clean_ai.png`（豆包 AI 生成人像）：已入库，用作 Phase 2 干净 AI 负例 /
  Phase 6 评测样本；OCR 可读出「豆包AI生成」水印。
- 用户提供 `hero.mp4`（13.1s 真人自拍，普通人像）：按决定**仅本地使用**，
  已加入 `.gitignore`，仅作本地负例（涉及真实个人面部，不入提交材料）。
- `fixtures/media/risk_ad_10s.mp4`（合成，Phase 2）：10s 三分镜，植入图库照片 / 合成人像 /
  合成旋律；配套 mock 在 `fixtures/cache/`，见 [fixtures/README.md](fixtures/README.md)。

## Phase 8（2026-11-25）✅（离线部分）

**验收**：`make verify-8`（全量 pytest + `scripts/p8_check.py`）

- 结果：通过。图片 p50 **1.04s** / p90 1.17s（5 次，目标 <20s）；
  15s 视频 p50 **9.71s** / p90 9.88s（3 次，目标 <60s）；
  干净集 P0/P1 误报 **0/9**，植入集 P0/P1 命中 4/4；五场景离线回放通过。
- `pytest -q`：**140 passed**；ruff 全绿。
- 用户素材接入：`v1.jpg` 作为干净占位素材（零信号）；`干扰1.png` 接入
  `clean_ai` 场景，真实 OCR 触发 P3 AI 标注义务，Nike logo 使用单路 Vision mock，
  按规则最高 **P2**（mock 在 fixture 中明确标注）。合成 15s 视频保留用于高风险全链路。

**完成项**

- `scripts/p8_check.py`：启动真实服务，预热 OCR/ASR 后测图片 5 次、15s 视频 3 次，
  计算 p50/p90 并断言 SLA；随后跑评测与五场景回放，最后输出凭证/Docker 就绪状态。
- 延迟口径：预热模型后再采样，包含抽取层与工具层；mock 工具不产生外部成本。
- 误报：完整链路干净集 0/9，报告随 `make eval` 生成。
- 彩排：五个场景顺序与 URL 由 `make demo` 打印；用户占位素材已接入两个场景。
- Makefile 增加 `p8` / `verify-8`。

**未完成 / 阻塞**

- **实时凭证未验证**：本机已配置 Feishu 与 ACRCloud；Anthropic、Google Vision、
  AWS Rekognition 仍缺失，无法做真实 API 对照。
- **Docker 未验证**：本机没有 Docker，镜像与 compose 未实际构建运行。
- **录屏未产出**：本环境不能录屏；`make demo` 提供可复现的彩排路径与现场 URL。
- **用户素材尚未提交**：`v1.jpg`、`干扰1.png` 等是未跟踪文件；确认后可随 Phase 8 一起提交。
- 用户素材是演示占位，不代表真实客户品牌；Nike logo 命中为 mock，演示时需说明。

**下一步**：决赛彩排与录屏；有凭证/ Docker 的环境重跑 `make verify-8`，把阻塞项转为已验证。

## Phase 7（2026-11-25）✅

**验收**：`make verify-7`（清理/演示单测 → `scripts/p7_check.py` → `scripts/demo_check.py`）

- 结果：通过。TTL 清理删除过期素材 1 条、素材文件 1 个、抽取目录 1 个、孤儿授权文件 1 个，
  未过期素材与被 LicenseRecord 引用的授权文件保留；五个演示场景离线回放通过，
  批量汇总 4 条素材 / 7 条信号（P0 2、P1 2、P2 2、P3 1）。
- `pytest -q`：**140 passed**；ruff 全绿；`make verify-0/1/2/3/4/5/6` 回归通过。

**完成项**

- 演示缓存：`DEMO_MODE=true` 时工具优先读 `fixtures/cache/<tool>/<sha256>.json`
  （按素材与分镜/窗口键控），缺失才回落 mock / live；`scripts/make_demo_cache.py`
  从 flat mock 生成，已提交 5 个缓存文件。
- 回放：`GET /demo`、`GET /demo/replay/{id}`（HTML / `?format=json`）；五个场景为
  高风险视频全链路、授权到期、替换后复检、干净 AI 人像、历史批量巡检。
  场景数据幂等写入，卡片与复核页直接可用；`DEMO_MODE=false` 时接口返回 404。
- TTL：`app/workers/cleanup.py` 按外键顺序删除过期素材及其 Job / Detection / Finding /
  Evidence / ReviewAction / ToolCall / FeishuEvent / 授权挂接 / Campaign，并删除素材文件、
  `extracted/<sha256>/` 与未被引用的旧授权文件；`cleanup_loop` 每小时跑一次；
  `ASSET_TTL_HOURS` / `CLEANUP_INTERVAL_SECONDS` 可调。
- 部署：`Dockerfile`、`.dockerignore`、`docker-compose.yml`（app + worker）；
  `WORKER_IN_APP=false` 关闭 app 内 worker，避免双 worker；`app/workers/run.py`
  作为独立进程同时跑任务队列与清理。
- 验收：`scripts/p7_check.py`、`scripts/demo_check.py`、`tests/test_cleanup.py`、
  `tests/test_demo_api.py`，并在 `tests/test_tools_base.py` 增加 demo 缓存优先用例；
  Makefile 增加 `p7` / `verify-7` / `demo` / `cleanup`。

**未完成 / 已知限制**

- 本机没有 Docker，镜像与 compose 未实际构建运行，只做静态存在性与配置断言。
- `batch_scan` 是预置结果的汇总回放，不是对历史素材库重跑流水线。
- 演示素材 `expires_at=None`，TTL 清理不会删除它们；真实上传素材仍按 24h 清理。
- app 与 worker 共用 SQLite（WAL），仅支持单机；多 worker 仍需把认领改为条件更新。
- 演示缓存只覆盖已有 mock 的素材；新增素材需重跑 `scripts/make_demo_cache.py`。
- `make demo` 用 TestClient 离线验证并打印 URL；现场需要以 `DEMO_MODE=true` 启动服务。

**下一步**：Phase 8 —— 彩排、录屏、修误报；全链路 p50 达标（图片 <20s、15s 视频 <60s）。

## Phase 6（2026-11-25）✅

**验收**：`make eval`（先跑 Verifier / 指标单测，再生成 `eval_report.md` + `eval_report.json`）

- 结果：通过。评测集 **11 条**（干净 9 条，受冲刺模式 ≤12 条约束）：
  - 完整链路 P0/P1 **召回 4/4、精确 4/4、干净集误报 0/9**，外部调用 52 次；
  - 关闭 Verifier：51 次；关闭 Planner 帧预算：56 次（帧预算放大带来 4 次额外调用）；
  - Verifier：低置信候选 1 条、二次检索 1 次、置信升级 0 条（mock 下同一分镜命中同一缓存）。
- `pytest -q`：**131 passed**；ruff 全绿；`make verify-0/1/2/3/4/5` 回归通过。

**完成项**

- `app/agent/verifier.py`：`0.4 ≤ confidence < 0.7` 的 copyright_image / logo 信号 →
  bbox 外扩 20%（无 bbox 时退化为整帧）→ 裁剪 → 2× 放大 → 同工具重搜；
  置信提升才合并并写证据「Verifier 二次检索」，最多 `VERIFIER_MAX_CALLS` 次、1 轮。
- `app/agent/ablation.py` + config：`ABLATION_VERIFIER` / `ABLATION_PLANNER` /
  `BASELINE_VLM_ONLY` / `VERIFIER_MAX_CALLS`。
- `app/agent/vlm_baseline.py`：纯 VLM 基线只读 `fixtures/cache/vlm_baseline.json`，
  仅评测脚本消费，生产流水线不 import。
- `app/eval/metrics.py`：类别 +（ref 相同 / bbox IoU ≥0.3 / 时间重叠 ≥50%）匹配、
  P0/P1 precision/recall、干净集误报、Wilson 区间、延迟分位数。
- `app/eval/run_eval.py`：四组对照（VLM 基线 / 无 Verifier / 完整 / 关闭 Planner），
  预热抽取缓存后再计时，输出 Markdown + JSON。
- `scripts/make_eval_set.py`：生成 11 条评测集、6 张干净样本（含对抗样本 `clean_bike`）、
  一条低置信 image_search mock（触发 Verifier）与 VLM 基线缓存。
- 修复：mock 里 `visually_similar` 与归一化后的 `similar` 不一致，导致「仅视觉相似」
  被当成强命中出 P1；`websearch.assess` 现在两种写法都按弱命中处理。

**未完成 / 已知限制**

- 工具层仍是 mock：调用次数与成本可测，召回/误报反映的是 fixture 行为。
- 样本量远小于 PROMPT §15 的 40–60 条，整体 n<15，报告只给计数与 Wilson 区间。
- Verifier 的真实收益需要 live 凭证：mock 下二次检索命中同一缓存，置信不会提升。
- 纯 VLM 基线是缓存（含一条人工标注的幻觉误报），不代表当前模型的真实能力。
- 延迟包含抽取（已预热缓存），不等同线上 SLA；成本在 mock 下恒为 0。

**下一步**：Phase 7 —— 演示模式、缓存与兜底、TTL 清理、Docker Compose，
验收 `make demo` 离线可复现。

## Phase 5（2026-11-25）✅

**验收**：`make verify-5`（先跑三个飞书模块单测，再跑 `python scripts/p5_check.py`）

- 结果：通过。mock 回环 0.50s：`url_verification` 回显 challenge；图片消息事件建任务并回执；
  同一 `message_id` 重复投递不重复建任务；任务完成后推送「P1 · 高风险信号 | 2 条信号」卡片，
  卡片内复核链接带签名 token、页面可打开；驳回空理由返回 400、确认动作写入 `ReviewAction`。
- `pytest -q`：**120 passed**；ruff 全绿；`make verify-0/1/2/3/4` 回归通过。

**完成项**

- `app/feishu/client.py`：tenant_access_token（缓存、提前 60s 过期）、发文本/卡片、
  下载消息资源、多维表格追加；`FEISHU_MODE=mock` 全部写 `DATA_DIR/feishu_outbox.jsonl`。
- `app/feishu/events.py`：v2.0 事件解析、Verification Token 校验、`X-Lark-Signature` 校验、
  加密事件解密（`AESCipher`）、按 `message_id` 幂等、回执 → 下载 → `store_asset` 建任务。
- `app/feishu/longconn.py`：官方 SDK 长连接（WebSocket）监听 `im.message.receive_v1`，
  SDK 对象转 v2.0 JSON 后复用同一条处理路径；lifespan 内后台线程启动。
- `app/feishu/notify.py`：任务完成后按 `FeishuEvent.job_id` 推卡片；无信号时发文本；
  推送失败只标记 `notify_failed`，不影响任务状态。
- `app/api/feishu.py`：公网回调 `/api/feishu/event`（`url_verification`、验签、解密、
  Token 校验），与长连接共用逻辑；`FEISHU_ENABLED=false` 返回 503。
- 数据层：新增 `FeishuEvent`（`message_id` 唯一）承担幂等与「回推目标会话」。
- 输出：卡片复用 `pipeline/report.build_card`；复核链接改用 `PUBLIC_BASE_URL` 拼接。
- 验收与测试：`scripts/p5_check.py`、Makefile `p5`/`verify-5`、13 个新增单测；
  `tests/conftest.py` 现在会把 `.env` 里的真实凭证清空，保证单测与本机状态无关。

**未完成 / 已知限制**

- **长连接未经真实凭证验证**：`.env` 里还没有 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`，
  `lark-oapi` 已安装（`pip install -e ".[feishu]"`），凭证到位后启动服务即可看到
  「飞书长连接启动」日志。
- 多维表格只在配置 `FEISHU_BITABLE_APP_TOKEN` / `FEISHU_BITABLE_TABLE_ID` 时写入，
  当前未申请 `bitable:app` 权限。
- 图片、文件、视频三种消息类型都实现了下载与入库，但真实文件下载只用 mock 验证过。
- 卡片是 card 1.0 结构；若飞书客户端改版需要升级到 2.0，只改 `pipeline/report.py`。
- 复核链接默认指向 `PUBLIC_BASE_URL`，演示时需改成评委能访问的地址。

**下一步**：Phase 6 —— Verifier 二次检索 + 评测 + 消融，
验收 `make eval`：生成报告。

## Phase 4（2026-11-25）✅

**验收**：`make verify-4`（先跑四个台账单测，再跑 `python scripts/p4_check.py`）

- 结果：通过。三份合成授权书字段完整，登记 TikTok / SEA 投放后状态分别为
  `expired` / `out_of_territory` / `effective`；挂接素材后状态重算并写库；
  驳回空理由返回 400、确认动作写入 `ReviewAction`；台账链路 **1.01s**。
- `pytest -q`：**106 passed**；ruff 全绿；`make verify-0/1/2/3` 回归通过。

**完成项**

- 抽取层 `app/ledger/extract.py`：PDF（PyMuPDF 文本层，扫描件渲染后 OCR）、DOCX
  （解包 `word/document.xml`）、图片（复用抽取层 OCR）；标签行规则解析授权方 / 被授权方 /
  授权类型 / 范围 / 地域 / 媒介 / 起止日期 / 可转授权 / 限制，日期支持 `- / . 年月日`。
  文本层置信 0.95、OCR 0.6；关键字段缺失或低置信进入 `needs_review`。
- LLM 可选路径：`LEDGER_LLM_ENABLED=true` 且安装 `.[llm]` 时用 `claude-opus-5-5` 的
  `messages.parse` 补空字段；任何异常回退规则解析。
- 状态机 `app/ledger/status.py`：七种状态；优先级 needs_review > out_of_scope > expired >
  out_of_territory > out_of_media > expiring_soon > effective；地域 / 平台别名归一
  （SEA、东南亚、新加坡……；Meta = Facebook/Instagram）；无 Campaign 时不判地域/媒介并
  标注 `campaign_missing`。
- 台账挂接 `app/ledger/gate.py`：重算记录状态；给覆盖到的风险类别补 `license` 证据
  （幂等）；过期/地域/媒介/范围不符时把 P2/P3 抬到 P1，**不降级**。
- API `app/api/admin_ledger.py`：`POST /api/licenses/upload`、`GET /api/licenses`、
  `GET|PATCH /api/licenses/{id}`、`POST /api/licenses/{id}/link`、
  `POST /api/assets/{id}/campaign`、`GET /api/assets/{id}/ledger`。
- 复核页：新增「授权台账」面板（状态与原因、挂接记录、登记/修改投放信息）。
- 素材与验收：`scripts/make_p4_fixtures.py` 生成三份授权书 PDF（字体子集，约 144KB/份）；
  `scripts/p4_check.py`、Makefile `p4`/`verify-4`、26 个新增单测。

**凭证验证（2026-11-25）**

- ACRCloud **已用真实凭证验证**（中国区 `identify-cn-north-1.acrcloud.cn`）：
  单窗口识别 1.09s 返回 `1001 no_match`（演示素材是合成旋律，属预期），
  完整 BGM 链路 `TOOLS_MODE=live` 跑通，`ToolCall(music, ok=True, cost=$0.008)` 记账正常，
  输出 P2「未识别出背景音乐」而非空结果。
- 首次调用返回 `3006 invalid arguments`：请求缺 `sample_bytes` 字段。已修复
  （`app/tools/music.py`）并补回归测试 `test_live_request_sends_sample_bytes`。
- 仍待真实凭证验证：Google Vision（图搜 / logo）、AWS Rekognition（公众人物）。

**未完成 / 已知限制**

- 字段抽取针对固定标签行模板；自由格式、手写、印章文件需要 LLM 路径或人工补录。
  LLM 路径未用真实凭证验证（当前无 `ANTHROPIC_API_KEY`）。
- `sublicensable` / `restrictions` 只做记录，不参与状态判定。
- 素材↔授权仍是人工挂接（PROMPT §9 的 MVP 边界），挂接后会自动重算，但不会自动匹配。
- Campaign 只有 platform / market / 起止日期，没有品类字段，所以商标词仍最高 P1。
- 授权书上传上限 20MB；原始文件留在 `DATA_DIR/licenses/`，TTL 清理在 Phase 7 一并处理。

**下一步**：Phase 5 —— 飞书收发 + 风险卡片 + 多维表格（可选），
验收 `make p5`：测试租户 round-trip。



## Phase 3（2026-11-25）✅

**验收**：`make verify-3`（先跑三个模块单测，再跑 `python scripts/p3_check.py`）

- 结果：通过。`sample_15s.mp4` 上传到完成 **8.13s**，命中
  `logo=P0`（Vision + CLIP + OCR 三路证据，注册号 USPTO 6124779）、
  `trademark_text=P1`（OCR 命中 NIKE，附注册号与来源链接）、
  `policy=P3`（Meta 第三方商标条款）；卡片标题「P0 · 高风险信号 | 3 条信号」。
- 干净素材 `clean_ai.png`：**无 P0/P1、无 logo / 商标词**；仅 1 条 P3
  （水印「豆包AI生成」触发 TikTok/Meta AI 标注条款提示），符合「干净素材不误报P0/P1」。
- `pytest -q`：**80 passed**；ruff 全绿；`make p0/p1/p2` 回归通过。

**完成项**

- 数据层 `data/watchlists/`：
  - `brands.json`：56 条品牌（含 4 个通用词）+ 别名 / 注册号 / tier / sector；
    Nike 注册号经公开聚合站核对，其余标记为待核验。
  - `competitor_terms.json`：竞品词表，默认不启用，需按客户配置。
  - `policy_clauses.yaml`：TikTok / Meta 24 条政策条款（id 唯一校验，带生效日期与官方 URL）。
- 商标词（`app/tools/trademark.py`）：全半角/大小写/™® 归一；拉丁词边界精确匹配；
  ≥6 字符允许 1 字符差异；中文别名 n-gram 精确匹配；通用词 P3、竞品词 P2、无注册信息 P2；
  证据含注册号、来源链接、文字 bbox 与帧时间戳。
- 政策 RAG（`app/tools/policy.py`）：BM25Plus（rank_bm25）检索，中文查询靠 summary/tags 对齐；
  阈值 `POLICY_MIN_SCORE=8.0`；输出前校验 clause_id；无达标条款不产出；信号固定 P3 并引用原文与生效日期。
- logo（`app/tools/logo.py`）：Vision `LOGO_DETECTION` 适配、CLIP 余弦 top-k、OCR 文本佐证三路投票；
  ≥2 路 + 注册信息 → P0，≥2 路无注册 → P1，单路 / 词表外 → P2；只有 OCR 命中不产出 logo 信号。
  open_clip 作为可选依赖（`.[clip]`），未安装时该路自动跳过。
- 编排：`pipeline/detect.py` 增加 logo → trademark → policy 三个确定性阶段；
  单工具失败仍只丢该信号。
- 验收与测试：`scripts/make_p3_fixtures.py` 生成 `fixtures/cache/logo.json`；
  `scripts/p3_check.py`、Makefile `p3`/`verify-3`、31 个新增单测（三个模块各一组）。

**未完成 / 已知限制**

- 词表为演示子集（56 条，PROMPT 要求 ≥500）；仅 Nike 的注册号经核对，其余 `reg_no=null`，
  因此只能产出 P2。正式使用前需导入核验过的词表。
- 政策条款为人工整理的演示摘录，`effective_date` 为采集时页面标注/近似值；
  对外引用前须逐条回官方页面核对。
- 商标词的 P0 需要「high-tier + 注册信息 + 同品类投放上下文」，Campaign 在 Phase 4 才接入，
  当前最高 P1。
- CLIP 向量库未随仓库分发（需品牌 logo 图片与 `.[clip]` 权重）；未安装时 logo 依赖
  Vision live 或 mock + OCR 佐证。
- VLM 复核（候选品牌内回答）留到 Phase 6 Verifier；当前 mock 不调用 LLM。
- 政策信号统一 P3；与平台/地区上下文联动的定级在 Campaign 接入后再做。

**下一步**：Phase 4 —— 授权台账 + 复核页增强，
验收 `make p4`：三份授权书状态正确（有效 / 过期 / 地域不符），复核动作落库。

## Phase 2（2026-11-25）✅

**验收**：`make verify-2`（等价 `make p2` / `python scripts/p2_check.py`）

- 结果：通过。`risk_ad_10s.mp4` 上传到完成 **8.12s**（ASR 关闭；OCR 模型已缓存），命中
  `copyright_image=P0`（Shutterstock full match）、`celebrity=P1`（合成人像）、`bgm=P2`
  （示例曲目）；卡片标题「P0 · 高风险信号 | 3 条信号」，页脚免责声明齐全。
- 干净素材 `clean_ai.png`：**0 条信号**，无 P0/P1、无公众人物误报。
- `ToolCall` 记账：image_search 5 次、celebrity 4 次、music 1 次，mock 成本 $0。
- `pytest -q`：**49 passed**；ruff 全绿；`make verify-0`、`make verify-1` 回归通过。

**完成项**

- 工具运行时（`app/tools/base.py`）：每 Job 预算、熔断（3 次/60s → 60s）、live 结果缓存、
  `ToolCall` 记账；mock 不计费。`TOOLS_MODE=mock|auto|live|off`。
- 版权图像（`websearch.py`）：Vision WEB_DETECTION 响应归一化、图库/电商/社交/博客域名分类、
  定级规则（full/partial + 图库 → P0；未知来源匹配页 → P1；仅相似 → P2）；无凭证降级。
- BGM（`music.py`）：头/中/尾 10s 窗口、ACRCloud HMAC-SHA1 签名、code 1001 → no_match；
  识别到曲目 → P2「请核对授权」，全窗口未识别 → P2「未知音乐」。
- 公众人物（`celebrity.py`）：Haar 检测 + 人脸裁剪后外发、AWS SigV4、只落
  姓名/置信度/bbox/时间戳；命中 ≥0.80 → P1，附 Rekognition 参照记录。
- 编排与融合：`pipeline/detect.py` 确定性 DAG（图搜 → 名人 → BGM），按分镜轮流选帧；
  `fuse.dedupe_signals` 同参照物取最高等级；`queue.py` 工具层失败只降级、不拖垮 Job。
- 素材与验收：`scripts/make_p2_fixtures.py` 生成演示视频 + 三个 mock JSON；
  `scripts/p2_check.py`、Makefile `p2`/`verify-2`、26 个新增单测。

**未完成 / 已知限制**

- 三个 live 适配器中 **ACRCloud 已于 2026-11-25 用真实凭证验证**（见 Phase 4「凭证验证」）；
  Google Vision 与 AWS Rekognition 仍未验证，凭证到位后先跑单素材对照，再切 `TOOLS_MODE=auto`。
- 服务账号鉴权需 `pip install -e ".[vision]"`（google-auth）；API key 路径无额外依赖。
- 名人 live 只送第一张人脸的裁剪；多人画面会漏检其余人脸（Phase 6 Verifier 再补）。
- 未识别 BGM 的「高价值信号」只在 live 明确 no_match 时产出；mock 缺 fixture 不编造。
- 无授权台账：BGM 命中统一 P2，不判断过期/地域（Phase 4 接入 license 证据后才能升 P0/P1）。
- pHash 去重后本素材只选到 4 帧；图搜/名人帧预算仍受 `MAX_FRAMES=12` 与
  `TOOL_*_FRAMES` 双重约束。

**下一步**：Phase 3 —— logo 库 + 商标词表 + 政策 RAG，
验收 `make p3`：三个模块单测全绿（植入「Nike」命中、`bike` 不误报、政策条款引用带生效日期）。

## Phase 1（2026-11-25）✅

**验收**：`make verify-1`（等价 `make p1` / `python scripts/p1_check.py`）

- 结果：通过。15s 视频上传到完成 **12.88s**（含 OCR/ASR 模型首次加载，未计 SLA）；
  检出 **3 个分镜、4 个关键帧**；OCR 识别到
  `LOGO / SALE 50% OFF / NEW ARRIVAL / NIKE / 限时优惠 / 立即购买`；
  ASR（small/int8，language=en）转写
  `This is a 15 second test advertisement. Big sale today. Shop now for the new arrival. Limited time offer.`
- `pytest -q`：**23 passed**；ruff 全绿；`make verify-0` 回归通过。

**完成项**

- 素材层：ffmpeg 探测时长/分辨率/帧率/音轨，回写 `Asset.duration/width/height`；
  系统无 ffmpeg 时用 `imageio-ffmpeg` 自带二进制，ffprobe 缺失时解析 `ffmpeg -i`。
- 抽取层：
  - `app/pipeline/ffmpeg.py`：二进制定位、探测、抽音频（16k 单声道）、按时间戳抽帧。
  - `app/pipeline/scene.py`：PySceneDetect 分镜（可退均匀分段）、每镜 ≤3 帧 / 全片 ≤12 帧、
    Laplacian 清晰度排序、pHash 汉明 ≤6 去重、场景间轮流取帧。
  - `app/pipeline/ocr.py`：PaddleOCR / rapidocr-onnxruntime 双后端、结果结构归一化、
    bbox 归一化到 0–1；不可用时 status=unavailable。
  - `app/pipeline/asr.py`：faster-whisper small/int8/CPU，懒加载 + 单例，分段带起止时间。
  - `app/pipeline/extract.py`：编排 probe→asr→scene→frames→ocr，产物落盘
    `data/extracted/<sha256>/extraction.json` + `frames/*.jpg`，单步失败只降级并记 warnings。
- 接口与队列：`GET /api/jobs/{id}/extraction`；`/api/jobs/{id}` 增加 `extraction_available`；
  worker 阶段进度 `extract:probe|asr|scene|frames|ocr`，Detection 记抽取摘要。
- fixture：`scripts/make_fixture_video.py` 用 Pillow + SAPI 生成 15s 三镜视频
  `fixtures/media/sample_15s.mp4`（1280x720、H.264/AAC、含英文口播）。
- 验收与测试：`scripts/p1_check.py`、13 个新增单测、Makefile `p1`/`verify-1`；
  README / DECISIONS(D011–D014) / `.env.example` / pyproject 依赖同步更新。

**未完成 / 已知限制**

- OCR 实际运行引擎是 **rapidocr-onnxruntime**（PP-OCR 模型的 ONNX 分发）。PaddleOCR
  适配器已实现，但本机未装 paddlepaddle；`OCR_ENGINE=auto` 在装了 PaddleOCR 的环境会优先它。
  需要严格锁栈时执行 `pip install -e ".[paddle]"`。
- ASR 首次运行需从 `HF_ENDPOINT`（默认 hf-mirror）下载约 464MB 权重到 `data/models`；
  本机已缓存，离线环境需预置。
- 视频无音轨 → ASR status=no_audio；OCR/ASR 失败只降级信号，不阻塞卡片。
- 风险信号仍来自 `fixtures/fake_findings.json`，Phase 2 接入真实工具层后替换。
- `EXTRACT_ENABLED=false` 时跳过抽取（`p0_check` 与单测使用，隔离 5 秒预算）。

**下一步**：Phase 2 —— 图搜 + BGM + 名人（mock 优先，再接真），
验收 `make p2`：植入素材命中预期信号。

## Phase 0（2026-11-25）✅

**验收**：`make verify-0`（等价 `make p0` / `python scripts/p0_check.py`）

- 结果：通过。上传样例图 → fixture 卡片完成耗时 **0.39s**（上限 5s）；
  卡片含 P1 标题、2 条信号、免责声明、复核链接；复核页可打开；无效签名返回 403。
- `pytest -q`：**10 passed**（1 条 Starlette/httpx 弃用警告），ruff 全绿。
- `git status --short`：仓库已初始化，本阶段 38 个文件已提交为基线（commit 见 `git log`）。

**完成项**

- 项目骨架：`pyproject.toml`、`Makefile`、`.env.example`、`.gitignore`，目录按 PROMPT §5。
- `config.py`：pydantic-settings，覆盖全部 env 键与外部工具超时。
- `db.py`：SQLite + WAL + `foreign_keys=ON`；`models.py`：§6 全部实体、分类枚举与中文标签。
- 上传 API：`POST /api/upload`（sha256 去重、媒体类型校验、200MB 上限、图片宽高）；
  `GET /api/jobs/{id}` 返回 job / asset / findings / 卡片 JSON。
- Job 队列：`workers/queue.py` 轮询认领 + lifespan 内 worker；失败只标记任务，不拖垮进程。
- 占位流水线：`fixtures/fake_findings.json` → Detection / Finding / Evidence（dedupe_key 去重）。
- 证据红线：`pipeline/fuse.py`，P0/P1 无 reference/clause/license 证据自动降 P2 并注明。
- 卡片：`pipeline/report.py` 输出飞书 card 1.0 JSON，页脚固定免责声明。
- 最小 Web：上传页（轮询进度、信号列表、证据链入口）、复核页（HMAC 签名 URL、确认/驳回、
  驳回必填理由并写 ReviewAction）。
- 验收与测试：`scripts/p0_check.py`、`scripts/make_fixture_image.py`、10 个单测。

**未完成（按计划后移）**

- 真实媒体规范化（ffmpeg）、文件大小/时长抽取：Phase 1。
- 飞书收发、卡片推送、多维表格：Phase 5。
- 真实工具适配器（图搜/BGM/名人/logo/政策 RAG）：Phase 2–3。
- 授权台账与复核页增强：Phase 4。
- Verifier 二次检索、评测与消融：Phase 6。
- 演示模式/缓存、TTL 清理任务、`docker-compose.yml`、`app/workers/run.py`：Phase 7。

**阻塞 / 环境说明（2026-11-25 验收时修复）**

- GNU Make 4.4.1 已通过 winget（`ezwinports.make`）安装到用户目录；已打开的 shell
  需要重开，或手动把 `%LOCALAPPDATA%\Microsoft\WinGet\Packages\ezwinports.make_*\bin`
  加入 PATH。
- `pytest` 只在 `.venv` 内：验收前先 `source .venv/Scripts/activate`。
- 仓库为本次验收时 `git init`，38 个文件已 `git add`，**尚未提交**。
- `StarletteDeprecationWarning`：Starlette TestClient 提示改用 `httpx2`，不影响运行，
  升级依赖时处理。
- 外部凭证未提供（Anthropic、GCP Vision、ACRCloud、AWS、飞书）：Phase 2 起需要；
  Phase 0/1 走 mock 不阻塞。
