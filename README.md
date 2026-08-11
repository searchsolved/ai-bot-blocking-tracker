# AI Bot Blocking Tracker

Daily audit of which UK and US news publishers block AI bots in robots.txt,
split by what each bot actually does. Blocking a training bot is a licensing
position; blocking a search bot removes you from AI answers. No other public
tracker makes that distinction.

Live page: (pending deploy)

## What it tracks

1,000 UK and US news publishers checked daily against 14 bots in three
classes. The list is registry-derived, not hand-picked: candidates come from
Wikidata (newspapers and news websites with country and official-website
properties), the open-source homepages.news site registry, publisher-network
enumeration (Reach, Newsquest, National World and others), and an original
curated core; every candidate must appear in the Tranco top 1M ranked domain
list (which also kills dead and misremembered domains), an LLM classification
pass drops non-publishers (journals, aggregators, wires, defunct titles), and
the final 1,000 are the top-ranked survivors by Tranco rank. The pipeline
lives in `seed/`.

Bot classes:

- **Training** (a licensing decision): GPTBot, CCBot, ClaudeBot, anthropic-ai,
  Google-Extended, Applebot-Extended, Meta-ExternalAgent, Bytespider
- **AI search / index** (blocking these costs citations): OAI-SearchBot,
  Claude-SearchBot, PerplexityBot
- **User-triggered fetch**: ChatGPT-User, Claude-User, Perplexity-User

Per domain it also records the edge provider (Cloudflare, Fastly, CloudFront,
Akamai) and whether Cloudflare's managed robots.txt block has been injected.

## Engine-status model

The page reports each publisher's openness to three AI answer engines, derived
from the search crawler verdicts: **Blocked** (Disallow: / for that bot),
**Path rules** (an explicit AI-specific path restriction), **Open** (anything
else, including generic sitewide housekeeping inherited from the `*` group;
treating inherited partials as blocked-ish was the original design's biggest
false alarm). Unreadable domains (WAF 403s to our identified agent) render as
greyed rows marked Unknown rather than being dropped.

Signals: "Won't train, will cite" (4+ training bots blocked, no search bots),
"OpenAI exception" (4+ training bots blocked but GPTBot spared, the licensing
fingerprint), "Wall", "No AI rules", "Unreadable".

## Live checker (optional)

`worker/checker.js` is a Cloudflare Worker exposing `/check?domain=` (live
robots.txt parse with the same rules) and `/suggestions?min=N` (domains the
public has checked at least N times, with a TLD-based region guess, for
manual review; nothing is auto-added to `domains.txt`). Rate limiting is
three-layer: results are cached in KV for 10 minutes so bursts never re-fetch
a publisher's robots.txt, a best-effort per-IP throttle allows 10 checks per
minute, and at deploy time add a Cloudflare rate limiting rule on /check as
the hard backstop (one rule is included on the free plan). Deploy with wrangler
after creating the SUGGEST KV namespace (see `worker/wrangler.toml`), then
set the Worker URL as the `CHECKER_URL` repo secret (and locally in
`checker_url.txt`). The page only renders the checker box when the URL is
configured, so the static site works without it.

## Method and honest limits

- One GET per domain per day, to `/robots.txt` only, the file that exists to
  be fetched, with an identifying user agent.
- Rule evaluation follows RFC 9309 longest-match semantics: the group whose
  user-agent token is the longest prefix match for the bot wins; `*` is the
  fallback. Verdicts: blocked (Disallow: /), partial (some paths disallowed),
  allowed, or no rule.
- robots.txt is declared policy, not enforcement. A Cloudflare or WAF edge
  block is invisible from outside: spoofing a bot user agent proves nothing,
  because edge providers verify real crawlers by IP and signature and will
  challenge an unverified copy even where the real bot is allowed. This
  tracker therefore reports declared policy only, plus the edge provider so
  you know where silent enforcement is possible.
- Perplexity documents that Perplexity-User generally ignores robots.txt, so
  a block against it is a statement, not a control.

## Repo layout

- `audit_robots.py` - fetcher, parser, classifier (stdlib only)
- `domains.txt` - curated publisher list (edit freely; one domain per line)
- `data/YYYY-MM-DD.json` - daily snapshots (git history = change log)
- `data/dr.json` - Ahrefs Domain Rating enrichment, refreshed when older
  than 28 days (DR moves slowly; daily fetching would be pointless)
- `fetch_dr.py` - DR refresh via the free Ahrefs endpoint; needs
  `AHREFS_API_KEY` (repo secret in Actions). Verify the endpoint path with
  `--canary` on first run with a real key
- `build_page.py` - renders `docs/index.html` and `docs/latest.json` from the
  latest snapshot, diffs against the previous one, merges DR and favicons
- `.github/workflows/daily.yml` - daily cron: audit, DR refresh (when stale),
  rebuild page, commit

## Hosting

GitHub Actions runs the daily job; GitHub Pages serves `/docs`. Domain Rating
by [Ahrefs](https://ahrefs.com/) under their
[Domain Rating licence](https://ahrefs.com/legal/domain-rating-license);
favicons via Google's public favicon service.

## Deferred (needs history or new data)

"Recently opened to AI search" preset and per-row change-age flags (needs ~30
days of snapshots); stale-fallback for unreadable domains (render last
successful read with its date); aggregate trend chart; roster additions
(Amazonbot, DuckAssistBot, Meta-ExternalFetcher; Bingbot only ever as a note,
never a fourth engine column, since blocking Bingbot is a conventional-SEO
decision); GitHub-issue link for publisher suggestions.

## Running locally

```
python3 audit_robots.py domains.txt --out data/$(date +%F).json
python3 build_page.py
```

No dependencies beyond the Python standard library.
