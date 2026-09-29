"""Download card artwork (characters, action cards, summons) from the Russian Genshin wiki.

    python -m gcoach.tools.wiki_images            # download missing images
    python -m gcoach.tools.wiki_images --force    # re-download everything

The image file of every card is named in its infobox (``|Изображение = ...``),
already cached in ``knowledge/data/wiki_raw.json``. The wiki serves server-side
scaled thumbnails, so only small versions are downloaded. Images are official
HoYoverse artwork: they are stored locally for personal use and are not part of
the repository (see .gitignore).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from ..knowledge.wikitext import find_templates
from .wiki_import import RAW_FILE, USER_AGENT, _get

log = logging.getLogger("gcoach.wiki_images")
OUT_DIR = Path(__file__).resolve().parents[1] / "web" / "img" / "cards"
INDEX = OUT_DIR / "index.json"
THUMB_WIDTH = 320


def file_key(card_id: str) -> str:
    """Filesystem-safe name (ids contain ':' and quotes)."""
    return hashlib.md5(card_id.encode("utf-8")).hexdigest()[:14] + ".webp"


def collect() -> dict[str, str]:
    """{knowledge-base id: wiki image file name}"""
    raw = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for title, page in raw["pages"].items():
        ibs = find_templates(page["wikitext"], "СП7 Инфобокс")
        if not ibs:
            continue
        ib = ibs[0].named
        image = (ib.get("Изображение") or "").strip()
        if not image:
            continue
        card_id = ib.get("Название") or title.removeprefix("СП7:")
        out[card_id] = image
    return out


def thumb_urls(files: list[str]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for i in range(0, len(files), 50):
        chunk = files[i:i + 50]
        data = _get({"action": "query", "prop": "imageinfo", "iiprop": "url", "iiurlwidth": str(THUMB_WIDTH),
                     "redirects": "1", "titles": "|".join("Файл:" + f for f in chunk)})
        q = data["query"]
        norm = {n["to"]: n["from"] for n in q.get("normalized", [])}
        redir = {r["to"]: r["from"] for r in q.get("redirects", [])}  # renamed files on the wiki
        for p in q["pages"]:
            info = (p.get("imageinfo") or [{}])[0]
            url = info.get("thumburl") or info.get("url")
            if url:
                title = redir.get(p["title"], p["title"])
                name = norm.get(title, title).split(":", 1)[-1]
                urls[name] = url
        log.info("resolved %d/%d image urls", min(i + 50, len(files)), len(files))
        time.sleep(0.3)
    return urls


def download(url: str, retries: int = 4) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429, 503) and attempt < retries - 1:
                time.sleep(8 * (attempt + 1))  # CDN rate limit: back off and retry
                continue
            raise
    raise RuntimeError("unreachable")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index: dict[str, str] = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() and not args.force else {}
    wanted = collect()
    todo = {cid: img for cid, img in wanted.items()
            if args.force or cid not in index or not (OUT_DIR / index[cid]).exists()}
    log.info("cards with images: %d, to download: %d", len(wanted), len(todo))
    urls = thumb_urls(sorted(set(todo.values())))
    ok = failed = 0
    for n, (cid, img) in enumerate(sorted(todo.items()), 1):
        url = urls.get(img) or urls.get(img.replace("_", " "))
        if not url:
            failed += 1
            continue
        try:
            data = download(url)
            im = Image.open(io.BytesIO(data)).convert("RGBA")
            name = file_key(cid)
            im.save(OUT_DIR / name, "WEBP", quality=86, method=5)
            index[cid] = name
            ok += 1
        except Exception as exc:  # keep going; a missing picture just falls back to the drawn card
            log.warning("%s: %s", cid, exc)
            failed += 1
        if n % 50 == 0:
            log.info("downloaded %d/%d", n, len(todo))
            INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
        time.sleep(0.15)
    INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    size = sum(p.stat().st_size for p in OUT_DIR.glob("*.webp"))
    print(f"downloaded: {ok}, failed: {failed}, total images: {len(index)}, {size // 1024} KB")


if __name__ == "__main__":
    main()
