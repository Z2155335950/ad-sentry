
# 【10 小时冲刺模式 · 覆盖原提示词全部范围】
只实现：本地上传（图片优先；视频只收预抽帧+时间戳 JSON）→
图搜 / ACRCloud / 离线商标词表（各带 fixture）→ 简单融合定级 →
风险卡片 → 复核页（证据链 + 授权台账状态机）→ DEMO_MODE 缓存。
禁止：飞书从零接入、ASR、分镜、人脸/名人、AI 检测、C2PA、字体、
Agent/LangGraph、评测集 >12 条、多维表格、PDF、批量巡检、爬 USPTO。
所有外部调用必须有 mock；主路径不得依赖 LLM 自由生成。
每 2 小时必须有一次可运行提交，更新 PROGRESS.md。


# 素材哨兵 AdSentry · AI 开发总提示词（Master Prompt）

> 配套文档：同目录 `FDE-01-实施方案.md`（原始方案）、`DOAI-报名文案.md`（对外文案）。**冲突时以本文件为准。**
> 比赛：DOAI 2026 · FDE 赛道（飞书深诺命题），决赛 12/5–6，8 分钟路演 + 5 分钟问答，开发窗口约 10 天。

## 0. 使用方式（给人看）

1. 新建空目录 `ad-sentry/`，把本文件复制为 `ad-sentry/PROMPT.md`。
2. 用 Claude Code / Cursor 打开该目录，发送下面的「开场指令」。
3. 每次只让 AI 做一个 Phase，跑完验收命令再继续；要求它维护 `PROGRESS.md`。
4. 新会话先让它读 `PROMPT.md` + `PROGRESS.md`，不要让 AI 重新发明方案。

## 开场指令（复制给 AI）

```
你是本项目的主程。先通读 PROMPT.md（以及能找到的 FDE-01-实施方案.md 作为业务背景）。
现在只做三件事，不要写代码：
1) 输出最终文件树与 Phase 0 的 ≤15 条任务清单；
2) 指出提示词中与现行 API/库不一致之处（如有）；
3) 列出需要我提供的凭证名称（只列名称，不要索要明文）。
完成后停下等我确认。之后每次只实现一个 Phase，结束时运行验收命令并更新 PROGRESS.md。
```

---

# 提示词正文

## 1. 角色与目标

你是资深全栈 + 多模态检索/风控系统工程师。目标：实现「素材哨兵 AdSentry」——面向出海广告投放的**上线前侵权风险筛查助手**。用户（设计师/投放）在飞书群里丢图片或短视频，系统在 45 秒内返回带证据链的风险卡片（P0–P3），点开进入 Web 复核页，人工确认/驳回后归档。

**只输出风险信号与技术证据，不输出「侵权」判定。** 这是产品红线，也是答辩时最容易被追问的地方。

交付物：可运行系统 + 演示模式（离线可复现）+ 评测报告（含消融）+ 架构说明。

## 2. 产品红线（硬约束）

1. 对外文案禁用「侵权」「违法」作判定，统一用「疑似 / 风险 / 需确认」；每张卡片页脚固定免责声明。
2. **P0/P1 必须至少有一条参照物证据**（reference / clause / license 三类之一）；只有模型判断的，最高 P2。
3. 不做普通人身份识别；不自建人脸库；人脸图像与特征不落盘（只存是否命中、置信度区间、时间戳）。
4. 没有参照物的类别不自动检测（声音权、外观专利、赛事 IP、超出 logo 库的卡通 IP），只输出规则清单提示。
5. 所有第三方调用集中在 `app/tools/` 适配器内：统一超时、重试、熔断、成本记账、可 mock。业务代码不得直接 import 供应商 SDK。

## 3. 交付范围

**P0（演示必需）**
- 飞书机器人：收图/收视频、异步回执、风险卡片；本地 Web 上传作为降级入口
- 抽帧 + OCR + ASR；反向图搜；ACRCloud BGM；logo 检索；授权台账（抽取 + 状态机）
- Web 复核页最小版（证据链 + 确认/驳回）
- 评测集 v1（≥20 条，其中干净素材 ≥8 条）

**P1（拉开差距）**
- 公众人物（AWS Rekognition）；政策 RAG；Agent 二次检索 + 消融；多维表格留档
- AI 生成检测（C2PA + 检测器，弱信号）

**P2（有余力）**
- 字体识别（WhatFontIs）；批量巡检 UI；PDF 报告；阈值调优

**明确不做**：实时 USPTO/EUIPO 全文检索（无官方 API）、自建人脸库、全语种商标检索、声音权自动判定、真实法律意见。

## 4. 技术栈（锁定，不讨论替代）

- Python 3.11；FastAPI；SQLModel + SQLite；pydantic v2 + pydantic-settings；httpx
- 媒体：ffmpeg；PySceneDetect；PaddleOCR（chi_sim+en）；faster-whisper（small, int8, CPU）；imagehash（pHash）
- 向量：open_clip（ViT-B/32）+ numpy 余弦（库 ≤300 条，不用 FAISS/pgvector）
- 检索：rank_bm25 + YAML 条款（不引入向量数据库）
- Agent：可选 langgraph；**默认手写确定性 DAG**，Agent 只出现在 Verifier 二次检索
- LLM/VLM：anthropic Python SDK；默认 `claude-opus-5-5`；provider 可替换
- 前端：Jinja2 + 原生 JS（不引框架）；Docker Compose（app + worker）

### 4.1 LLM 调用细则（必须照做）

- `client = anthropic.Anthropic()`；模型读 `LLM_MODEL`，默认 `claude-opus-5-5`。
- 结构化输出用 `client.messages.parse(...)`（或 `output_config={"format": {...}}` + pydantic 校验）；**不要用旧的 `output_format` 参数**。
- 图片按 base64 `image` content block 传入；**视频不能直传**，先抽帧再送，单次 ≤8 帧。
- 开启自适应思考：`thinking={"type": "adaptive"}`；成本控制用 `output_config={"effort": ...}`（默认 `medium`，关键判定 `high`）。
- 每次调用记录 `usage`（input/output tokens）与估算成本到 `ToolCall` 表；单次超时 30s，重试 1 次，失败降级不崩溃。
- 定义 `LLMClient` 协议：任何供应商实现都必须返回 pydantic 校验后的对象；解析失败重试一次，仍失败则丢弃该信号（绝不抛到主流程）。
- 参考价格（美元/百万 token）：Opus 5.5 `$4 / $20`；Sonnet 5.5 `$2 / $10`；Haiku 5.5 `$0.10 / $0.50`。批量字段抽取可选更便宜档，但**主判定用 Opus 5.5**。
- 提示词里固定：「你只做事实判断（是否出现、置信度），不给法律结论；不确定时输出 null，不要猜测。」

## 5. 目录结构（必须遵循）

```
ad-sentry/
  PROMPT.md  PROGRESS.md  DECISIONS.md  README.md  Makefile  .env.example
  docker-compose.yml
  app/
    main.py  config.py  db.py  models.py
    api/ feishu.py  review.py  upload.py  admin_ledger.py
    pipeline/ ingest.py  extract.py  scene.py  ocr.py  asr.py  fuse.py  report.py
    tools/ base.py  websearch.py  music.py  celebrity.py  logo.py  trademark.py
           policy.py  aigen.py  font.py  vlm.py
    agent/ verifier.py  ablation.py
    ledger/ extract.py  status.py
    workers/ queue.py  cleanup.py
    eval/ metrics.py  run_eval.py
  data/watchlists/brands.json  competitor_terms.json  policy_clauses.yaml
  fixtures/media/ licenses/ cache/
  templates/review.html  upload.html
  tests/
```

## 6. 数据模型（SQLModel）

```python
Asset(id, sha256, media_type, path, duration, width, height, c2pa: dict|None, created_at, expires_at)
Job(id, asset_id, status: queued|running|done|failed, stage, error, created_at, started_at, finished_at)
Detection(id, job_id, asset_id, tool, raw: dict, confidence, cost_usd)
Finding(id, asset_id, category, level, confidence, suggestion, review_status, dedupe_key)
Evidence(id, finding_id, kind: reference|clause|bbox|timestamp|metadata|license,
         ref_url, ref_record_id, bbox: str|None, t_start, t_end, clause_id, note)
LicenseRecord(id, licensor, licensee, scope_type, scope_items: json, territory: json,
              media: json, start, end, sublicensable, restrictions: json,
              source_doc, field_confidence: json, status)
AssetLicense(asset_id, license_id, linked_by, linked_at)
Campaign(id, asset_id, platform, market, start, end)   # 关键：授权与政策判断的上文
PolicyClause(id, platform, section, text, effective_date, url)
ReviewAction(id, finding_id, reviewer, action: confirm|reject, reason, created_at)
ToolCall(id, job_id, tool, input_hash, ok, latency_ms, cost_usd, error, created_at)
ToolCache(key, tool, result_json, created_at)          # 演示与省钱都靠它
```

分类枚举：`logo / copyright_image / celebrity / bgm / trademark_text / policy / ai_generated / font / license`
中文标签：商标标识 / 版权图像 / 公众人物 / 背景音乐 / 商标词与文案 / 平台政策 / AI 生成 / 字体 / 授权。
`Campaign` 缺失时，地域与媒介类判断只能输出「无法核验，需补充投放信息」（P2），**不得猜**。

## 7. 流水线与 SLA

```
接入 → 素材层(下载/规范化/pHash/TTL) → 抽取层(分镜/关键帧/OCR/ASR/C2PA)
→ 工具层(并行适配器) → 融合层(去重/分级) → 输出层(卡片/复核页/多维表格)
```

- 图片目标 p50 < 20s；15 秒视频目标 p50 < 60s，卡面文字写「45 秒内」，卡片先回执再更新。
- 每个外部工具独立超时：图搜 8s、ACRCloud 10s、Rekognition 8s、LLM 30s；单工具失败只丢该信号。
- 熔断：同一工具 60 秒内连续 3 次失败则打开 60s，期间直接走缓存/mock。

## 8. 检测模块：难点 → 方案 → 细节 → 验收

### 8.1 版权图像（反向图搜）

**难点**：召回依赖搜索引擎索引；按帧计费；「找到了相似图」不等于「找到了权利人」；视频帧数会爆炸。

**方案**
- 帧预算：PySceneDetect（ContentDetector，threshold=27，min_scene_len=15）切镜 → 每镜最多 3 帧 → 全片 ≤12 帧；pHash 去重（汉明距离 ≤6）；优先取清晰度（Laplacian 方差）与 OCR 文字面积高的帧。
- 调用 Google Vision `WEB_DETECTION`，解析 `pagesWithMatchingImages`、`fullMatchingImages`、`partialMatchingImages`、`visuallySimilarImages`、`bestGuessLabels`。
- 来源页性质按域名分类：图库（shutterstock/getty/istock/adobe-stock/alamy/depositphotos/123rf/dreamstime/freepik/vecteezy）、电商、社交、博客、未知；只抓页面 `<title>` 与 meta（3s 超时），不抓正文。
- 定级规则：full/partial match + 图库域名 → 高确定性（可 P0/P1）；仅 visually similar → 中确定性；无命中 → 不产出。
- 降级：GCP 不可用/未配置 → 对客户素材库做 pHash 查重，输出「与历史素材高度相似」。

**细节**：证据必须含 `ref_url` + 页面标题 + 匹配类型 + 帧时间戳；同 URL 去重。

**验收**：植入图命中并给出页面 URL；断网时走缓存返回 fixture，不崩溃。

### 8.2 BGM（音频指纹）

**难点**：识别≠有授权；「未知音乐」本身是高价值信号；按次计费。

**方案**
- ffmpeg 抽 16k 单声道音频；取 3 个 10 秒窗口（头/中/尾）；ACRCloud Identify（HMAC-SHA1 签名）。
- 缓存键 = sha256(音频窗口)；命中缓存不再计费。
- 定级：识别到商业曲目且台账**无**记录 → P2「请核对授权」；台账显示过期/地域不符/范围外 → P0/P1；未识别 → P2「未知音乐，上线前请确认授权」。
- 已知接口行为：成功返回 metadata（title/artist/label/offset），无匹配返回 code 1001。

**验收**：植入曲目命中并给片段区间；未知曲目输出「未知音乐」而不是空结果。

### 8.3 商标 / 品牌 logo

**难点**：CLIP 小库误报高；VLM 会编造品牌名；视频角落 logo 分辨率低；品牌近似形干扰。

**方案（三路证据投票）**
1. 若已开通 Google Vision `LOGO_DETECTION`，直接用官方 logo 检测拿 bbox + 品牌名（首选）。
2. 自建库：`data/watchlists/brands.json`（100–300 条：品牌名、别名、类别、注册号、来源 URL、logo 图片路径）；open_clip 预计算向量，余弦 top-5。
3. OCR 文本与品牌名/别名做 rapidfuzz 匹配（≥85 分，拉丁词要词边界；中文≥2 字）。
4. VLM 复核：只问候选列表内的 5 个品牌，按固定 JSON schema 返回。

**判定**：至少两路一致才 ≥P1；只有 VLM 说"是"→ 最高 P2；库内必须存在注册信息才允许 P0。bbox 优先取 Vision/OCR 的框；视频跨帧聚合，取最佳帧做证据。

**VLM 复核提示词（固定）**
```
system: 你是广告素材合规复核助手，只做事实判断，不给法律结论。
user: 候选品牌：{top5 名称与别名}。判断图中是否出现其中任一品牌的标识
（logo、文字招牌、产品上的商标）。不确定输出 present=null，不要猜测未列出的品牌。
只输出 JSON：{"brand": str|null, "present": true|false|null,
"location": "left-top|right-top|...|null", "text_seen": str|null, "confidence": 0..1}
```

**验收**：植入 logo 命中并给 bbox 与品牌；干净素材 0 误报；「bike」不能命中 Nike。

### 8.4 公众人物

**难点**：合规（生物识别数据）；名人库以欧美为主；AI 生成的"明星脸"不会被识破；Google 名人识别已于 2025-09 关停，**禁止使用**。

**方案**
- AWS Rekognition `RecognizeCelebrities`（约 $0.001/图）；先用人脸检测框裁剪人脸区域再发送，最小化数据。
- 结果只写库：是否命中、名字、置信度区间、帧/bbox；**不存人脸图与特征**；日志不写素材内容。
- 命中且授权台账无对应记录 → P1「疑似公众人物，需确认肖像授权」；未命中不做任何推断。
- VLM 只允许输出「是否存在疑似公众人物」的低置信提示（最高 P2），不得点名。

**验收**：名人测试图命中；普通人像零命中、零误报。

### 8.5 AI 生成 / C2PA

**难点**：C2PA 在剪辑导出后基本丢失；检测器概率不可靠；不能当证据。

**方案**：读取 C2PA（`c2patool` 可选）；检测器 API 可选（Sightengine/Hive），归一为「AI 疑似概率（检测器名）」。**最高 P2**：「来源不明/AI 疑似，按平台要求确认标注义务」。绝不能单独触发 P0/P1。

### 8.6 商标词 / 文案（离线词表，替代 USPTO 检索）

**难点**：USPTO 无官方全文检索 API（TSDR 仅按注册号查；公共检索站是内部接口，爬虫随时挂）；EUIPO 接口需申请；关键词直配会误报洪水。

**方案**
- `data/watchlists/brands.json` 离线词表（≥500 条）：`{mark, aliases[], owner, jurisdictions[], classes[], reg_no, source_url, tier}`。
- 输入：OCR 文本 + ASR 转写；归一化（大小写、全半角、标点、空格）。
- 匹配：拉丁词用词边界 + 允许品牌后缀（™/®），模糊 ≤1 字符差异；中文按 n-gram 精确匹配。
- 负向处理：通用词（PLUS/PRO/ULTRA 等）只给 P3；竞品词表单独维护；P0 仅限 high-tier 品牌 + 同品类上下文。
- 证据：命中词、品牌、注册号、来源 URL、所在帧/bbox。

**验收**：「Nike」命中并附注册号；「bike」不误报；词表整体可离线运行。

### 8.7 平台政策 RAG

**难点**：政策页会变；回答容易转述走样；不能编条款。

**方案**：`policy_clauses.yaml` 人工整理 ≥20 条（TikTok/Meta），字段 `id/platform/section/text/effective_date/url`；rank_bm25 取 top3；**必须引用原文与生效日期**；无达标条款则不产出；输出前校验 `clause_id` 存在。

**验收**：查询「AI 标注义务」返回对应条款原文与日期；无相关条款时输出空并记录 no_clause。

### 8.8 字体（P2）

WhatTheFont **没有公开 API**（已核实）；如需做，用 WhatFontIs API（200 次/天免费，约 $1/1000），只输出提示级「字体来源待确认」。写进方案时不要写 WhatTheFont。

## 9. 授权台账（差异化重点）

**难点**：素材与授权书如何对齐；授权范围（地域/媒介/期限/角色）与投放上下文如何比对；扫描件/手写/印章抽取质量。

**方案（MVP 边界要守住）**
- 上传授权文件（PDF/DOCX/图片）→ 文本抽取（PyMuPDF / python-docx / PaddleOCR）→ LLM 结构化抽取。
- 抽取 schema：
```json
{"licensor":"","licensee":"","scope_type":"music|image|trademark|character|font|other",
 "scope_items":[],"territory":[],"media":[],"start":"YYYY-MM-DD","end":"YYYY-MM-DD",
 "sublicensable":null,"restrictions":[],
 "field_confidence":{"territory":0.0,"end":0.0,"start":0.0,"scope_type":0.0},
 "missing_fields":[]}
```
- 任一关键字段置信 <0.7 或缺失 → 进入「待人工录入」，不参与判定。
- **素材↔授权记录在 MVP 中由人工挂接**（复核页选记录），实体自动对齐不进 MVP；写进答辩话术。
- 状态机：`effective | expiring_soon(≤30天) | expired | out_of_territory | out_of_media | out_of_scope | needs_review`。
- 范围判定依赖 `Campaign(platform, market, start, end)`：无 Campaign → 输出「无法核验，需补充投放信息」（P2），不得默认通过或拒绝。

**演示准备**：三份合成授权书（PDF），分别覆盖「已过期」「地域不含东南亚」「有效」；对应三张卡片演示状态机。

**验收**：三份文档抽取正确；过期/地域不符能给出具体字段证据；缺 Campaign 时不误判。

## 10. Agent 编排与消融

- 主干**确定性 DAG**：extract → tools（按媒体类型选择）→ fuse → report。Planner 用规则表，不用 LLM 决定跳过哪些工具（防止漏检）。
- Verifier（唯一 Agent 环节）：对 `0.4 ≤ confidence < 0.7` 的结果做一次二次检索——bbox 外扩 20% 裁剪 + 2 倍放大重搜；logo 场景做 OCR/CLIP/VLM 交叉投票；最多 1 轮，受预算约束。
- 消融开关（env）：`ABLATION_VERIFIER=0/1`、`ABLATION_PLANNER=0/1`、`BASELINE_VLM_ONLY=1`。
- 纯 VLM 基线：单次多图调用让模型列风险，仅用于评测对比，绝不进生产路径。
- 评测报告必须输出：调用次数对比、单素材成本对比、干净集误报对比、植入集召回对比。

## 11. 风险定级与证据

**证据结构**：`kind ∈ {reference, clause, bbox, timestamp, metadata, license}`；P0/P1 至少一条 `reference|clause|license`。

**Rubric（后果严重度 × 判定确定性）**

| 后果 \ 确定性 | 直接命中 ≥0.7 | 候选 0.4–0.7 | 仅模型 <0.4 |
| --- | --- | --- | --- |
| 高（商业图库/知名商标/公众人物） | P0 | P1 | P2（无参照物） |
| 中（未知来源/AI 疑似/字体存疑） | P2 | P2 | P3 |
| 低（规则提示） | P3 | P3 | P3 |

冲突处理：同素材同类别信号等级不一致时取高者，并注明「工具结论冲突」。去重键：`category + ref_id/brand/clause_id`，保留证据最完整的一条。

**措辞模板**：卡片标题「P0 · 高风险信号」；正文「检测到疑似 Nike 商标标识（置信 0.82），参照 USPTO 注册号 XXX」；页脚固定免责声明。

## 12. 飞书接入

- 配置：`FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_VERIFICATION_TOKEN / FEISHU_ENCRYPT_KEY`；`FEISHU_ENABLED=false` 时走本地上传页。
- 事件：订阅 `im.message.receive_v1`；处理 `url_verification` challenge；校验签名；按 `event_id/message_id` 幂等去重。
- 收文件：`im/v1/messages/{message_id}/resources/{file_key}` 下载，限制 200MB。
- 发卡片：`im/v1/messages?receive_id_type=chat_id`，`msg_type=interactive`；卡片含等级色、信号列表、证据摘要、「查看证据链」按钮（签名 URL）。
- 按钮跳转复核页（HMAC 签名，24h 有效）；多维表格（P1）用 `bitable` API 追加记录。
- **演示风险**：飞书回调需要公网地址；本地开发用隧道，现场断网则用录屏 + 本地上传页兜底。

**卡片 JSON 最小示例**
```json
{"config":{"wide_screen_mode":true},
 "header":{"template":"red","title":{"tag":"plain_text","content":"P0 · 高风险素材 | 3 条信号"}},
 "elements":[
  {"tag":"div","text":{"tag":"lark_md","content":"**商标标识** · Nike（0.82）\n参照物：USPTO 1234567\n位置：00:03.2 bbox(0.71,0.12,0.88,0.30)"}},
  {"tag":"action","actions":[{"tag":"button","type":"primary","text":{"tag":"plain_text","content":"查看证据链"},"url":"https://HOST/review/ID?token=T"}]},
  {"tag":"note","elements":[{"tag":"plain_text","content":"本结果为风险提示，不构成法律意见。"}]}]}
```

## 13. Web 复核页

- 路由：`GET /` 上传；`GET /review/{finding_id}?token=`；`POST /api/review/{id}/action`；`GET /api/jobs/{id}`。
- 图片：bbox 覆盖层（归一化坐标 × 渲染尺寸）；视频：`<video>` + 时间戳按钮（点击 seek）；信号列表按等级排序，每条展开证据（链接、条款原文、授权状态）。
- 动作：确认 / 驳回（理由必填，进 `ReviewAction`，供阈值回流）。
- 鉴权：HMAC 签名 URL；不引入用户体系。

## 14. 异步、限流、成本与清理

- 队列：`Job` 表 + worker 循环，`asyncio.Semaphore(4)`；同一素材串行，跨素材并行。
- 预算：单素材 ≤$0.25（env 可调）；超限跳过可选工具并在卡片注明「预算受限」；图搜帧数 ≤12 硬上限。
- 成本记账：每次外部调用写 `ToolCall(cost_usd)`；`GET /admin/cost` 汇总。
- TTL：素材 24h 删除；`cleanup.py` 每小时跑；测试必须验证删除。人脸结果不落盘（见 8.4）。
- 日志：JSONL，字段 `request_id/job_id/tool/latency/ok`；**不得写素材内容或人脸数据**。

## 15. 评测

- 数据集 `fixtures/eval.jsonl`：`{id, path, clean, truth:[{category, ref, bbox, t_start, t_end, level}]}`；40–60 条，干净素材 15–20 条（含 AI 人像、字体、普通人）。
- 匹配规则：类别一致，且（bbox IoU ≥0.3 或时间重叠 ≥50% 或 ref 相同）。
- 指标：整体 P0/P1 precision/recall；干净集 FP 率；p50/p90 延迟；单素材成本；消融四组对比。
- 样本量纪律：每类 n<15 时只报计数与案例，不报 precision/recall 小数；报告注明局限（Wilson 区间）。
- 输出 `eval_report.md` + JSON；`make eval` 一键跑（默认走缓存）。

## 16. 演示模式

- `DEMO_MODE=1`：所有工具优先读 `fixtures/cache/{tool}/{hash}.json`；缓存缺失才真实调用。
- `GET /demo/replay/{id}`：加载预置完整结果；卡片/复核页直接可用。
- 五个场景（对应路演）：高风险视频全链路 → 证据链与授权到期 → 替换后重检通过 → 干净 AI 人像素材仅 P2 → 历史素材批量巡检。
- 现场只跑一条干净素材，其余用预缓存；全程录屏兜底。
- `make demo` 打印五个场景的 URL 与预期输出。

## 17. 分阶段计划与验收（10 天）

| Phase | 内容 | 验收命令 |
| --- | --- | --- |
| 0 (D1) | 骨架、配置、DB、上传、假卡片 | `make p0`：上传图片 5s 内出 fixture 卡片 |
| 1 (D2) | 素材层 + 抽取层（分镜/帧/OCR/ASR/pHash） | `make p1`：15s 视频输出帧、文本、转写 JSON |
| 2 (D3–4) | 图搜 + BGM + 名人（mock 优先，再接真） | `make p2`：植入素材命中预期信号 |
| 3 (D5) | logo 库 + 商标词表 + 政策 RAG | `make p3`：三个模块单测全绿 |
| 4 (D6) | 授权台账 + 复核页 | `make p4`：三份授权书状态正确，复核动作落库 |
| 5 (D7) | 飞书收发 + 卡片 + 多维表格（可选） | `make p5`：测试租户 round-trip |
| 6 (D8) | Verifier 二次检索 + 评测 + 消融 | `make eval`：生成报告 |
| 7 (D9) | 演示模式、缓存、兜底、清理任务 | `make demo` 离线可复现 |
| 8 (D10) | 彩排、录屏、修误报 | 全链路 p50 达标 |

每次 Phase 结束：更新 `PROGRESS.md`（完成项/未完成/阻塞/下一步），跑 `make test`。

## 18. 工程质量

- ruff + pytest；核心逻辑（rubric、matcher、状态机、去重、签名）必须有单测。
- `.env.example` 只列键名；密钥不进仓库；演示缓存由脚本生成并提交（不含密钥）。
- 所有外部适配器先写 mock（固定 JSON），再接真实 API；真实实现不得改变业务接口。
- README 写清：启动、配置、演示、评测、已知限制。
- 代码注释只写「为什么」与约束，不写逐行说明。

## 19. 明确禁止（踩了就是事故）

1. 不实现 USPTO/EUIPO 全文检索，不爬 `tmsearch`（无官方 API，WAF 会封，评委会问来源）。
2. 不使用 Google Cloud 名人识别（2025-09 已关停）；公众人物用 AWS Rekognition。
3. 不调用不存在的 WhatTheFont API；需要字体用 WhatFontIs 或不做。
4. 不让 LLM 单独产出 P0/P1；不让 LLM 输出「侵权」结论。
5. 不持久化人脸图像/特征；日志不写素材内容。
6. 不把 C2PA/检测器概率当证据；最高 P2。
7. 演示主路径不依赖 LLM 自主调度；Planner 用规则表。
8. 不把「未知 BGM」直接判 P0/P1。
9. 单素材外部调用不得超预算；图搜帧数不得超 12。
10. 不把素材发往无合规说明的第三方；外发点必须集中在 `tools/` 并留痕。

## 20. 完成定义（DoD）

- 干净素材集 P0/P1 误报 = 0（或可解释并记录）；
- 植入视频能命中 logo + BGM + 商标词，每条带证据与定位；
- 授权台账三态（有效/过期/地域不符）演示成立；
- 复核页可确认/驳回并落库；飞书测试租户 round-trip 成功；
- `make demo` 断网可复现五个场景；`make eval` 产出报告与消融表；
- 延迟：图片 p50 < 20s，15s 视频 p50 < 60s；
- 读一遍对外文案：无「侵权判定」措辞，免责声明齐全。
