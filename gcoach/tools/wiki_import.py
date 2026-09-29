"""Import Genius Invokation TCG card data from the Russian Genshin Impact Fandom wiki.

Source page: https://genshin-impact.fandom.com/ru/wiki/Священный_призыв_семерых/Список_карт

The page renders its tables from wiki categories, so the importer walks exactly
those categories through the MediaWiki API, downloads each card page's wikitext
and stores it in ``gcoach/knowledge/data/wiki_raw.json``. The structured
knowledge base is then built from that cache by ``gcoach.knowledge.wiki_parse``.

Usage:
    python -m gcoach.tools.wiki_import            # download + build
    python -m gcoach.tools.wiki_import --offline  # rebuild from the cached raw file
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ..knowledge.wiki_parse import build_knowledge

log = logging.getLogger("gcoach.wiki_import")

API = "https://genshin-impact.fandom.com/ru/api.php"
SOURCE_PAGE = "https://genshin-impact.fandom.com/ru/wiki/Священный_призыв_семерых/Список_карт"
USER_AGENT = "GCoach-knowledge-importer/1.0 (local Genius Invokation coach; read-only)"
DATA_DIR = Path(__file__).resolve().parents[1] / "knowledge" / "data"
RAW_FILE = DATA_DIR / "wiki_raw.json"

# Categories used by the tables on the source page.
CATEGORIES = {
    "character": "Карты персонажей СП7",
    "equipment": "Карты экипировки СП7",
    "support": "Карты поддержки СП7",
    "event": "Карты событий СП7",
    "summon": "Помощники СП7",
}
UNAVAILABLE_CATEGORY = "Недоступные карты"
API_EN = "https://genshin-impact.fandom.com/api.php"
RULES_EN_PAGE = "Genius Invokation TCG/Rules"


def _get(params: dict, retries: int = 3, api: str = API) -> dict:
    params = {**params, "format": "json", "formatversion": "2"}
    # POST: 50 Cyrillic titles do not fit into a GET query string.
    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(api, data=body, headers={
        "User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # network hiccup -> back off and retry
            if attempt == retries - 1:
                raise
            log.warning("request failed (%s), retrying", exc)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def category_members(category: str) -> list[str]:
    titles: list[str] = []
    cont: dict = {}
    while True:
        data = _get({"action": "query", "list": "categorymembers", "cmtitle": f"Категория:{category}",
                     "cmlimit": "500", "cmtype": "page", **cont})
        titles += [m["title"] for m in data["query"]["categorymembers"]]
        if "continue" not in data:
            return titles
        cont = data["continue"]


def fetch_pages(titles: list[str], delay: float = 0.4) -> dict[str, dict]:
    pages: dict[str, dict] = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        data = _get({"action": "query", "prop": "revisions", "rvprop": "content|ids|timestamp",
                     "rvslots": "main", "titles": "|".join(chunk)})
        for p in data["query"]["pages"]:
            if p.get("missing") or not p.get("revisions"):
                continue
            rev = p["revisions"][0]
            pages[p["title"]] = {
                "title": p["title"],
                "revid": rev.get("revid"),
                "timestamp": rev.get("timestamp"),
                "wikitext": rev["slots"]["main"]["content"],
            }
        log.info("fetched %d/%d pages", min(i + 50, len(titles)), len(titles))
        time.sleep(delay)
    return pages


def fetch_rules_en() -> dict:
    """The Russian wiki has no TCG rules page (its reaction pages link to a missing
    section), so reaction rules come from the English wiki rules page."""
    data = _get({"action": "parse", "page": RULES_EN_PAGE, "prop": "wikitext|revid"}, api=API_EN)
    return {
        "url": "https://genshin-impact.fandom.com/wiki/" + RULES_EN_PAGE.replace(" ", "_"),
        "revid": data["parse"].get("revid"),
        "wikitext": data["parse"]["wikitext"],
    }


def download() -> dict:
    unavailable = set(category_members(UNAVAILABLE_CATEGORY))
    membership: dict[str, list[str]] = {}
    for kind, cat in CATEGORIES.items():
        for title in category_members(cat):
            membership.setdefault(title, []).append(kind)
    pages = fetch_pages(sorted(membership))
    for title, page in pages.items():
        page["kinds"] = membership.get(title, [])
        page["unavailable"] = title in unavailable
    raw = {
        "source": SOURCE_PAGE,
        "api": API,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": pages,
        "rules_en": fetch_rules_en(),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    return raw


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="rebuild from the cached raw file")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.offline:
        raw = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    else:
        raw = download()
    report = build_knowledge(raw, DATA_DIR)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
