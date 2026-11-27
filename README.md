# 素材哨兵 AdSentry

**把广告素材丢进飞书，60 秒内拿回一张「上线前风险提示」卡片。**

设计师或投放同学把图片、短视频发到飞书群，机器人自动排查**版权图像、名人肖像、背景音乐、商标、平台政策**五类风险，回一张分级卡片（P0–P3），点进去可以看证据、复核、驳回。

> 它只给**风险信号和技术证据**，不做「是否侵权」的法律判定。所有 P0/P1 结论都必须带上可点开的参照物（原图页面、政策条款、授权文件）。

---

## 它解决什么问题

出海投放最怕的不是被拒审，而是**上线后才发现素材有问题**：用了图库里的图、撞了名人肖像、BGM 没授权、画面里闪过大牌 logo。

人工排查的难点是「查不全」和「说不清」——现在的做法基本靠经验。AdSentry 把这一步变成一条可复现的流水线，每个结论都附证据链，方便直接拿去和法务、供应商对账。

## 怎么用

1. **发给机器人**：在飞书群里发一张图或一段视频（也可以直接用 Web 页面上传）
2. **收到回执**：机器人回复「已接收，预计 45 秒内返回风险卡片」
3. **拿到卡片**：按 P0（高）到 P3（提示）分级列出信号，每条附证据
4. **进复核页**：点「查看证据链」打开网页，看关键帧、原文、匹配来源，做人工确认

## 主要能力

| 能力 | 说明 |
| --- | --- |
| 图片 / 视频分析 | 视频自动抽帧、切片、语音转写，逐帧过检测 |
| 版权图像 | 反向图搜，命中图库页面即报，附原页面链接 |
| 名人肖像 | 人脸检测 + 名人库比对，只记录姓名与置信度，不存人脸图 |
| 背景音乐 | 音频指纹识别曲目，提示核对 BGM 授权 |
| 商标与 logo | 视觉、向量、文字三路投票，减少误报 |
| 平台政策 | 检索 TikTok / Meta 条款原文，提示需要标注或整改的点 |
| 授权台账 | 上传授权书自动识别有效期、地域、媒介，过期或超范围自动升级风险 |
| 双层复核 | 置信度中等的结论会自动放大裁剪后复查一次 |

## 技术栈

| 层 | 用了什么 |
| --- | --- |
| 语言 / 运行时 | Python 3.11+ |
| Web 后端 | FastAPI、Uvicorn、Jinja2 服务端渲染 |
| 数据 | SQLModel + SQLite（WAL）+ Pydantic Settings |
| 前端 | 服务端渲染 HTML + 原生 CSS/JS，无前端框架 |
| 音视频 | FFmpeg、OpenCV、PySceneDetect（分镜）、Pillow |
| 语音转写 | faster-whisper（本地 CPU 推理） |
| OCR | PaddleOCR / RapidOCR（ONNX，同一套 PP-OCR 模型） |
| 检索与匹配 | rank-bm25（政策条款检索）、rapidfuzz（商标模糊匹配）、imagehash（pHash 去重） |
| 文档解析 | PyMuPDF（授权书 PDF 文本层） |
| 视觉与音频模型 | Google Cloud Vision（反向图搜 / logo）、AWS Rekognition（公众人物）、ACRCloud（BGM 指纹）、open_clip（logo 向量，可选） |
| LLM | Anthropic Claude（可选，仅用于授权书字段补全） |
| 机器人 | lark-oapi（飞书长连接 WebSocket） |
| 工程 | pytest、ruff、Docker / Docker Compose、Make |

外部 AI 服务都是**可替换、可 mock** 的：默认 `TOOLS_MODE=mock` 读本地缓存，不联网也能跑通全流程；凭证齐全后切 `auto` 才走真实接口。

## 快速开始

需要 Python 3.11+。

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pip install -e ".[feishu]"   # 可选：接飞书机器人
```

生成演示素材（一次性）：

```bash
python scripts/make_fixture_image.py
python scripts/make_fixture_video.py
python scripts/make_p2_fixtures.py
```

启动服务：

```bash
python -m uvicorn app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/>，上传 `fixtures/media/risk_ad_10s.mp4`，就能看到 P0/P1/P2 三类信号。

想先看效果、不想跑流水线的话，用 `make demo` 离线回放五个预置场景。

## 接入飞书机器人

飞书侧需要一次性配置（缺一项都收不到消息）：

1. 权限：`im:resource`、`im:message`、`im:message:send_as_bot`
2. 事件订阅方式选「长连接」，订阅 `im.message.receive_v1`
3. 开启机器人能力，创建版本并发布
4. 把机器人拉进测试群

本地 `.env` 里填好 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`，并把 `FEISHU_ENABLED=true`。

长连接模式**不需要公网地址**；如果改用公网回调（`FEISHU_LONG_CONNECTION=false`），则需要 ngrok 之类的隧道。

## 配置

所有配置项见 [.env.example](.env.example)，复制成 `.env` 后按需填写。密钥只放 `.env`，不会进仓库。

几个关键开关：

| 变量 | 作用 |
| --- | --- |
| `TOOLS_MODE` | `mock`（默认，离线可跑）/ `auto`（有凭证就走真实接口）/ `live` / `off` |
| `DEMO_MODE` | 打开后工具优先读演示缓存，适合现场演示 |
| `ASR_MODEL_SIZE` | 语音识别模型大小，默认 `small`，首次运行会下载 |
| `BUDGET_PER_ASSET_USD` | 单素材外部调用预算上限，默认 0.25 美元 |

## 部署

```bash
docker compose up --build
```

app 与 worker 拆成两个容器，共用 `./data` 目录。目前只支持单机 SQLite。

## 已知限制

- **工具层默认是 mock**：不接真实凭证也能跑通全流程，但结论来自预置数据。Google Vision、AWS Rekognition 等接口尚未用真实凭证验证。
- **商标词表是演示子集**（56 条），政策条款是摘录，对外引用前需核对官方原文。
- **授权书识别针对固定模板**，自由格式、手写、盖章文件需要人工补录。
- **评测集只有 11 条**，指标只作参考，不是严谨的基准。
- 完整的问题列表和待办见 [PROGRESS.md](PROGRESS.md)。

## 更多文档

- [docs/TECHNICAL.md](docs/TECHNICAL.md) — 抽取层、工具层、评测、演示模式的技术细节
- [PROGRESS.md](PROGRESS.md) — 各阶段进展与未完成项
- [DECISIONS.md](DECISIONS.md) — 关键取舍记录

## 免责声明

本工具输出为基于公开信息的风险提示与技术证据，不构成法律意见；上线前请结合授权文件与法务意见自行判断。
