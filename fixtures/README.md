# 演示与测试素材

| 文件 | 来源 | 规格 | 用途 |
| --- | --- | --- | --- |
| `sample_image.png` | 合成：`scripts/make_fixture_image.py` | 640×360 PNG | Phase 0 验收：上传 → fixture 卡片 |
| `sample_15s.mp4` | 合成：`scripts/make_fixture_video.py`（Pillow 画面 + Windows SAPI 口播） | 15s、1280×720、H.264/AAC、3 个分镜、中英文画面文字、英文口播 | Phase 1 验收：分镜 / 关键帧 / OCR / ASR |
| `clean_ai.png` | 用户提供；豆包 AI 生成 | 384×384 PNG，右下角含水印「豆包AI生成」 | 干净 AI 人像负例（P0/P1 误报=0 的评测样本）；OCR 可读出水印，后续可作为 AI 标注义务线索 |

## 未入库素材

`hero.mp4`（用户提供，13.1s 竖版视频，540×960，HEVC/AAC）画面为真实普通人的自拍
（非广告素材），音轨为一句中文，OCR 未识别到品牌文字。含真实个人面部，按用户决定
**仅本地使用**，已在 `.gitignore` 中排除；仅作为本地「普通人像零误报」负例。
