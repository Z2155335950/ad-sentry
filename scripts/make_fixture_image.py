"""生成 Phase 0 测试图（一张带假 logo 区域的合成广告图），只需运行一次。"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "media" / "sample_image.png"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    width, height = 640, 360
    image = Image.new("RGB", (width, height), (18, 26, 42))
    draw = ImageDraw.Draw(image)

    for y in range(height):
        t = y / height
        draw.line(
            [(0, y), (width, y)],
            fill=(int(18 + 40 * t), int(26 + 60 * t), int(42 + 90 * t)),
        )

    # 假 logo 区域，对应 fixture 里的 bbox
    draw.rounded_rectangle((455, 45, 565, 110), radius=14, fill=(210, 40, 40))
    draw.text((487, 66), "LOGO", fill=(255, 255, 255))
    draw.text((40, 300), "SALE 50% OFF", fill=(255, 255, 255))

    image.save(OUT)
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
