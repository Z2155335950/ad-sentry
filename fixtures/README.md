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

凭证齐全后用 `TOOLS_MODE=auto` 切换真实调用；mock 文件缺失时工具返回空，不编造信号。

## 未入库素材

`hero.mp4`（用户提供，13.1s 竖版视频，540×960，HEVC/AAC）画面为真实普通人的自拍
（非广告素材），音轨为一句中文，OCR 未识别到品牌文字。含真实个人面部，按用户决定
**仅本地使用**，已在 `.gitignore` 中排除；仅作为本地「普通人像零误报」负例。
