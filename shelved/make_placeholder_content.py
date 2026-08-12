"""Generate placeholder pages so the pipeline runs end to end.

Real comics are blocked on rights confirmation (IMPLEMENTATION_PLAN.md §7 decision 1).
This keeps the payment path, the paywall boundary and the packaging testable meanwhile.
Placeholder pages are obviously synthetic on sight — nothing here should ever be
mistaken for, or shipped as, real artwork.

    python tools/make_placeholder_content.py --pages 24

The scramble is a deterministic block shuffle standing in for `aiposematic`, which
arrives in Phase 2. It defines the same paywall boundary; only the cipher changes.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = (1200, 1800)  # portrait, comic-page proportions


def make_page(n: int, total: int, slug: str) -> Image.Image:
    rng = random.Random(f"{slug}:{n}")
    img = Image.new("RGB", SIZE, (250, 248, 244))
    d = ImageDraw.Draw(img)
    for _ in range(6):
        x0, y0 = rng.randint(40, SIZE[0] - 400), rng.randint(40, SIZE[1] - 400)
        d.rectangle(
            [x0, y0, x0 + rng.randint(200, 380), y0 + rng.randint(200, 380)],
            outline=(20, 20, 20),
            width=6,
            fill=(rng.randint(150, 240), rng.randint(150, 240), rng.randint(150, 240)),
        )
    d.rectangle([0, 0, SIZE[0] - 1, SIZE[1] - 1], outline=(20, 20, 20), width=10)
    d.text((60, SIZE[1] - 90), f"PLACEHOLDER  {slug}  page {n}/{total}", fill=(20, 20, 20))
    return img


def scramble(img: Image.Image, key: str, blocks: int = 12) -> Image.Image:
    """Deterministic block shuffle. Placeholder for the aiposematic scramble."""
    w, h = img.size
    bw, bh = w // blocks, h // blocks
    tiles = [
        (c * bw, r * bh, img.crop((c * bw, r * bh, (c + 1) * bw, (r + 1) * bh)))
        for r in range(blocks)
        for c in range(blocks)
    ]
    order = list(range(len(tiles)))
    random.Random(key).shuffle(order)
    out = Image.new("RGB", (bw * blocks, bh * blocks))
    for dest, src in enumerate(order):
        out.paste(tiles[src][2], ((dest % blocks) * bw, (dest // blocks) * bh))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="placeholder-issue-1")
    ap.add_argument("--pages", type=int, default=24)
    ap.add_argument("--preview-pages", type=int, default=3)
    ap.add_argument("--content-dir", default=None)
    args = ap.parse_args()

    if args.content_dir:
        content = Path(args.content_dir)
    else:
        import os
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from pintheonv2.config import resolve_data_dir

        content = Path(os.environ.get("PINTHEON_CONTENT_DIR", resolve_data_dir() / "content"))

    pages_dir = content / "titles" / args.slug / "pages"
    prev_dir = content / "previews" / args.slug
    pages_dir.mkdir(parents=True, exist_ok=True)
    prev_dir.mkdir(parents=True, exist_ok=True)

    for n in range(1, args.pages + 1):
        img = make_page(n, args.pages, args.slug)
        img.save(pages_dir / f"{n:03d}.png")
        # Only the first few pages get a public preview — that is the paywall.
        if n <= args.preview_pages:
            scramble(img, f"{args.slug}:public").save(prev_dir / f"{n:03d}.png")

    (content / "titles" / args.slug / "meta.json").write_text(
        json.dumps(
            {
                "title": "Placeholder Issue #1",
                "creator": "PintheonV2 test fixture",
                "blurb": "Synthetic pages for pipeline testing. Not real artwork.",
                "preview_pages": args.preview_pages,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"{args.pages} pages -> {pages_dir}")
    print(f"{min(args.preview_pages, args.pages)} previews -> {prev_dir}")


if __name__ == "__main__":
    main()
