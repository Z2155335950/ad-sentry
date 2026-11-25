# 技术决策记录

按时间倒序，「决策 / 原因 / 影响」。与 PROMPT.md 冲突时以 PROMPT 为准。

## D045 演示回放用静态 scenarios.json 幂等写入

- 决策：`fixtures/demo/scenarios.json` 保存五个场景的完整结果；`/demo/replay/{id}`
  按素材 sha256、`Job.stage=demo:<id>`、Finding `dedupe_key` 幂等落库，重复打开不新增记录。
- 原因：现场演示要求断网、可复现、可反复点开；实时跑工具会引入网络、模型加载与成本不确定性。
- 影响：回放结果与真实上传写同一套表，复核页、授权面板、卡片逻辑完全复用；演示素材
  `expires_at=None`，不会被 TTL 清理。修改演示内容只改 JSON 与 `scripts/demo_check.py`。

## D044 演示缓存按素材键控，flat mock 保留

- 决策：`DEMO_MODE=true` 时 `base.effective_mode` 优先命中
  `fixtures/cache/<tool>/<sha256>.json`（单文件内含 `scenes` / `windows`），缺失才回落到
  `TOOLS_MODE` 的 mock / live；flat 的 `fixtures/cache/<tool>.json` 继续服务单测与 `make pN`。
- 原因：PROMPT §16 指定按 `{tool}/{hash}` 组织演示缓存；保留 flat mock 可以避免重写已有测试
  与 Phase 2/3 验收脚本。
- 影响：`scripts/make_demo_cache.py` 从 flat mock 生成按素材文件；新增演示素材时两步都要更新。

## D043 TTL 清理使用显式 DELETE 顺序

- 决策：`cleanup_expired` 不依赖 ORM relationship，按 ReviewAction → Evidence → Finding →
  Detection → ToolCall → FeishuEvent → Job → AssetLicense → Campaign → Asset 的顺序
  执行 `DELETE`，每个素材提交一次。
- 原因：模型只有外键、没有 relationship，SQLAlchemy 工作单元不保证删除顺序；开启
  `PRAGMA foreign_keys=ON` 后先删 asset 会触发 FOREIGN KEY constraint failed（本次验收实际踩到）。
- 影响：新增引用 Asset / Finding / Job 的表时，必须把 DELETE 加到对应顺序之前，并补
  `tests/test_cleanup.py` 的级联断言。

## D042 双进程部署用 WORKER_IN_APP 控制

- 决策：默认 `WORKER_IN_APP=true`，`uvicorn app.main:app` 在 lifespan 内同时跑 worker 与清理；
  `docker-compose.yml` 的 app 服务设 `WORKER_IN_APP=false`，由 `python -m app.workers.run`
  单独跑 worker，避免两个进程同时认领任务。
- 原因：PROMPT §4 要求 compose 拆 app + worker；单机开发又需要 `make run` 一条命令可用。
- 影响：换多 worker 前需把 `claim_next_job` 改为条件更新；清理循环与 worker 同生命周期。

## D041 素材 TTL 改为配置项

- 决策：`ASSET_TTL_HOURS`（默认 24）决定上传素材的 `expires_at`；
  `CLEANUP_INTERVAL_SECONDS`（默认 3600）决定清理频率。
- 原因：演示与测试需要缩短 TTL 验证删除；生产默认保持 PROMPT §14 的 24 小时。
- 影响：`ingest.asset_ttl()` 是唯一计算点；清理只处理 `DATA_DIR` 下的文件，
  `fixtures/` 演示素材永不删除。

## D040 评测集控制在 12 条以内

- 决策：`fixtures/eval.jsonl` 交付 11 条（2 条植入 + 9 条干净），不再扩充到 PROMPT §15
  的 40–60 条；报告只给计数与 Wilson 区间，不给稳定的 precision/recall 小数。
- 原因：PROMPT 顶部冲刺模式明确「评测集 >12 条」属禁止项，且正文的样本量纪律要求
  n<15 时只报计数；造 40 条没有标注质量的样本反而会削弱可信度。
- 影响：以后扩充评测集时同步改 `scripts/make_eval_set.py` 的天花板与报告口径。

## D039 Verifier 无 bbox 时退化为整帧重搜

- 决策：copyright_image 信号通常没有 bbox（图搜结果只有参照物 URL），Verifier 用
  `0,0,1,1` 作为裁剪框，即整帧 2× 放大重搜。
- 原因：PROMPT §10 的二次检索核心是「放大重搜」，bbox 只是定位手段；退化为整帧
  仍能触发一次独立检索，且不会因为缺 bbox 直接跳过这条低置信信号。
- 影响：新增工具若返回 bbox，Verifier 自动走局部裁剪；没有 bbox 时调用量不变。

## D038 纯 VLM 基线只由评测脚本消费

- 决策：`BASELINE_VLM_ONLY` 与 `app/agent/vlm_baseline.py` 只被 `app/eval/run_eval.py`
  使用；生产流水线（worker / detect）不 import 该模块。
- 原因：PROMPT §10 要求「纯 VLM 基线仅用于评测对比，绝不进生产路径」。把开关放在
  detect 里等于给生产留了一个误开的入口。
- 影响：基线数据来自 `fixtures/cache/vlm_baseline.json`，报告必须标注为缓存基线。

## D037 mock 与线上统一用 `similar`

- 决策：`websearch.assess` 把 `similar` 与 `visually_similar` 都按弱命中处理；
  新写的 mock fixture 统一用 `similar`。
- 原因：`normalize_vision_response` 输出 `similar`，而 Phase 2 的 mock 手写成了
  `visually_similar`；`assess` 只判断 `!= "similar"`，导致「仅视觉相似」被当成
  强命中出 P1，Phase 6 评测里表现为干净样本误报。
- 影响：只影响弱命中判定；full/partial/stock 的定级路径不变，Phase 2/3 验收回归通过。



## D036 单测屏蔽 .env 真实凭证

- 决策：`tests/conftest.py` 在导入 `app` 之前把所有第三方凭证环境变量置空，并固定
  `TOOLS_MODE=mock`、`FEISHU_MODE=mock`。
- 原因：ACRCloud / 飞书凭证写进 `.env` 后，单测读到真实 `FEISHU_ENCRYPT_KEY` 会触发签名校验，
  出现本机第一次跑就 403 的现象；测试结果不能依赖开发机的 `.env`。
- 影响：新增外部服务时，把它的凭证键加进 conftest 的屏蔽列表；端到端脚本（`scripts/pN_check.py`）
  自己显式设置所需 env，不受影响。

## D035 FeishuEvent 承担幂等与回推

- 决策：新增 `FeishuEvent(event_id, message_id 唯一, chat_id, job_id, status)`，
  **不**给 `Job` 加来源字段。
- 原因：PROMPT §6 锁定了 Job 结构；而幂等去重必须落库（进程重启后仍有效），
  回推卡片也需要知道任务来自哪个会话。
- 影响：状态机 `received → queued → processed | ignored | failed | notify_failed`；
  清理任务（Phase 7）可与素材一起按 TTL 删除。

## D034 `FEISHU_MODE=mock` 写本地 outbox

- 决策：mock 模式下发送消息、写多维表格都追加到 `DATA_DIR/feishu_outbox.jsonl`，
  下载资源返回 `fixtures/media/sample_image.png`。
- 原因：`make p5` 必须离线可跑；把「发出去的东西」落成文件，验收脚本可以断言回执、
  卡片标题、复核链接，而不用连测试租户。
- 影响：outbox 是验收产物不是业务数据，随 `data/*` 被 gitignore；真实模式不写该文件。

## D033 长连接与公网回调共用一条处理路径

- 决策：`longconn.py` 把 SDK 事件对象转成 v2.0 JSON，交给 `events.handle_event`；
  `/api/feishu/event` 也调用同一个函数。
- 原因：两种接入方式的业务行为必须一致，否则演示用长连接、线上用回调会漂移。
- 影响：新增事件类型时只改 `events.parse_event` / `handle_event`，两个入口自动生效。

## D032 复核链接必须显式配置 `PUBLIC_BASE_URL`

- 决策：卡片里的复核链接用 `PUBLIC_BASE_URL` 拼接，不再从请求头推导。
- 原因：长连接模式没有 HTTP 请求，拿不到 `request.base_url`；跨进程推送也需要固定域名。
- 影响：演示前把 `PUBLIC_BASE_URL` 改成评委可访问的地址，否则卡片按钮会指向本机。

## D031 ACRCloud 请求必须带 sample_bytes

- 决策：`_live_identify` 把窗口文件读进内存，同时提交 `sample` 文件与 `sample_bytes`（文件字节数），
  再做 HMAC-SHA1 签名。
- 原因：2026-11-25 用真实凭证首次调用返回 `3006 invalid arguments`——ACRCloud Identify 的
  multipart 表单要求 `sample_bytes`，缺了它签名校验通过但参数校验失败。
- 影响：签名串不包含 `sample_bytes`（仍是 `POST /v1/identify <key> audio 1 <ts>`），
  新增参数不要误加进签名；回归测试 `test_live_request_sends_sample_bytes` 锁定请求形状。

## D030 授权挂接只升不降

- 决策：挂接授权记录后，若状态为 expired / out_of_territory / out_of_media / out_of_scope，
  把 P2/P3 的信号抬到 P1；状态为 effective / expiring_soon 时只补 `license` 证据，不降级。
- 原因：反向图搜、BGM、名人识别各自的参照物证据独立成立；授权有效只说明「这一路不再构成
  授权问题」，不能抹掉其他工具已经发现的信号。自动降级会让 P0 证据链静默消失。
- 影响：要降低等级只能人工在复核页驳回；`license` 证据按 `ref_record_id=license:{id}` 幂等。

## D029 无 Campaign 不判地域与媒介

- 决策：没有 Campaign 时状态只按日期给出，并在结果里带 `campaign_missing=True` 和
  「缺少投放信息，地域与媒介无法核验」；复核页显式提示补充 platform / market。
- 原因：PROMPT §9 要求缺投放上下文时输出「无法核验」，不得默认通过或拒绝。地域列表可以
  离线归一（SEA ≈ 东南亚 / 新加坡），但市场本身必须由投放方给出。
- 影响：登记 Campaign 后 `apply_to_asset` 立即重算并回写状态；市场无法识别时保守记为
  「需人工核验」，不当作不符。

## D028 授权书抽取默认走规则，LLM 只补空字段

- 决策：`app/ledger/extract.py` 用标签行正则解析固定模板，PDF 文本层置信 0.95、OCR 0.6；
  `LEDGER_LLM_ENABLED=true` 且装了 `.[llm]` 时用 `messages.parse` 补空字段，失败静默回退。
- 原因：演示与验收必须离线可复现；扫描件的 OCR 置信不足以判定状态，正好触发
  `needs_review` 的人工录入路径，比让模型编字段更可信。
- 影响：自由格式 / 手写 / 印章授权书需要 LLM 或人工补录；新增模板只需扩 `LABEL_MAP`。

## D027 状态机优先级固定

- 决策：needs_review > out_of_scope > expired > out_of_territory > out_of_media >
  expiring_soon > effective；关键字段 = 授权类型 / 地域 / 起止日期，置信阈值 0.7。
- 原因：不可判定优先于可判定；已过期比地域不符更根本；范围不符说明记录与素材类别无关，
  应先于日期判断，避免把不适用的授权拿来挡风险。
- 影响：新增状态需要同时更新 `StatusResult.is_risk` 与优先级测试
  （`tests/test_ledger_status.py`）。

## D026 授权文件落盘位置与大小上限

- 决策：上传的授权文件写 `DATA_DIR/licenses/<sha256><ext>`，单文件上限 20MB，扩展名白名单
  `.pdf/.docx/.png/.jpg/.jpeg/.webp/.bmp`；原始文件不随仓库分发。
- 原因：授权书含商业条款，属于客户数据；素材 TTL 清理（Phase 7）要一并覆盖该目录。
- 影响：演示素材 `fixtures/licenses/` 是脚本生成的合成文件，可以入库；真实授权书走
  `DATA_DIR`，不入库。



## D025 政策 RAG 只做提示级，阈值可调

- 决策：BM25Plus 检索 + `POLICY_MIN_SCORE=8.0`；命中只产出 P3，并强制附 `clause_id`、
  原文摘录与 `effective_date`；无达标条款不产出。
- 原因：没有 Campaign（平台 / 地域 / 品类）时无法判断政策后果严重度，
  PROMPT §11 要求缺投放上下文不得猜；P3 只做「请对照条款」的提示。
- 影响：Campaign 接入（Phase 4）后可把政策信号升级为 P2/P1；阈值与 top-k 通过 env 调整，
  修改语料后需重跑 `pytest tests/test_policy.py` 校准。

## D024 logo 三路投票：OCR 只作佐证

- 决策：logo 信号至少需要 Vision / CLIP / VLM 中一路命中；OCR 文本命中只累加票数，
  单独命中交给商标词模块（`trademark_text`）。≥2 路且有注册信息 → P0；
  ≥2 路无注册 → P1；单路或词表外品牌 → P2。
- 原因：PROMPT §8.3 要求「至少两路一致才 ≥P1」「库内必须有注册信息才允许 P0」；
  把 OCR 当 logo 会与商标词重复计分。
- 影响：open_clip 未安装时，demo 用 mock 提供 CLIP 路线；真实环境需品牌 logo 图与
  `logo_vectors.npz` 才能启用向量路。

## D023 商标词短词只精确匹配

- 决策：拉丁词按词边界精确匹配；只有长度 ≥ `TRADEMARK_FUZZY_MIN_LEN`（默认 6）才允许
  1 字符差异；中文别名 ≥2 字精确包含。
- 原因：PROMPT §8.6 允许模糊匹配，同时验收要求「bike 不能命中 Nike」——
  四字母短词做 1-edit 模糊必然误报，所以对短词收紧。
- 影响：调低该阈值会直接引入 Nike/bike 类误报；新增短品牌名列时默认只做精确匹配。

## D022 词表先交付可解释的演示子集

- 决策：`data/watchlists/brands.json` 先交付 56 条（含 4 个通用词），字段与 PROMPT §8.6
  一致；仅 Nike 的 `reg_no` 经公开聚合站核对，其余留空（只能 P2）。
- 原因：10 天窗口内无法逐条核验 ≥500 条注册信息；编造注册号会破坏证据链的可信度。
- 影响：正式导入核验词表后逻辑不变；`reg_no=null` 的品牌会在卡片上注明「无注册信息」。

## D021 Evidence.bbox 统一存字符串

- 决策：OCR / Vision 产生的列表型 bbox 在写证据前统一转成 `x1,y1,x2,y2` 字符串；
  归一化坐标保留 4 位小数。
- 原因：`Evidence.bbox` 在数据模型里是字符串列，Phase 2 的名人 bbox 已是字符串；
  混用列表会让写入 SQLite 时报 `type 'list' is not supported`（本次验收实际踩到）。
- 影响：新增工具一律调用 `trademark.format_bbox`；复核页按逗号解析。

## D020 未识别 BGM 只在有明确 no_match 时产出

- 决策：live 模式下 ACRCloud `code=1001` 记为 `no_match`；只有所有窗口都明确 no_match
  且没有任何识别结果时，才产出「未知音乐」P2。mock fixture 缺失时工具返回 empty，不编造。
- 原因：§8.2 把「未知音乐」当作高价值信号，但必须与「mock 没数据」区分开，否则任何素材
  都会被塞一条 P2。
- 影响：新增 mock 素材时要么给窗口结果，要么保持空；不要用默认 no_match 兜底。

## D019 `fixtures/cache/` 入库

- 决策：移除 `.gitignore` 里的 `fixtures/cache/`；Phase 2 mock JSON 由脚本生成并提交。
- 原因：§16/§18 要求演示缓存可离线复现且不含密钥；忽略该目录会让 `make p2` 在新环境失效。
- 影响：重新生成 `risk_ad_10s.mp4` 会改变 sha256，需重跑 `make_p2_fixtures.py` 同步 mock。

## D018 名人信号的参照物与隐私边界

- 决策：celebrity P1 的锚点证据是 `kind=reference, ref_record_id=rekognition:<name>`
  （识别服务记录），同时附 bbox 与时间戳；live 只上传 Haar 检测到的人脸裁剪（外扩 20%），
  库内只落姓名 / 置信度 / bbox / 时间戳。
- 原因：§2 要求 P0/P1 至少一条参照物证据，§8.4 要求先裁人脸再外发且不落人脸数据。
- 影响：live 未检测到人脸时不调用、不计费；mock 不做人脸门控（合成人像无法保证被级联检出）。

## D017 工具层统一运行时

- 决策：`app/tools/base.py::ToolRuntime` 统一处理每 Job 预算（默认 `$0.25`）、熔断
  （60s 内 3 次失败 → 打开 60s）、live 结果 `ToolCache`、每次调用写 `ToolCall`；
  mock 模式不计费、不写缓存。
- 原因：§2/§7/§14 要求超时、熔断、成本记账与可 mock；收敛在一个入口避免各适配器重复实现。
- 影响：新增工具只实现「输入 → 归一化 dict」，预算/记账由 runtime 负责。

## D016 mock 按「资产 sha256 + 分镜/窗口序号」键控

- 决策：mock fixture 以素材 sha256 和分镜/窗口序号为键；live 路径仍按输入内容与时间戳生成
  `input_hash` 用于缓存。
- 原因：抽帧 JPEG 编码随 ffmpeg 版本变化，按帧内容哈希做 mock 会在换机器后全部 miss。
- 影响：mock 只对已生成 fixture 的素材生效；新素材需要重新生成或走真实工具。

## D015 工具层是确定性 DAG，不用 LLM 选工具

- 决策：`app/pipeline/detect.py::run` 固定执行 图搜 → 名人 → BGM，按媒体类型与预算跳过；
  工具选择不交给 LLM。
- 原因：§10 与 §19 明确禁止演示主路径依赖 LLM 自主调度，防止漏检。
- 影响：Phase 6 的 Verifier 只做低置信二次检索，不改变这里的工具集合。

## D014 抽取产物落盘，不新增数据表

- 决策：抽取结果写 `DATA_DIR/extracted/<sha256>/extraction.json`（关键帧同目录 `frames/`），
  DB 里只写一条 `Detection(tool="extract", raw=摘要)`；通过 `GET /api/jobs/{id}/extraction` 读取。
- 原因：PROMPT §6 锁定了数据模型；帧/OCR/转写是工具的输入而不是 Finding，放 JSON 便于
  工具层与评测复用，也避免大字段进 SQLite。
- 影响：TTL 清理（Phase 7）要同时删 `extracted/<sha256>/`；DEMO_MODE 缓存可复用这些文件。

## D013 `EXTRACT_ENABLED` 开关隔离阶段验收

- 决策：默认 true；`scripts/p0_check.py` 与 `tests/conftest.py` 设为 false。
- 原因：Phase 0 验收度量「上传 → 卡片」5 秒预算，OCR/ASR 首次模型加载会污染它；
  抽取链路由 `make p1` 与抽取层单测单独覆盖。
- 影响：新阶段新增端到端检查时，明确它验证的是哪段链路，不要互相借环境变量。

## D012 媒体/OCR/ASR 依赖的降级路径

- 决策：
  - ffmpeg：系统 PATH → `FFMPEG_BIN` → `imageio-ffmpeg` 自带二进制；ffprobe 缺失时解析
    `ffmpeg -i` 的 stderr 取元数据。
  - OCR：`OCR_ENGINE=auto` 先试 PaddleOCR，未安装则用 rapidocr-onnxruntime（同一套 PP-OCR
    模型），都不可用则 status=unavailable，不抛断流水线。
  - ASR：faster-whisper small / int8 / CPU；权重下载端点默认 `https://hf-mirror.com`
    （本机实测 huggingface.co 不可达），可用 `HF_ENDPOINT=` 清空改回官方。
- 原因：本机无系统 ffmpeg、paddlepaddle 安装体积大；验收必须在当前机器可复现。
  rapidocr 与 PaddleOCR 用的是同一份 PP-OCR 模型，只是推理框架不同。
- 影响：装好 PaddleOCR 后无需改代码，auto 模式自动优先它；OCR 结果结构差异收敛在
  `app/pipeline/ocr.py` 的归一化层，业务代码只消费 OcrLine。

## D011 分镜/关键帧策略

- 决策：PySceneDetect（ContentDetector 27 / min_scene_len 15）不可用时按时长均匀分段；
  每镜最多 3 帧、全片 ≤ `MAX_FRAMES`（默认 12）；镜内按 Laplacian 方差排序，场景间轮流取帧，
  pHash 汉明距离 ≤6 去重；帧图片统一 JPEG 存 `extracted/<sha>/frames/`。
- 原因：PROMPT §8.1 的帧预算与清晰度优先；轮流取帧避免长视频只覆盖开头。
- 影响：后续图搜/logo 检测直接消费 `frames[].path` 与 `phash`，不再重复抽帧。

## D010 阶段验收入口使用 `make verify-N`

- 决策：每个 Phase 增加 `verify-N` 目标（N 从 0 起），`verify-0` 委托给 PROMPT 规定的 `p0`。
- 原因：验收统一按 `make verify-{N}` / `pytest -q` / `git status --short` 三步执行；
  PROMPT 的 `make p0`…`make p8` 保留为底层目标。
- 影响：新增 Phase 时同时加 `pN` 与 `verify-N`；验收标准本身不变。

## D009 本机无 make 时的验收方式

- 决策：Makefile 照常维护；本机（Windows Git Bash，无 make）用等价 Python 命令执行验收。
- 原因：安装 make 是系统级改动，不阻塞开发。
- 影响：`make p0` ⇔ `python scripts/p0_check.py`；`make test` ⇔ `python -m pytest`。
- 更新（2026-11-25）：已安装 `ezwinports.make` 4.4.1；等价命令保留为 PATH 未刷新时的兜底。

## D008 卡片 JSON 采用飞书 card 1.0 结构

- 决策：`app/pipeline/report.py` 输出 `config/header/elements` 结构，与 PROMPT §12 示例一致。
- 原因：演示环境飞书版本兼容性最好；后续如迁移 card 2.0，只改 report.py。
- 影响：`build_card` 的输入是纯 dict，便于单测与演示回放。

## D007 复核链接鉴权：HMAC 签名，不引入用户体系

- 决策：token 形如 `<exp>.<hmac_sha256(secret, "finding_id:exp")>`，默认 24h 过期。
- 原因：PROMPT §13 明确最小鉴权；无用户体系。
- 影响：密钥 `APP_HMAC_SECRET` 只放 `.env`；换密钥会使旧链接失效。

## D006 证据红线在写库时执行

- 决策：`pipeline/fuse.py::cap_level_without_anchor` 在生成 Finding 时调用；无 reference/clause/license 证据的 P0/P1 直接降为 P2 并在建议里注明。
- 原因：让「模型单独判断不出 P0/P1」成为数据层事实，而不是展示层的修饰。
- 影响：后续真实工具接入时，所有 Finding 写入必须经过 fuse。

## D005 Phase 0 占位流水线

- 决策：worker 不调用任何外部工具，把 `fixtures/fake_findings.json` 写入 Detection/Finding/Evidence。
- 原因：先跑通「上传 → 任务 → 卡片 → 复核页」骨架，真实流水线在 Phase 1–4 逐步替换内部实现。
- 影响：`_apply_fixture` 是唯一占位点；替换时保持 Job/Finding/Evidence 接口不变。

## D004 Job 队列用 SQLite 轮询，单 worker

- 决策：`workers/queue.py` 轮询 `Job.status=queued`，`asyncio.to_thread` 执行处理函数。
- 原因：Phase 0 无并发需求；PROMPT §14 要求同一素材串行、跨素材并行，后续再加 `asyncio.Semaphore(4)`。
- 影响：多 worker 前需把认领改为条件更新（`UPDATE ... WHERE status='queued'`），代码中已留注释。

## D003 SQLite + WAL

- 决策：`connect_args={"check_same_thread": False}`，连接时 `journal_mode=WAL`、`foreign_keys=ON`。
- 原因：worker 线程与请求线程共享同一库文件。
- 影响：换 PostgreSQL 只需改 `DATABASE_URL`（SQLModel 层不依赖 SQLite 特性）。

## D002 FastAPI lifespan 内启动 worker

- 决策：`app/main.py` 的 lifespan 启动 `worker_loop`，关闭时取消。
- 原因：Phase 0 单进程跑通；Phase 7 拆成 `app + worker` 两个容器。
- 影响：`TestClient(app)` 也会拉起 worker，端到端测试无需额外进程。

## D001 目录结构与 PROMPT §5 对齐

- 决策：严格按 PROMPT §5 建目录；补充 `pyproject.toml`、`app/workers/run.py`（后续）、`fixtures/eval.jsonl`、`templates/base.html`。
- 原因：提示词声明目录「必须遵循」，补的是正文已引用但树里漏列的文件。
- 影响：新增模块先确认是否已有约定位置。
