"""生成 Phase 3 的 logo mock fixture。

`sample_15s.mp4` 第 2 镜（5–10s）画面里有「NIKE」文字，OCR 能读到；
本脚本按素材 sha256 + 分镜索引写入 Vision logo 检测与 CLIP 向量库的 mock 命中，
让三路投票在离线验收时可复现。真实调用在 `TOOLS_MODE=auto` 且凭证齐全时走 live。

运行：python scripts/make_p3_fixtures.py [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / "fixtures" / "media" / "sample_15s.mp4"
CACHE = ROOT / "fixtures" / "cache"

# NIKE 副标题居中，归一化坐标来自 1280x720 画布上的文字框
NIKE_BBOX = [0.44, 0.58, 0.56, 0.67]


def write_logo_mock(sha256: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "note": (
            "Phase 3 mock：sample_15s.mp4 第 2 镜的 NIKE 文字；"
            "Vision 与 CLIP 两路模拟命中 + OCR 真实命中构成三路证据。"
        ),
        "assets": {
            sha256: {
                "scenes": {
                    "1": {
                        "provider": "google_vision_logo(mock)",
                        "logos": [
                            {"brand": "Nike", "score": 0.93, "bbox": NIKE_BBOX}
                        ],
                        "clip": [{"brand": "Nike", "score": 0.88}],
                    }
                }
            }
        },
    }
    path = CACHE / "logo.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="覆盖已有 mock")
    args = parser.parse_args()

    if not VIDEO.exists():
        raise SystemExit(f"缺少 {VIDEO}，先运行 scripts/make_fixture_video.py")
    path = CACHE / "logo.json"
    if path.exists() and not args.force:
        existing = json.loads(path.read_text(encoding="utf-8"))
        sha256 = hashlib.sha256(VIDEO.read_bytes()).hexdigest()
        if sha256 in (existing.get("assets") or {}):
            print(f"已存在：{path}（--force 覆盖）")
            return

    sha256 = hashlib.sha256(VIDEO.read_bytes()).hexdigest()
    written = write_logo_mock(sha256)
    print(f"written {written}")
    print(f"asset sha256: {sha256}")
    print("预期：NIKE logo P0（Vision/CLIP/OCR 三路）、商标词 P1（注册号 6124779）")


if __name__ == "__main__":
    main()
