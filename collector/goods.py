"""Which goods the collector watches, and how one book is asked for.

Every good is a craftable, plain Unique (6) or Strange (11) cosmetic or weapon, one book per
good, up to 30 listings per side. A `Kind` says how a book is asked for: its quality, and
whether the request also excludes Australium, killstreak and festivized listings (`strict`).
The stored row's kind columns are always the plain good's.

This is the public half of the collector's site module. The part that fetches a book from
the website and parses its response is not in this repository; `replay_source.py` feeds
stored books through the same `ScrapeQueue` transport interface instead.
"""
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

SITE = "https://next.backpack.tf"
QIDS = (6, 11)                 # the qualities collected: Unique + Strange
# Legacy books (the 2,881 cosmetics watched before 2026-10-31) were asked for loosely until
# pass 489 and strictly since, like every other book. False = the old loose request again.
LEGACY_STRICT = True


@dataclass(frozen=True)
class Kind:
    """How a book is asked for: its quality, and whether the request also excludes
    Australium, killstreak and festivized listings (`strict`)."""
    quality: int = 6
    strict: bool = True

    @property
    def shape(self) -> str:
        return f"q{self.quality}{'!' if self.strict else ''}"

    def query(self, item: str) -> dict:
        """The classifieds filters for this book."""
        q = {"itemName": item, "quality": str(self.quality), "craftable": "1"}
        if self.strict:
            q["australium"] = "0"
            q["killstreakTier"] = "0"
            q["festivized"] = "0"
        return q

    def meta(self) -> dict:
        """The fields a request carries to the row."""
        return asdict(self)

    @classmethod
    def of(cls, quality: int, meta=None) -> "Kind":
        """The kind a request asked for: its meta's `strict`, else a legacy book."""
        m = meta or {}
        if "strict" not in m:
            return legacy(quality)
        return cls(int(quality), bool(m["strict"]))


def legacy(quality: int) -> Kind:
    """The kind of a legacy book (a watched cosmetic from before 2026-10-31)."""
    return Kind(int(quality), strict=LEGACY_STRICT)


def app_url(item: str, kind) -> str:
    """The public classifieds page for this book (a link for people; nothing fetches it
    here). `kind` may be a bare quality (a legacy book)."""
    kind = legacy(kind) if isinstance(kind, int) else kind
    return f"{SITE}/classifieds?" + urlencode(kind.query(item))
