# 技术决策记录

按时间倒序，「决策 / 原因 / 影响」。与 PROMPT.md 冲突时以 PROMPT 为准。

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
