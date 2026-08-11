# tools.ts entry for leefoot.com (apply to main, not the blog branch)

First tool for the existing 'AI Search' category. Copy into `src/data/tools.ts`
in `lee-single-page-site` once the tracker is deployed; fill in `appUrl` and
`githubUrl` with the final URLs. All copy below is source-verified against
`audit_robots.py` and `build_page.py`.

```ts
{
  name: 'AI Bot Blocking Tracker',
  slug: 'ai-bot-blocking-tracker',
  description: 'Daily tracker of UK and US news publishers blocking AI bots in robots.txt.',
  longDescription: 'Fetches the robots.txt of 1,000 registry-derived UK and US news publishers once a day and evaluates the rules for 14 AI bots using RFC 9309 longest-match semantics. For each publisher it shows whether the ChatGPT, Perplexity and Claude search crawlers are Open, restricted by path rules, or Blocked (the citing axis), the licensing stance across the eight training crawlers (the training axis), and the Ahrefs Domain Rating. Preset filters surface prime PR targets (high DR, open to all three engines), publishers that block training bots but spare GPTBot (the licensing-deal fingerprint), and walls that block everything. Includes CSV export, shareable filtered URLs, a live checker for any domain, and day-to-day change tracking from git-committed snapshots.',
  icon: 'Shield',
  color: 'cyan',
  category: 'AI Search',
  isNew: true,
  appUrl: 'TBC',
  githubUrl: 'TBC',
  features: [
    '1,000 registry-derived UK and US news publishers checked daily',
    'Per-engine citing status for ChatGPT, Perplexity and Claude, separate from training stance',
    'Ahrefs Domain Rating on every publisher with prime-target presets',
    'OpenAI-exception detection: every training bot blocked except GPTBot',
    'CSV export, copyable domain lists and shareable filtered URLs',
    'Live checker for any domain plus a day-to-day change log',
  ],
  inputs: ['None. The tracker runs on a registry-derived publisher list (Wikidata + homepages.news + publisher networks, Tranco-validated).'],
  output: 'A daily-updated table with per-publisher and per-bot verdicts, pattern badges, a change log, and latest.json for reuse.',
  useCases: [
    'Checking whether a publisher blocks training bots but allows citation bots before pitching digital PR',
    'Spotting licensing deals as they appear in robots.txt',
    'Tracking how AI blocking policy shifts across the news industry over time',
  ],
}
```

Notes for deploy:
- Repo: new public repo under the searchsolved account (Actions cron needs its
  own repo; do not fold into search-solved-public-seo).
- Hosting: GitHub Pages from /docs is zero-config with the included workflow;
  a custom domain (e.g. tracker.leefoot.com) can be added later without
  changing the workflow.
- The site repo is currently on the draft blog branch; make this edit on main.
