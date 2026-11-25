"""把 flat 的 mock 缓存拆成 DEMO_MODE 使用的按素材缓存（PROMPT §16）。

输入：fixtures/cache/<tool>.json（{assets: {sha256: {scenes|windows}}})
输出：fixtures/cache/<tool>/<sha256>.json，内容与单个 assets 条目一致。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "fixtures" / "cache"
TOOLS = ("image_search", "celebrity", "music", "logo")


def main() -> None:
    total = 0
    for tool in TOOLS:
        source = CACHE_DIR / f"{tool}.json"
        if not source.exists():
            print(f"[SKIP] {source} 不存在")
            continue
        assets = json.loads(source.read_text(encoding="utf-8")).get("assets") or {}
        target_dir = CACHE_DIR / tool
        target_dir.mkdir(parents=True, exist_ok=True)
        for sha256, entry in assets.items():
            target = target_dir / f"{sha256}.json"
            target.write_text(
                json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            total += 1
        print(f"[OK] {tool}: {len(assets)} 条")
    print(f"演示缓存文件共 {total} 个")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
