# PROGRESS

> 每个 Phase 结束时更新：完成项 / 未完成 / 阻塞 / 下一步。冲突时以 PROMPT.md 为准。

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

**下一步**：Phase 1 —— 素材层 + 抽取层（分镜/关键帧/OCR/ASR/pHash），
验收 `make p1`：15s 视频输出帧、文本、转写 JSON。
