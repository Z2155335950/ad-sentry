# 演示与测试素材

| 文件 | 来源 | 规格 | 用途 |
| --- | --- | --- | --- |
| `sample_image.png` | 合成：`scripts/make_fixture_image.py` | 640×360 PNG | Phase 0 验收：上传 → fixture 卡片 |
| `sample_15s.mp4` | 合成：`scripts/make_fixture_video.py`（Pillow 画面 + Windows SAPI 口播） | 15s、1280×720、H.264/AAC、3 个分镜、中英文画面文字、英文口播 | Phase 1 验收：分镜 / 关键帧 / OCR / ASR |
| `clean_ai.png` | 用户提供；豆包 AI 生成 | 384×384 PNG，右下角含水印「豆包AI生成」 | 干净 AI 人像负例（P0/P1 误报=0 的评测样本）；OCR 可读出水印，后续可作为 AI 标注义务线索 |
| `risk_ad_10s.mp4` | 合成：`scripts/make_p2_fixtures.py`（Pillow 画面 + numpy 合成旋律） | 10s、960×540、H.264/AAC、3 个分镜 | Phase 2 验收：植入「图库照片 / 合成人像 / 示例曲目」，命中版权图像 P0、公众人物 P1、BGM P2 |

## Mock fixture（`fixtures/cache/`）

Phase 2 的外部工具默认走 mock，按**素材 sha256 + 分镜/窗口序号**键控（避免抽帧编码差异
导致内容哈希漂移），由 `scripts/make_p2_fixtures.py` 生成并提交：

| 文件 | 工具 | 内容 |
| --- | --- | --- |
| `image_search.json` | Google Vision WEB_DETECTION | 分镜 0：图库 full match + 博客 visually similar |
| `celebrity.json` | AWS Rekognition | 分镜 1：示例公众人物（合成人像，不指向真人） |
| `music.json` | ACRCloud | 窗口 0：示例曲目；窗口 1–2：无匹配 |
| `logo.json` | Google Vision LOGO_DETECTION + CLIP | `sample_15s.mp4` 分镜 1：NIKE 文字（Vision + CLIP 两路 mock，OCR 为真实命中） |

Phase 3 的 `logo.json` 由 `scripts/make_p3_fixtures.py` 生成（`make p3` 会自动补齐）。
商标词表与政策条款是静态数据，位于 `data/watchlists/`，不按素材键控。

## 授权书样本（`fixtures/licenses/`）

三份**合成**授权书（PDF，含中文字体子集），由 `scripts/make_p4_fixtures.py` 生成，
不对应任何真实授权关系：

| 文件 | 覆盖场景 | 关键字段 |
| --- | --- | --- |
| `license_expired.pdf` | 授权已过期 | 到期 2025-06-30；地域 美国、欧盟；媒介 TikTok、Meta |
| `license_sea_excluded.pdf` | 地域不含东南亚 | 到期 2027-12-31；地域 美国、欧盟、英国 |
| `license_valid.pdf` | 授权有效 | 到期 2027-12-31；地域 东南亚、美国 |

`make p4` 会上传三份文件、登记 TikTok / SEA 投放上下文、挂接素材，断言状态分别为
`expired` / `out_of_territory` / `effective`。

凭证齐全后用 `TOOLS_MODE=auto` 切换真实调用；mock 文件缺失时工具返回空，不编造信号。

## 演示回放（Phase 7）

`fixtures/demo/scenarios.json` 保存五个路演场景的完整结果（findings / 证据 / Campaign /
授权记录）：风险视频全链路、授权到期、替换后复检、干净 AI 人像、历史批量巡检。
`/demo/replay/{id}` 按素材 sha256 幂等写入，不调用外部工具。

`fixtures/cache/<tool>/<sha256>.json` 是 `DEMO_MODE=true` 的按素材缓存
（`image_search` / `celebrity` / `music` / `logo`），由 `scripts/make_demo_cache.py`
从 flat mock 生成；演示缓存缺失时才回落 `TOOLS_MODE`。新增演示素材后需重跑该脚本。

## 未入库素材

`hero.mp4`（用户提供，13.1s 竖版视频，540×960，HEVC/AAC）画面为真实普通人的自拍
（非广告素材），音轨为一句中文，OCR 未识别到品牌文字。含真实个人面部，按用户决定
**仅本地使用**，已在 `.gitignore` 中排除；仅作为本地「普通人像零误报」负例。
