"""Tests for scripts/fetch_feeds.py. Run with: python -m unittest discover -s tests -v

The fixtures below are small hand-written samples shaped like the real feeds and
pages. They are not live data.
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_feeds as ff  # noqa: E402

NOW = datetime(2026, 11, 2, 22, 0, tzinfo=timezone.utc)

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>Sample</title>
<item>
  <title>Election Eve: what to expect</title>
  <link>https://example.org/p/election-eve</link>
  <pubDate>Mon, 02 Nov 2026 18:30:00 GMT</pubDate>
  <description><![CDATA[<p>Rumors about <b>ballot</b> counting &amp; delays.</p>]]></description>
</item>
<item>
  <title>School bus route changes</title>
  <link>https://example.org/p/buses</link>
  <pubDate>Sun, 01 Nov 2026 12:00:00 GMT</pubDate>
  <description>Routes change next week.</description>
  <category>Education</category>
</item>
<item>
  <title>Bad link item</title>
  <link>javascript:alert(1)</link>
  <pubDate>Sun, 01 Nov 2026 12:00:00 GMT</pubDate>
</item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Early voting totals</title>
    <link rel="self" href="https://example.org/self"/>
    <link rel="alternate" href="https://example.org/early-voting"/>
    <updated>2026-11-01T10:00:00-04:00</updated>
    <summary>More than 3 million ballots cast.</summary>
  </entry>
</feed>"""

NCSBE_HTML = """
<html><body>
<h3>Recent Press Releases</h3>
<div>Nov 2, 2026</div>
<a href="https://www.ncsbe.gov/news/press-releases/2026/11/02/state-election-officials-host-two-press-conferences-election-day">State Election Officials to Host Two Press Conferences on Election Day</a>
<div>Oct 30, 2026</div>
<a href="/news/press-releases/2026/10/30/10-tips-election-day-voters-2026-general-election">10 Tips for Election Day Voters: 2026 General Election</a>
<a href="/news/press-releases/2026/10/30/10-tips-election-day-voters-2026-general-election">Read More</a>
<a href="https://www.ncsbe.gov/news/recent-press-releases">View all press releases</a>
<a href="https://www.ncsbe.gov/voting/voter-id">Voter Photo ID</a>
</body></html>
"""

COMMON_CAUSE_HTML = """
<a href="https://www.commoncause.org/north-carolina/articles/wild_summer_in_nc_politics/">Read More</a>
<a href="https://www.commoncause.org/north-carolina/articles/wild_summer_in_nc_politics/">5 things to know about a wild summer in North Carolina politics</a>
<a href="https://www.commoncause.org/north-carolina/articles/speak-up-for-early-voting-in-north-carolina/">Speak up for early voting in North Carolina</a>
<a href="https://www.commoncause.org/north-carolina/updates/?&pg=2">Load More</a>
"""

ARTICLE_HTML = """
<meta property="article:modified_time" content="2026-10-30T10:00:00+00:00" />
<script type="application/ld+json">{"@type":"Article","datePublished":"2026-10-28T21:18:45+00:00"}</script>
"""


class ParsingTests(unittest.TestCase):
    def test_rss_fields_and_html_stripping(self):
        entries = ff.parse_feed(RSS)
        self.assertEqual(len(entries), 3)
        first = entries[0]
        self.assertEqual(first["title"], "Election Eve: what to expect")
        self.assertEqual(first["url"], "https://example.org/p/election-eve")
        self.assertEqual(first["summary"], "Rumors about ballot counting & delays.")
        self.assertEqual(first["published"], datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc))
        self.assertEqual(entries[1]["categories"], ["Education"])

    def test_atom_prefers_alternate_link_and_converts_timezone(self):
        entry = ff.parse_feed(ATOM)[0]
        self.assertEqual(entry["url"], "https://example.org/early-voting")
        self.assertEqual(entry["published"], datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc))

    def test_xml_with_entity_declarations_is_refused(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel/></rss>'
        with self.assertRaises(ValueError):
            ff.parse_feed(evil)

    def test_links_ncsbe_dates_come_from_url_and_titles_are_deduped(self):
        src = json.loads((ROOT / "sources.json").read_text())["sources"][0]
        strat = src["fetch"][0]
        entries = ff.parse_links(NCSBE_HTML, strat["url"], strat["link_regex"], strat["date_regex"])
        self.assertEqual([e["title"] for e in entries], [
            "State Election Officials to Host Two Press Conferences on Election Day",
            "10 Tips for Election Day Voters: 2026 General Election"])
        self.assertEqual(entries[1]["published"], datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc))
        self.assertTrue(entries[1]["date_only"])
        self.assertEqual(entries[1]["url"],
                         "https://www.ncsbe.gov/news/press-releases/2026/10/30/10-tips-election-day-voters-2026-general-election")

    def test_links_without_dates_and_generic_titles(self):
        entries = ff.parse_links(COMMON_CAUSE_HTML, "https://www.commoncause.org/north-carolina/updates/",
                                 r"/north-carolina/articles/[^/?#]+/?$")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["title"], "5 things to know about a wild summer in North Carolina politics")
        self.assertIsNone(entries[0]["published"])

    def test_page_date_from_json_ld_or_meta(self):
        self.assertEqual(ff.page_date(ARTICLE_HTML), datetime(2026, 10, 28, 21, 18, 45, tzinfo=timezone.utc))
        meta = '<meta property="article:published_time" content="2026-10-29T08:00:00-04:00" />'
        self.assertEqual(ff.page_date(meta), datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc))
        self.assertIsNone(ff.page_date("<html>no dates here</html>"))


class BuildTests(unittest.TestCase):
    def config(self, **extra):
        src = {"id": "s", "name": "S", "kind": "stream",
               "fetch": [{"type": "rss", "url": "https://example.org/feed"}]}
        src.update(extra)
        return {"settings": {"maxAgeDays": 45, "maxPerSource": 40}, "sources": [src]}

    def test_unsafe_urls_are_dropped(self):
        feed = ff.build_feed(self.config(), {}, lambda u: RSS, NOW)
        urls = [i["url"] for i in feed["items"]]
        self.assertNotIn("javascript:alert(1)", urls)
        self.assertEqual(len(urls), 2)

    def test_keyword_filter(self):
        cfg = self.config(keyword_regex=json.loads((ROOT / "sources.json").read_text())["sources"][3]["keyword_regex"])
        feed = ff.build_feed(cfg, {}, lambda u: RSS, NOW)
        self.assertEqual([i["title"] for i in feed["items"]], ["Election Eve: what to expect"])
        self.assertEqual(feed["sources"][0]["status"]["state"], "ok")

    def test_keyword_regex_avoids_look_alikes(self):
        import re
        rx = re.compile(json.loads((ROOT / "sources.json").read_text())["sources"][3]["keyword_regex"], re.I)
        for text in ("Duke Energy electric rates rise", "Pollution cleanup at the river", "Politics of the budget"):
            self.assertIsNone(rx.search(text), text)
        for text in ("Voters line up for early voting", "Polls close at 7:30", "New precinct maps",
                     "Absentee ballots arrive", "Gerrymandering case heads to court"):
            self.assertIsNotNone(rx.search(text), text)

    def test_url_regex_filters_other_sections(self):
        cfg = self.config(url_regex="/p/election")
        feed = ff.build_feed(cfg, {}, lambda u: RSS, NOW)
        self.assertEqual(len(feed["items"]), 1)

    def test_failed_source_keeps_previous_items_and_reports_error(self):
        first = ff.build_feed(self.config(), {}, lambda u: RSS, NOW)

        def boom(url):
            raise RuntimeError("timeout")
        second = ff.build_feed(self.config(), first, boom, NOW)
        self.assertEqual(len(second["items"]), 2)
        self.assertEqual(second["sources"][0]["status"]["state"], "error")
        self.assertIn("timeout", second["sources"][0]["status"]["error"])

    def test_fallback_strategy_used_when_first_fails(self):
        cfg = self.config(fetch=[
            {"type": "rss", "url": "https://example.org/feed"},
            {"type": "links", "url": "https://example.org/list", "link_regex": "/north-carolina/articles/"}])

        def fetcher(url):
            if url.endswith("/feed"):
                raise RuntimeError("404")
            return COMMON_CAUSE_HTML.encode()
        feed = ff.build_feed(cfg, {}, fetcher, NOW)
        self.assertEqual(len(feed["items"]), 2)
        self.assertTrue(all(i.get("dateApprox") for i in feed["items"]))

    def test_first_seen_time_is_remembered_for_undated_links(self):
        cfg = self.config(fetch=[{"type": "links", "url": "https://example.org/list",
                                  "link_regex": "/north-carolina/articles/"}])
        fetcher = lambda u: COMMON_CAUSE_HTML.encode()  # noqa: E731
        first = ff.build_feed(cfg, {}, fetcher, NOW)
        later = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
        second = ff.build_feed(cfg, first, fetcher, later)
        self.assertEqual({i["published"] for i in second["items"]}, {"2026-11-02T22:00:00Z"})

    def page_dates_config(self):
        return self.config(fetch=[{"type": "links", "url": "https://example.org/list",
                                   "link_regex": "/north-carolina/articles/", "page_dates": True}])

    def test_page_dates_are_read_once_and_remembered(self):
        calls = []

        def fetcher(url):
            calls.append(url)
            return (COMMON_CAUSE_HTML if url.endswith("/list") else ARTICLE_HTML).encode()
        first = ff.build_feed(self.page_dates_config(), {}, fetcher, NOW)
        self.assertEqual({i["published"] for i in first["items"]}, {"2026-10-28T21:18:45Z"})
        self.assertFalse(any(i.get("dateApprox") for i in first["items"]))
        self.assertEqual(len(calls), 3)
        calls.clear()
        ff.build_feed(self.page_dates_config(), first, fetcher, NOW)
        self.assertEqual(calls, ["https://example.org/list"])

    def test_page_date_failure_falls_back_and_stops_lookups(self):
        calls = []

        def fetcher(url):
            calls.append(url)
            if url.endswith("/list"):
                return COMMON_CAUSE_HTML.encode()
            raise RuntimeError("timeout")
        feed = ff.build_feed(self.page_dates_config(), {}, fetcher, NOW)
        self.assertEqual(len(feed["items"]), 2)
        self.assertTrue(all(i.get("dateApprox") for i in feed["items"]))
        self.assertEqual(len(calls), 2)
        self.assertEqual(feed["sources"][0]["status"]["state"], "ok")

    def test_old_items_expire_and_future_dates_are_clamped(self):
        old = {"items": [{"id": "x", "source": "s", "title": "Old", "url": "https://example.org/old",
                          "published": "2026-08-01T00:00:00Z", "summary": ""}]}
        rss = (b'<rss><channel><item><title>Future</title><link>https://example.org/f</link>'
               b'<pubDate>Fri, 01 Jan 2027 00:00:00 GMT</pubDate></item></channel></rss>')
        feed = ff.build_feed(self.config(), old, lambda u: rss, NOW)
        self.assertEqual([i["title"] for i in feed["items"]], ["Future"])
        self.assertEqual(feed["items"][0]["published"], "2026-11-02T22:00:00Z")

    def test_tools_have_no_status_and_no_items(self):
        cfg = {"sources": [{"id": "t", "name": "T", "kind": "tool", "homepage": "https://example.org"}]}
        feed = ff.build_feed(cfg, {}, lambda u: self.fail("tools must not be fetched"), NOW)
        self.assertIsNone(feed["sources"][0]["status"])
        self.assertEqual(feed["items"], [])

    def test_no_fetch_mode_marks_empty_sources_pending(self):
        feed = ff.build_feed(self.config(), {}, lambda u: self.fail("must not fetch"), NOW, no_fetch=True)
        self.assertEqual(feed["sources"][0]["status"]["state"], "pending")

    def test_real_config_is_valid(self):
        cfg = json.loads((ROOT / "sources.json").read_text())
        ids = [s["id"] for s in cfg["sources"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("uwcip", ids)
        for s in cfg["sources"]:
            self.assertTrue(s["homepage"].startswith("https://"))
            if s["kind"] == "stream":
                self.assertTrue(s["fetch"])


class MainTests(unittest.TestCase):
    def test_second_run_with_same_data_does_not_rewrite_file(self):
        with tempfile.TemporaryDirectory() as d:
            cfg_path, out = Path(d, "sources.json"), Path(d, "feed.json")
            cfg_path.write_text(json.dumps({"sources": [
                {"id": "s", "name": "S", "kind": "stream",
                 "fetch": [{"type": "rss", "url": "https://example.org/feed"}]}]}))
            original = ff.fetch_url
            ff.fetch_url = lambda url: RSS
            try:
                ff.main(["--config", str(cfg_path), "--out", str(out)])
                first_text = out.read_text()
                ff.main(["--config", str(cfg_path), "--out", str(out)])
                self.assertEqual(out.read_text(), first_text)
            finally:
                ff.fetch_url = original


if __name__ == "__main__":
    unittest.main()
