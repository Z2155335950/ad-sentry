# 技术决策记录

按时间倒序，「决策 / 原因 / 影响」。与 PROMPT.md 冲突时以 PROMPT 为准。

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
