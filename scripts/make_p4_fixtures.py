"""生成 Phase 4 的三份合成授权书 PDF（PROMPT §9 演示准备）。

分别覆盖：授权已过期 / 授权地域不含东南亚 / 授权有效。
内容为演示合成文本，不对应任何真实授权关系。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "fixtures" / "licenses"

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)

COMMON_HEADER = [
    "素材授权书（演示样本 / SYNTHETIC DEMO LICENCE）",
    "本文件由 scripts/make_p4_fixtures.py 生成，仅用于演示与测试。",
    "",
]

DOCUMENTS = {
    "license_expired.pdf": [
        "授权方（Licensor）：Star Music Publishing Ltd.",
        "被授权方（Licensee）：Flyer AdCo",
        "授权类型（Scope）：music",
        "授权范围（Scope Items）：示例曲目《Neon Sky》",
        "地域范围（Territory）：美国、欧盟",
        "媒介范围（Media）：TikTok、Meta",
        "生效日期（Start）：2024-01-01",
        "到期日期（End）：2025-06-30",
        "可转授权（Sublicensable）：否",
        "限制条款（Restrictions）：不得用于敏感品类",
    ],
    "license_sea_excluded.pdf": [
        "授权方（Licensor）：Blue Ocean Music Group",
        "被授权方（Licensee）：Flyer AdCo",
        "授权类型（Scope）：music",
        "授权范围（Scope Items）：示例曲目《Harbor Lights》",
        "地域范围（Territory）：美国、欧盟、英国",
        "媒介范围（Media）：TikTok、Meta",
        "生效日期（Start）：2026-01-01",
        "到期日期（End）：2027-12-31",
        "可转授权（Sublicensable）：否",
        "限制条款（Restrictions）：不得转售",
    ],
    "license_valid.pdf": [
        "授权方（Licensor）：Sunrise Audio Works",
        "被授权方（Licensee）：Flyer AdCo",
        "授权类型（Scope）：music",
        "授权范围（Scope Items）：示例曲目《Sunrise Drive》",
        "地域范围（Territory）：东南亚、美国",
        "媒介范围（Media）：TikTok、Meta",
        "生效日期（Start）：2026-01-01",
        "到期日期（End）：2027-12-31",
        "可转授权（Sublicensable）：是",
        "限制条款（Restrictions）：不得用于敏感品类",
    ],
}


def _font_file() -> str | None:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _write_pdf(path: Path, lines: list[str], font_file: str | None) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    fontname = "helv"
    if font_file:
        page.insert_font(fontname="cjk", fontfile=font_file)
        fontname = "cjk"
    y = 90.0
    for line in [*COMMON_HEADER, *lines]:
        page.insert_text((64, y), line, fontsize=11, fontname=fontname)
        y += 26.0
    if font_file:
        doc.subset_fonts()  # 只嵌入用到的字形，否则每份 PDF 十几 MB
    doc.save(path)
    doc.close()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    force = "--force" in sys.argv
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    font_file = _font_file()
    if font_file is None:
        print("[WARN] 未找到中文字体，生成的文件只有 ASCII 内容可读")

    for name, lines in DOCUMENTS.items():
        path = OUT_DIR / name
        if path.exists() and not force:
            continue
        _write_pdf(path, lines, font_file)
        print(f"生成 {path.relative_to(ROOT)}")
    print(f"授权书目录: {OUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
