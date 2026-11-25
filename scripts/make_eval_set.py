"""生成 Phase 6 评测集：fixtures/eval.jsonl + 干净样本图片 + VLM 基线缓存。

评测集控制在 12 条以内（冲刺模式的显式约束）：2 条植入素材 + 干净图片若干。
干净图片只含通用文案，用于检查误报；其中 `clean_bike.png` 专门对抗「bike 命中 Nike」。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "fixtures" / "eval"
EVAL_PATH = ROOT / "fixtures" / "eval.jsonl"
VLM_CACHE = ROOT / "fixtures" / "cache" / "vlm_baseline.json"

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

IMAGES = {
    "clean_sale.png": ["BIG SALE", "50% OFF TODAY"],
    "clean_bike.png": ["BIKE CITY TOUR", "GUIDED RIDES"],
    "clean_new.png": ["NEW ARRIVAL", "SHOP THE LOOK"],
    "clean_promo.png": ["LIMITED OFFER", "ORDER NOW"],
    "clean_plain.png": ["ENJOY THE SUMMER", "FIND YOUR STYLE"],
    "clean_blank.png": [],
    # 低置信候选：mock 只返回 visually_similar，用来触发 Verifier 二次检索
    "verify_prompt.png": ["VINTAGE POSTER", "RETRO STYLE"],
}


def _font(size: int):
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _make_image(path: Path, lines: list[str]) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (640, 360), (28, 32, 44))
    draw = ImageDraw.Draw(image)
    font = _font(54)
    total = len(lines) * 70
    y = (360 - total) // 2
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        width = box[2] - box[0]
        draw.text(((640 - width) // 2, y), line, font=font, fill=(240, 242, 248))
        y += 70
    image.save(path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    for name, lines in IMAGES.items():
        path = OUT_DIR / name
        _make_image(path, lines)
        entries.append(
            {
                "id": name.replace(".png", ""),
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "clean": True,
                "truth": [],
            }
        )

    sample_image = ROOT / "fixtures" / "media" / "sample_image.png"
    if sample_image.exists():
        entries.append(
            {
                "id": "sample_image",
                "path": "fixtures/media/sample_image.png",
                "clean": True,
                "truth": [],
            }
        )

    clean_ai = ROOT / "fixtures" / "media" / "clean_ai.png"
    if clean_ai.exists():
        entries.append(
            {
                "id": "clean_ai",
                "path": "fixtures/media/clean_ai.png",
                "clean": True,
                "truth": [],
            }
        )

    sample_15s = ROOT / "fixtures" / "media" / "sample_15s.mp4"
    if sample_15s.exists():
        entries.append(
            {
                "id": "sample_15s",
                "path": "fixtures/media/sample_15s.mp4",
                "clean": False,
                "truth": [
                    {"category": "logo", "ref": "trademark", "level": "P0"},
                    {"category": "trademark_text", "ref": "6124779", "level": "P1"},
                ],
            }
        )

    risk_ad = ROOT / "fixtures" / "media" / "risk_ad_10s.mp4"
    if risk_ad.exists():
        entries.append(
            {
                "id": "risk_ad_10s",
                "path": "fixtures/media/risk_ad_10s.mp4",
                "clean": False,
                "truth": [
                    {"category": "copyright_image", "ref": "web:", "level": "P0"},
                    {"category": "celebrity", "ref": "rekognition", "level": "P1"},
                    {"category": "bgm", "ref": "acrcloud", "level": "P2"},
                ],
            }
        )

    if len(entries) > 12:
        raise SystemExit(f"冲刺模式限制评测集 ≤12 条，当前 {len(entries)}")

    EVAL_PATH.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )

    # 给低置信候选样本写一份 image_search mock：只有 visually_similar（P2 / 0.5），
    # 用来在评测里真实触发 Verifier 的二次检索分支
    verify_image = OUT_DIR / "verify_prompt.png"
    mock_path = ROOT / "fixtures" / "cache" / "image_search.json"
    mock = {"schema": 1, "assets": {}}
    if mock_path.exists():
        mock = json.loads(mock_path.read_text(encoding="utf-8"))
    mock.setdefault("assets", {})[_sha(verify_image)] = {
        "scenes": {
            "0": {
                "provider": "google_vision(mock)",
                "matches": [
                    {
                        "url": "https://example-gallery.test/retro-poster",
                        "title": "Retro poster collection",
                        "match_type": "similar",
                        "domain_kind": "blog",
                    }
                ],
                "best_guess_labels": ["poster"],
            }
        }
    }
    mock_path.write_text(json.dumps(mock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assets: dict[str, list[dict]] = {}
    if sample_15s.exists():
        assets[_sha(sample_15s)] = [
            {"category": "logo", "level": "P1", "confidence": 0.6},
            {"category": "trademark_text", "level": "P1", "confidence": 0.7},
        ]
    if risk_ad.exists():
        assets[_sha(risk_ad)] = [
            {"category": "copyright_image", "level": "P0", "confidence": 0.6},
            {"category": "celebrity", "level": "P1", "confidence": 0.75},
        ]
    if clean_ai.exists():
        # 人为记录的一次幻觉：模型把 AI 生成人像当成公众人物
        assets[_sha(clean_ai)] = [{"category": "celebrity", "level": "P1", "confidence": 0.6}]

    VLM_CACHE.parent.mkdir(parents=True, exist_ok=True)
    VLM_CACHE.write_text(
        json.dumps(
            {
                "note": "纯 VLM 基线缓存（一次人工记录的模型直读结论），仅用于评测对比",
                "assets": assets,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"评测集: {EVAL_PATH.relative_to(ROOT)}（{len(entries)} 条）")
    print(f"干净样本: {OUT_DIR.relative_to(ROOT)}")
    print(f"VLM 基线缓存: {VLM_CACHE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
