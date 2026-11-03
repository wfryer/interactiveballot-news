#!/usr/bin/env python3
"""Collect election news items from the sources in sources.json and write feed.json.

Standard library only, so it runs on any GitHub Actions runner without installs.

Each source lists one or more fetch strategies, tried in order until one returns
entries:
  rss    parse an RSS 2.0, RSS 1.0 or Atom feed
  links  read an HTML page and collect links that match a regular expression
         (used when a site has no feed). If the link contains a date, such as
         /2026/11/02/, that date is used; otherwise the first time we saw the
         link is used and the item is marked as approximate.

One source failing never stops the others. A failed source keeps the items it
had last time.
"""
import argparse
import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

USER_AGENT = "InteractiveBallotNewsBot/1.0 (+https://interactiveballot.com/news/)"
META_KEYS = ("id", "name", "short", "kind", "type", "scope", "description",
             "homepage", "about", "aboutLabel", "cta")
GENERIC_TITLES = {"read more", "learn more", "continue reading", "view all",
                  "view embed", "more", "click here"}


# ---------------------------------------------------------------- fetching

def fetch_url(url, timeout=25, retries=2):
    """Return the response body as bytes, retrying a couple of times."""
    last = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, "
                          "application/xml, text/html;q=0.8, */*;q=0.5",
            })
            with urlopen(req, timeout=timeout) as resp:
                return resp.read(5_000_000)
        except Exception as exc:  # network errors are reported, not raised
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{type(last).__name__}: {last}")


# ----------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def strip_html(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = re.sub(r"&(?:nbsp|#160);", " ", value)
    parser = HTMLParser(convert_charrefs=True)
    out = []
    parser.handle_data = out.append
    try:
        parser.feed(value)
        parser.close()
        value = "".join(out)
    except Exception:
        pass
    return " ".join(value.split())


def truncate(value, limit):
    if len(value) <= limit:
        return value
    cut = value[:limit].rsplit(" ", 1)[0].rstrip(",;:.")
    return cut + "…"


def parse_date(value):
    """Parse an RSS or ISO 8601 date string into an aware UTC datetime, or None."""
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_feed(data):
    """Parse RSS or Atom bytes into a list of entry dicts."""
    if b"<!ENTITY" in data:
        raise ValueError("refusing XML that declares entities")
    root = ET.fromstring(data)
    entries = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        entry = {"title": "", "url": "", "published": None, "summary": "",
                 "categories": []}
        guid = ""
        for child in el:
            name = _local(child.tag)
            if name == "title":
                entry["title"] = strip_html(_text(child))
            elif name == "link":
                href = child.get("href")
                if href:
                    if not entry["url"] or child.get("rel") in (None, "alternate"):
                        entry["url"] = href.strip()
                elif _text(child) and not entry["url"]:
                    entry["url"] = _text(child)
            elif name == "guid":
                guid = _text(child)
            elif name in ("pubDate", "published", "date", "updated"):
                dt = parse_date(_text(child))
                if dt and (entry["published"] is None or name != "updated"):
                    entry["published"] = dt
            elif name in ("description", "summary"):
                entry["summary"] = strip_html(_text(child))
            elif name == "content" and not entry["summary"]:
                entry["summary"] = strip_html(_text(child))
            elif name == "category":
                term = child.get("term") or _text(child)
                if term:
                    entry["categories"].append(term)
        if not entry["url"] and guid.startswith("http"):
            entry["url"] = guid
        entries.append(entry)
    return entries


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._href = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._buf = []

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._buf).split())))
            self._href = None


def parse_links(html_text, base_url, link_regex, date_regex=None):
    """Collect matching links from an HTML page as entry dicts."""
    collector = _Anchors()
    collector.feed(html_text)
    link_re = re.compile(link_regex)
    date_re = re.compile(date_regex) if date_regex else None
    best = {}
    order = []
    for href, text in collector.links:
        url = urljoin(base_url, href).split("#")[0]
        if not link_re.search(url):
            continue
        if len(text) < 8 or text.lower() in GENERIC_TITLES:
            continue
        if url not in best:
            order.append(url)
            best[url] = text
        elif len(text) > len(best[url]):
            best[url] = text
    entries = []
    for url in order:
        published = None
        if date_re:
            m = date_re.search(url)
            if m:
                try:
                    published = datetime(int(m["y"]), int(m["m"]), int(m["d"]),
                                         12, 0, tzinfo=timezone.utc)
                except ValueError:
                    published = None
        entries.append({"title": best[url], "url": url, "published": published,
                        "date_only": published is not None, "summary": "",
                        "categories": []})
    return entries


# ----------------------------------------------------------------- building

def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def safe_http_url(url):
    return bool(re.match(r"https?://[^\s]+$", url or "", re.I))


def run_source(src, fetcher, now, old, summary_chars=280):
    """Try each strategy for a source. Returns (items, error_or_None)."""
    keyword = re.compile(src["keyword_regex"], re.I) if src.get("keyword_regex") else None
    url_re = re.compile(src["url_regex"]) if src.get("url_regex") else None
    errors = []
    for strat in src["fetch"]:
        try:
            raw = fetcher(strat["url"])
            if strat["type"] == "rss":
                entries = parse_feed(raw)
            elif strat["type"] == "links":
                entries = parse_links(raw.decode("utf-8", "replace"), strat["url"],
                                      strat["link_regex"], strat.get("date_regex"))
            else:
                raise ValueError(f"unknown strategy type {strat['type']!r}")
        except Exception as exc:
            errors.append(f"{strat['url']}: {exc}")
            continue
        if not entries:
            errors.append(f"{strat['url']}: no items found")
            continue
        items = []
        for e in entries:
            url, title = e["url"], e["title"]
            if not title or not safe_http_url(url):
                continue
            if url_re and not url_re.search(url):
                continue
            haystack = " ".join([title, e["summary"], " ".join(e["categories"])])
            if keyword and not keyword.search(haystack):
                continue
            published = e["published"]
            approx = False
            if published is None:
                prior = old.get(url)
                published = parse_iso(prior["published"]) if prior else now
                approx = True
            if published > now:
                published = now
            item = {
                "id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:12],
                "source": src["id"],
                "title": title,
                "url": url,
                "published": iso(published),
                "summary": truncate(e["summary"], summary_chars),
            }
            if e.get("date_only"):
                item["dateOnly"] = True
            if approx:
                item["dateApprox"] = True
            items.append(item)
        return items, None
    return [], "; ".join(errors)


def build_feed(config, previous, fetcher, now, no_fetch=False):
    settings = config.get("settings", {})
    max_age = timedelta(days=settings.get("maxAgeDays", 45))
    per_source = settings.get("maxPerSource", 40)
    summary_chars = settings.get("summaryChars", 280)

    prev_by_source = {}
    for item in previous.get("items", []):
        prev_by_source.setdefault(item.get("source"), {})[item.get("url")] = item

    all_items, sources = [], []
    for src in config["sources"]:
        meta = {k: src[k] for k in META_KEYS if k in src}
        if src.get("kind") == "tool" or not src.get("fetch"):
            sources.append({**meta, "status": None})
            continue
        old = prev_by_source.get(src["id"], {})
        fresh, error = ([], None)
        if not no_fetch:
            fresh, error = run_source(src, fetcher, now, old, summary_chars)
        merged = dict(old)
        for item in fresh:
            merged[item["url"]] = item
        kept = [i for i in merged.values() if parse_iso(i["published"]) >= now - max_age]
        kept.sort(key=lambda i: (i["published"], i["id"]), reverse=True)
        kept = kept[:per_source]
        if no_fetch:
            state = "ok" if kept else "pending"
        else:
            state = "error" if error else "ok"
        status = {"state": state, "itemCount": len(kept),
                  "newest": kept[0]["published"] if kept else None}
        if error:
            status["error"] = error[:400]
        sources.append({**meta, "status": status})
        all_items.extend(kept)

    all_items.sort(key=lambda i: (i["published"], i["id"]), reverse=True)
    return {"generatedAt": iso(now), "sources": sources, "items": all_items}


def comparable(feed):
    """The feed minus its timestamp, used to skip rewrites when nothing changed."""
    return {k: v for k, v in feed.items() if k != "generatedAt"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--config", default=str(root / "sources.json"))
    ap.add_argument("--out", default=str(root / "feed.json"))
    ap.add_argument("--no-fetch", action="store_true",
                    help="write source details and keep existing items without fetching")
    args = ap.parse_args(argv)

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    out = Path(args.out)
    previous = {}
    if out.exists():
        try:
            previous = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}

    now = datetime.now(timezone.utc).replace(microsecond=0)
    feed = build_feed(config, previous, fetch_url, now, no_fetch=args.no_fetch)

    if previous and comparable(previous) == comparable(feed):
        print("No changes; feed.json left as is.")
    else:
        out.write_text(json.dumps(feed, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        print(f"Wrote {out} with {len(feed['items'])} items.")

    for s in feed["sources"]:
        st = s.get("status")
        if st:
            note = f" ({st['error']})" if st.get("error") else ""
            print(f"  {s['id']}: {st['state']}, {st['itemCount']} items{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
