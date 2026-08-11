"""The title library — what is on sale, and what the free preview looks like.

Phase 1 serves pages from disk. IPFS lands in Phase 2 and must not sit on the Phase 1
critical path.

Layout under the content directory:

    titles/<slug>/meta.json          title, creator, blurb, preview_pages
    titles/<slug>/pages/001.png ...  full-resolution pages, sold
    previews/<slug>/001.png ...      scrambled pages, free

Preview generation is a placeholder scramble (see `scramble.py`). The real
`aiposematic` integration is Phase 2; the boundary it defines is the same either way,
so the paywall shape is settled now and the cipher is swapped later.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class UnknownTitle(KeyError):
    """Requested title is not in the library."""


@dataclass(frozen=True)
class Title:
    slug: str
    name: str
    creator: str
    blurb: str
    pages: list[Path]
    previews: list[Path] = field(default_factory=list)
    preview_pages: int = 3

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def public_dict(self, price_display: str, issue_url: str) -> dict:
        """Catalogue entry. Written for an agent reading the Bazaar, not for a browser."""
        return {
            "slug": self.slug,
            "title": self.name,
            "creator": self.creator,
            "blurb": self.blurb,
            "pages": self.page_count,
            "price": price_display,
            "previewPages": min(self.preview_pages, self.page_count),
            "issueUrl": issue_url,
        }


def _sorted_pages(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(
        (p for p in d.iterdir() if p.suffix.lower() in PAGE_SUFFIXES),
        key=lambda p: p.name,
    )


class Library:
    """Titles available on this node."""

    def __init__(self, content_dir: Path) -> None:
        self.content_dir = Path(content_dir)
        self.titles_dir = self.content_dir / "titles"
        self.previews_dir = self.content_dir / "previews"
        self._titles: dict[str, Title] = {}

    def load(self) -> Library:
        self._titles = {}
        if not self.titles_dir.is_dir():
            return self
        for d in sorted(self.titles_dir.iterdir()):
            if not d.is_dir() or not SLUG_RE.match(d.name):
                continue
            title = self._load_title(d)
            if title is not None:
                self._titles[title.slug] = title
        return self

    def _load_title(self, d: Path) -> Title | None:
        pages = _sorted_pages(d / "pages")
        if not pages:
            return None
        meta = {}
        meta_file = d / "meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
        return Title(
            slug=d.name,
            name=meta.get("title", d.name.replace("-", " ").title()),
            creator=meta.get("creator", "Unknown"),
            blurb=meta.get("blurb", ""),
            pages=pages,
            previews=_sorted_pages(self.previews_dir / d.name),
            preview_pages=int(meta.get("preview_pages", 3)),
        )

    def __len__(self) -> int:
        return len(self._titles)

    def all(self) -> list[Title]:
        return list(self._titles.values())

    def get(self, slug: str) -> Title:
        try:
            return self._titles[slug]
        except KeyError as exc:
            raise UnknownTitle(slug) from exc

    def preview_page(self, slug: str, n: int) -> Path:
        """One free scrambled page. `n` is 1-indexed, as it reads.

        Only the first `preview_pages` are public — that boundary is the paywall.
        """
        title = self.get(slug)
        limit = min(title.preview_pages, len(title.previews))
        if n < 1 or n > limit:
            raise UnknownTitle(f"{slug}#{n}")
        return title.previews[n - 1]
