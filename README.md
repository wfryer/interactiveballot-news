# Interactive Ballot: North Carolina Election News

A filterable feed of election updates for `interactiveballot.com/news`, in the same spirit as news.wesfryer.com.

## How it works

1. `sources.json` lists every source: its name, type, plain-language description, links, and how to fetch it.
2. `scripts/fetch_feeds.py` (Python standard library only) reads each source and writes `feed.json`.
3. `.github/workflows/update-feed.yml` runs the script every 15 minutes and commits `feed.json` only when something changed.
4. `index.html` reads `feed.json` and shows the filterable list, source details, and the trackers section.

Sources that publish a feed (RSS or Atom) are read as a feed. Sources without one are read by collecting links from a page, as the NC State Board of Elections press release list is. Sources that cannot be read at all (JavaScript-only trackers) appear in the "Trackers and tools" section as links.

## Set up

1. Create a GitHub repository (for example `wfryer/interactiveballot-news`) and push this folder to it.
2. In the repository settings, under Actions, General, Workflow permissions, choose "Read and write permissions".
3. Open the Actions tab and run "Update election news feed" once with "Run workflow". Check the log: each source prints `ok` or an error.
4. Put `index.html` and `feed.json` where the site serves `/news/`. Two ways:
   - Serve the repository with GitHub Pages and point `interactiveballot.com/news` at it, or
   - Copy `index.html` into the `news` folder on the server. The page falls back to reading `feed.json` from `raw.githubusercontent.com`, so the copy on the server does not need to be refreshed. Check the `FEED_URLS` list near the top of the script in `index.html` and make sure the second address matches your repository name.

## Add or change a source

Edit `sources.json`; no code changes are needed.

- A source with a feed: add `{"type": "rss", "url": "..."}` under `fetch`.
- A source without a feed: add `{"type": "links", "url": "<list page>", "link_regex": "<regex for article links>"}`. If the article address contains a date like `/2026/11/02/`, add a `date_regex` with named groups `y`, `m`, `d`.
- A national or general outlet: add a `keyword_regex` (copy the one on NC Newsline) so only election-related posts are kept.
- A site that is only a tool or tracker: set `"kind": "tool"` and it will appear in the trackers section.

## Run it yourself

```
python3 scripts/fetch_feeds.py          # fetch and update feed.json
python3 scripts/fetch_feeds.py --no-fetch   # refresh source details only
python3 -m unittest discover -s tests -v
```

## Known limits

- NC State Board of Elections and Common Cause NC do not have a feed address I could confirm. The script reads NCSBE's press release list directly, and tries a Common Cause feed address before falling back to reading its Updates page. Common Cause articles on that page have no dates, so those items show "first seen" times.
- The John Locke tracker loads its data with JavaScript, so it is a link only.
- GitHub can delay scheduled runs by several minutes, especially at busy times.
