#!/usr/bin/env python3
"""Render docs/index.html and docs/latest.json from the newest data snapshot.

Audience-first model: three AI answer engines (ChatGPT, Perplexity, Claude)
each get a declared-policy state per publisher, derived from the search bot
verdicts. Training bots are a separate licensing decision. Unreadable domains
(WAF 403s etc.) render as greyed rows rather than vanishing.

Engine state derivation:
  blocked                -> "blocked"   (Disallow: / for that bot)
  partial AND explicit   -> "limited"   (an AI-specific path rule exists)
  anything else          -> "open"      (allowed, no rule, or generic * housekeeping)

Stdlib only.
"""

import glob
import html
import json
import os
import re
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
DOCS = os.path.join(BASE, "docs")
LISTS = os.path.join(BASE, "verticals")

e = html.escape

# Tracked verticals. Each has its own domain list, snapshot series and page;
# the index page compares them. Order here is the order shown everywhere.
VERTICALS = [
    {"key": "news", "label": "News", "noun": "publishers", "noun_one": "publisher",
     "title": "news publishers",
     "blurb": "National, regional and digital-native news titles across the UK and US.",
     "purpose": "Built for digital PR and SEO teams deciding where a placement can actually earn AI citations."},
    {"key": "ecommerce", "label": "Ecommerce", "noun": "retailers", "noun_one": "retailer",
     "title": "ecommerce retailers",
     "blurb": "Online retailers and consumer brands selling physical products across the UK and US.",
     "purpose": "Built for retailers and their SEO teams checking whether their own storefront, and their competitors', can be read and recommended by AI assistants."},
]
VERTICAL_BY_KEY = {v["key"]: v for v in VERTICALS}

TRAINING = ["GPTBot", "CCBot", "ClaudeBot", "anthropic-ai", "Google-Extended",
            "Applebot-Extended", "Meta-ExternalAgent", "Bytespider"]
SEARCH = ["OAI-SearchBot", "Claude-SearchBot", "PerplexityBot"]
USERFETCH = ["ChatGPT-User", "Claude-User", "Perplexity-User"]
ALL_BOTS = TRAINING + SEARCH + USERFETCH

ENGINES = [("chatgpt", "ChatGPT", "OAI-SearchBot"),
           ("perplexity", "Perplexity", "PerplexityBot"),
           ("claude", "Claude", "Claude-SearchBot")]

STATE_LABEL = {"open": "Open", "limited": "Path rules", "blocked": "Blocked", "unknown": "Unknown"}
EDGE_LABEL = {"cloudflare": "Cloudflare", "fastly": "Fastly", "cloudfront": "CloudFront",
              "akamai": "Akamai", "vercel": "Vercel"}

SIGNALS = {
    "wtwc": ("Won't train, will cite", "Blocks 4 or more training bots but no AI search bots: the ideal pitch profile."),
    "exception": ("OpenAI exception", "Blocks 4 or more training bots by name but spares GPTBot, the fingerprint of an OpenAI licensing deal."),
    "wall": ("Wall", "Blocks every training bot and every AI search bot."),
    "norules": ("No AI rules", "robots.txt contains no AI-specific rules at all."),
    "unreadable": ("Unreadable", "The site refused our identified audit agent (usually a WAF 403), so declared policy cannot be read. That refusal is itself a signal."),
}


def load_snapshots(vertical):
    files = sorted(glob.glob(os.path.join(DATA, vertical, "????-??-??.json")))
    if not files:
        return None, None, None
    latest = json.load(open(files[-1]))
    prev = json.load(open(files[-2])) if len(files) > 1 else None
    return latest, prev, os.path.basename(files[-1])[:-5]


def load_directory(vertical):
    """verticals/<key>.txt: 'domain | Display Name' with # region section comments."""
    names, regions, current = {}, {}, "UK"
    for line in open(os.path.join(LISTS, f"{vertical}.txt")):
        line = line.strip()
        if line.startswith("#"):
            if re.search(r"\bUS\b", line):
                current = "US"
            elif re.search(r"\bUK\b", line):
                current = "UK"
        elif line:
            parts = [p.strip() for p in line.split("|", 1)]
            d = parts[0].lower()
            names[d] = parts[1] if len(parts) > 1 and parts[1] else d
            regions[d] = current
    return names, regions


def load_dr():
    path = os.path.join(DATA, "dr.json")
    if not os.path.exists(path):
        return {}, None
    payload = json.load(open(path))
    return payload.get("ratings", {}), payload.get("fetched")


def checker_url():
    """Live checker Worker URL: env CHECKER_URL, or checker_url.txt, else None."""
    url = os.environ.get("CHECKER_URL", "").strip()
    if not url:
        path = os.path.join(BASE, "checker_url.txt")
        if os.path.exists(path):
            url = open(path).read().strip()
    return url.rstrip("/") or None


def engine_state(r, bot):
    v = r["bots"].get(bot)
    if not v:
        return "unknown"
    if v["verdict"] == "blocked":
        return "blocked"
    if v["verdict"] == "partial" and v["explicit"]:
        return "limited"
    return "open"


def blocked_count(r, bots):
    return sum(1 for b in bots if r["bots"].get(b, {}).get("verdict") == "blocked")


def train_stance(count):
    if count == len(TRAINING):
        return "Blocks all"
    if count >= 4:
        return "Blocks most"
    if count >= 1:
        return "Blocks some"
    return "Allows all"


def signals_for(r):
    out = []
    t = blocked_count(r, TRAINING)
    states = [engine_state(r, bot) for _, _, bot in ENGINES]
    blocked_engines = sum(1 for s in states if s == "blocked")
    gpt_blocked = r["bots"].get("GPTBot", {}).get("verdict") == "blocked"
    if t >= 4 and blocked_engines == 0:
        out.append("wtwc")
    if t >= 4 and not gpt_blocked:
        out.append("exception")
    if t == len(TRAINING) and blocked_engines == len(ENGINES):
        out.append("wall")
    if not any(v.get("explicit") for v in r["bots"].values()):
        out.append("norules")
    return out


def diff(latest, prev):
    """Day-over-day changes in *effective* state, not raw verdict.

    'allowed' and 'no-rule' both mean not blocked, and a partial rule that
    matched via the generic * group is not an AI decision, so comparing raw
    verdicts reports cosmetic robots.txt edits as policy changes. Comparing
    the state the page actually shows keeps the feed to real movements.
    """
    if not prev:
        return {}
    # Sites serve different robots.txt by IP reputation, so a snapshot taken
    # from a CI runner is not comparable with one taken from a laptop.
    if latest.get("collector") != prev.get("collector"):
        return {}
    prev_by = {r["domain"]: r for r in prev["results"]}
    by_domain = {}
    for r in latest["results"]:
        p = prev_by.get(r["domain"])
        if not p or r["error"] or p["error"]:
            continue
        for bot in ALL_BOTS:
            if bot not in r["bots"] or bot not in p["bots"]:
                continue
            a, b = engine_state(p, bot), engine_state(r, bot)
            if a != b:
                direction = "closed" if b == "blocked" else ("opened" if a == "blocked" else "changed")
                by_domain.setdefault(r["domain"], []).append(
                    (bot, STATE_LABEL[a].lower(), STATE_LABEL[b].lower(), direction))
    return by_domain


def bot_chip(r, bot):
    v = r["bots"].get(bot, {})
    verdict = v.get("verdict", "unknown")
    cls = {"blocked": "s-blocked", "partial": "s-limited", "allowed": "s-open", "no-rule": "s-open"}.get(verdict, "s-unknown")
    word = {"blocked": "Blocked", "partial": "Paths", "allowed": "Open", "no-rule": "Open"}.get(verdict, "?")
    paths = v.get("paths") or []
    tip = ""
    if verdict == "partial" and paths:
        shown = ", ".join(paths[:3]) + ("..." if len(paths) > 3 else "")
        tip = f' title="Disallow: {e(shown)}"'
    return f'<span class="bot"><span class="chip {cls}"{tip}>{word}</span> {e(bot)}</span>'


CSS = """
/* leefoot.com design system v4: light-only, Inter, orange accent */
:root {
  --paper:#FFFFFF; --ink:#1A1A1A; --muted:#5F5F5F; --line:#ECECEC; --accent:#E8623D;
  --accent-hover:#D4552F; --accent-light:#FDEEE8; --card:#FAFAFA; --inset:#F5F5F5;
  --good-bg:#E4F2E7; --good-fg:#1C6B3C; --warn-bg:#F8EED3; --warn-fg:#7A5510;
  --bad-bg:#F8E0DA; --bad-fg:#9C3018; --neut-bg:#F0F0F0; --neut-fg:#5F5F5F;
}
* { box-sizing:border-box }
body { margin:0; background:var(--paper); color:var(--ink);
  font:15.5px/1.6 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased }
.topbar-outer { border-bottom:1px solid var(--line) }
.topbar { max-width:1100px; margin:0 auto; padding:16px 20px; display:flex; align-items:center;
  justify-content:space-between; gap:16px }
.wordmark { font-weight:800; font-size:1.15rem; letter-spacing:-0.02em; color:var(--ink) }
.wordmark .dot { color:var(--accent) }
.navlinks { display:flex; align-items:center; gap:22px }
.navlinks a { color:var(--muted); font-weight:500; font-size:.92rem }
.navlinks a:hover { color:var(--ink) }
.navlinks a.cta { background:var(--accent); color:#fff; font-weight:600; padding:9px 18px;
  border-radius:12px; box-shadow:0 8px 20px -8px rgba(232,98,61,.5); transition:all .18s ease }
.navlinks a.cta:hover { background:var(--accent-hover); transform:translateY(-2px) }
.wrap { max-width:1100px; margin:0 auto; padding:44px 20px 64px }
h1,h2 { font-family:Inter,system-ui,sans-serif; font-weight:700; letter-spacing:-0.02em; text-wrap:balance }
h1 { font-size:2.2rem; line-height:1.15; margin:6px 0 10px }
h2 { font-size:1.35rem; margin:38px 0 10px }
.eyebrow { font-size:.72rem; letter-spacing:.14em; text-transform:uppercase; color:var(--accent); font-weight:600 }
.standfirst { color:var(--muted); max-width:72ch; margin:0 0 10px }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:22px 0 6px }
.tile { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:14px 16px }
.tile .n { font-size:1.9rem; font-weight:700; font-variant-numeric:tabular-nums; line-height:1.1 }
.tile .l { font-size:.8rem; color:var(--muted); margin-top:2px }
.tilenote { font-size:.78rem; color:var(--muted); margin:4px 0 0 }
.howto { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:10px 16px; margin:16px 0 }
.howto summary { cursor:pointer; font-weight:600; color:var(--accent) }
.howto ul { margin:8px 0 4px; padding-left:20px; max-width:80ch }
.howto li { margin:4px 0; font-size:.9rem }
.presets { display:flex; flex-wrap:wrap; gap:8px; margin:14px 0 8px }
.preset { font:inherit; font-size:.82rem; font-weight:600; color:var(--ink); background:var(--card);
  border:1px solid var(--line); border-radius:12px; padding:7px 15px; cursor:pointer; transition:all .18s ease }
.preset:hover { background:var(--accent-light); border-color:var(--accent); color:var(--accent-hover) }
.controls { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin:8px 0 12px }
.controls label { font-size:.82rem; color:var(--muted); display:flex; gap:5px; align-items:center }
input[type=search],select { font:inherit; color:var(--ink); background:var(--paper); border:1px solid var(--line);
  border-radius:10px; padding:8px 12px }
.count { font-size:.85rem; color:var(--muted); font-variant-numeric:tabular-nums }
.actions { margin-left:auto; display:flex; gap:8px }
.actions button { font:inherit; font-size:.82rem; font-weight:600; color:var(--ink); background:var(--card);
  border:1px solid var(--line); border-radius:10px; padding:7px 14px; cursor:pointer; transition:all .18s ease }
.actions button:hover { border-color:var(--ink); transform:translateY(-1px) }
.tablewrap { overflow:auto; max-height:80vh; border:1px solid var(--line); border-radius:14px; background:var(--card) }
table { border-collapse:separate; border-spacing:0; width:100%; font-size:.9rem }
thead th { position:sticky; background:var(--card); text-align:left; font-size:.68rem; letter-spacing:.09em;
  text-transform:uppercase; color:var(--muted); padding:8px 10px; border-bottom:2px solid var(--line);
  white-space:nowrap; z-index:2 }
thead tr:first-child th { top:0; height:30px }
thead tr:last-child th { top:30px }
th.group { text-align:center; border-bottom:1px solid var(--line); color:var(--accent) }
th[data-sort] { cursor:pointer; user-select:none }
th[data-sort]::after { content:""; display:inline-block; width:1em; color:var(--accent) }
th[data-sort].asc::after { content:"\\2191" }
th[data-sort].desc::after { content:"\\2193" }
td { padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; font-variant-numeric:tabular-nums }
tr.main { cursor:pointer }
tr.main:hover td { background:var(--inset) }
tr.main.unreadable td { opacity:.55 }
tr.detail { display:none }
tr.detail td { background:var(--inset) }
.pub .name { font-weight:600 }
.pub .dom { color:var(--muted); font-size:.78rem }
.fav { vertical-align:-3px; margin-right:8px; border-radius:3px }
.botgroups { display:flex; flex-direction:column; gap:8px; padding:4px 0; max-width:100ch }
.botgroup b { font-size:.72rem; letter-spacing:.09em; text-transform:uppercase; color:var(--accent); margin-right:8px }
.botgroup .note { color:var(--muted); font-size:.78rem }
.bots { display:inline-flex; flex-wrap:wrap; gap:5px 13px }
.bot { font-size:.8rem; color:var(--muted); white-space:nowrap }
.meta { color:var(--muted); font-size:.82rem }
.meta a { color:var(--accent) }
.chip,.badge { display:inline-block; font-size:.7rem; font-weight:600; padding:1px 8px; border-radius:99px; white-space:nowrap }
.s-open { background:var(--good-bg); color:var(--good-fg) }
.s-limited { background:var(--warn-bg); color:var(--warn-fg) }
.s-blocked { background:var(--bad-bg); color:var(--bad-fg) }
.s-unknown { background:var(--neut-bg); color:var(--neut-fg) }
.t-stance { background:var(--neut-bg); color:var(--neut-fg) }
.badge { margin:1px 2px 1px 0 }
.b-wtwc { background:var(--good-bg); color:var(--good-fg) }
.b-exception { background:var(--warn-bg); color:var(--warn-fg) }
.b-wall { background:var(--bad-bg); color:var(--bad-fg) }
.b-norules { background:var(--neut-bg); color:var(--neut-fg) }
.b-unreadable { background:var(--neut-bg); color:var(--neut-fg) }
.sigcell { white-space:normal; min-width:150px }
.vtabs { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 16px }
.vtab { font-size:.85rem; font-weight:600; color:var(--muted); background:var(--card);
  border:1px solid var(--line); border-radius:12px; padding:7px 15px; transition:all .18s ease }
.vtab:hover { border-color:var(--ink); color:var(--ink); text-decoration:none }
.vtab.on { background:var(--accent); border-color:var(--accent); color:#fff }
.legend { display:flex; gap:16px; flex-wrap:wrap; margin:0 0 12px; font-size:.85rem; color:var(--muted) }
.legend span { display:flex; align-items:center; gap:6px }
.swatch { width:12px; height:12px; border-radius:3px; display:inline-block }
.cmprow { display:grid; grid-template-columns:240px 1fr; align-items:center; gap:14px; padding:9px 0;
  border-bottom:1px solid var(--line) }
.cmprow .barlabel { white-space:normal; overflow:visible; line-height:1.35 }
@media (max-width:640px) { .cmprow { grid-template-columns:1fr } }
.cmprow:last-child { border-bottom:0 }
.cmpbars { display:flex; flex-direction:column; gap:4px }
.cmpbar { display:grid; grid-template-columns:1fr 44px; align-items:center; gap:10px }
.cmpbar .track { height:12px; background:var(--inset); border-radius:6px; overflow:hidden }
.cmpbar .fill { display:block; height:100%; border-radius:0 5px 5px 0; min-width:2px }
.cmpbar .val { font-size:.8rem; color:var(--muted); font-variant-numeric:tabular-nums; text-align:right }
.gap { font-weight:700; font-variant-numeric:tabular-nums }
.barchart { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:18px 22px }
.bargroup h3 { font-size:.78rem; letter-spacing:.09em; text-transform:uppercase; color:var(--accent);
  margin:14px 0 8px; font-weight:700 }
.bargroup:first-child h3 { margin-top:2px }
.bargroup h3 .note { color:var(--muted); text-transform:none; letter-spacing:0; font-weight:500 }
.barrow { display:grid; grid-template-columns:170px 1fr 44px; align-items:center; gap:12px; padding:3px 0 }
.barlabel { font-size:.84rem; color:var(--ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
.bartrack { display:block; height:10px; background:var(--inset); border-radius:5px; overflow:hidden }
.bar { display:block; height:100%; background:var(--accent); border-radius:0 4px 4px 0; min-width:2px }
.barval { font-size:.82rem; color:var(--muted); font-variant-numeric:tabular-nums; text-align:right }
.contrast td:nth-child(2), .contrast td:nth-child(3), .contrast th:nth-child(2), .contrast th:nth-child(3) { text-align:right }
ul { max-width:78ch }
.changes li { margin:3px 0 }
footer { margin-top:44px; color:var(--muted); font-size:.85rem; border-top:1px solid var(--line); padding-top:16px }
a { color:inherit; text-decoration:none }
.wrap a { color:var(--accent) }
.wrap a:hover { text-decoration:underline }
.wrap .preset, .wrap .actions button { text-decoration:none }
button:focus-visible, summary:focus-visible, a:focus-visible, th:focus-visible { outline:2px solid var(--accent); outline-offset:2px }
@media (max-width:720px) {
  .hide-m { display:none }
  h1 { font-size:1.7rem }
  .actions { margin-left:0 }
}
"""

JS = """
const t = document.getElementById('t');
const body = t.tBodies[0];
const rows = [...body.querySelectorAll('tr.main')];
const controls = {
  q: document.getElementById('q'),
  region: document.getElementById('region'),
  dr: document.getElementById('drmin'),
  sig: document.getElementById('sig'),
  eng: { chatgpt: document.getElementById('e-chatgpt'),
         perplexity: document.getElementById('e-perplexity'),
         claude: document.getElementById('e-claude') },
};
const countEl = document.getElementById('count');
let sort = { key: 'open', dir: 'desc' };
const RANK = { open: 2, limited: 1, blocked: 0, unknown: -1 };

function rowVisible(r) {
  const d = r.dataset;
  const q = controls.q.value.toLowerCase().trim();
  if (q && !d.domain.includes(q) && !d.name.includes(q)) return false;
  if (controls.region.value && d.region !== controls.region.value) return false;
  const min = parseInt(controls.dr.value || '0', 10);
  if (min && (parseFloat(d.dr) || 0) < min) return false;
  for (const [k, box] of Object.entries(controls.eng)) {
    if (box.checked && (d[k] === 'blocked' || d[k] === 'unknown')) return false;
  }
  if (controls.sig.value && !d.signals.split(',').includes(controls.sig.value)) return false;
  return true;
}

function apply(push) {
  let shown = 0;
  for (const r of rows) {
    const ok = rowVisible(r);
    r.style.display = ok ? '' : 'none';
    const det = r.nextElementSibling;
    if (!ok) det.classList.remove('open');
    det.style.display = ok && det.classList.contains('open') ? 'table-row' : 'none';
    if (ok) shown++;
  }
  countEl.textContent = 'Showing ' + shown + ' of ' + rows.length;
  if (push !== false) syncUrl();
}

function syncUrl() {
  const p = new URLSearchParams();
  if (controls.q.value) p.set('q', controls.q.value);
  if (controls.region.value) p.set('region', controls.region.value);
  if (controls.dr.value) p.set('dr', controls.dr.value);
  if (controls.sig.value) p.set('sig', controls.sig.value);
  const eng = Object.entries(controls.eng).filter(([, b]) => b.checked).map(([k]) => k);
  if (eng.length) p.set('e', eng.join(','));
  if (sort.key !== 'open' || sort.dir !== 'desc') { p.set('sort', sort.key); p.set('dir', sort.dir); }
  const qs = p.toString();
  history.replaceState(null, '', qs ? '?' + qs : location.pathname + location.hash);
}

function readUrl() {
  const p = new URLSearchParams(location.search);
  controls.q.value = p.get('q') || '';
  controls.region.value = p.get('region') || '';
  controls.dr.value = p.get('dr') || '';
  controls.sig.value = p.get('sig') || '';
  const eng = (p.get('e') || '').split(',');
  for (const [k, box] of Object.entries(controls.eng)) box.checked = eng.includes(k);
  if (p.get('sort')) sort = { key: p.get('sort'), dir: p.get('dir') || 'desc' };
}

function keyValue(r, key) {
  const d = r.dataset;
  if (key === 'name') return d.name;
  if (key === 'region') return d.region;
  if (key === 'dr') return parseFloat(d.dr) || -1;
  if (key === 'training') return parseInt(d.training, 10);
  if (key === 'open') return parseInt(d.open, 10) * 100 + (parseFloat(d.dr) || 0);
  if (RANK[d[key]] !== undefined) return RANK[d[key]];
  return 0;
}

function applySort() {
  const dirMul = sort.dir === 'asc' ? 1 : -1;
  const sorted = [...rows].sort((a, b) => {
    const av = keyValue(a, sort.key), bv = keyValue(b, sort.key);
    const cmp = typeof av === 'string' ? av.localeCompare(bv) : av - bv;
    return cmp * dirMul || a.dataset.name.localeCompare(b.dataset.name);
  });
  for (const r of sorted) {
    body.appendChild(r);
    const det = document.getElementById('det-' + r.dataset.domain);
    if (det) body.appendChild(det);
  }
  for (const th of t.tHead.querySelectorAll('th[data-sort]')) {
    th.classList.remove('asc', 'desc');
    th.removeAttribute('aria-sort');
    if (th.dataset.sort === sort.key) {
      th.classList.add(sort.dir);
      th.setAttribute('aria-sort', sort.dir === 'asc' ? 'ascending' : 'descending');
    }
  }
}

t.tHead.addEventListener('click', ev => {
  const th = ev.target.closest('th[data-sort]');
  if (!th) return;
  const key = th.dataset.sort;
  if (sort.key === key) sort.dir = sort.dir === 'asc' ? 'desc' : 'asc';
  else sort = { key, dir: key === 'name' || key === 'region' ? 'asc' : 'desc' };
  applySort(); syncUrl();
});

body.addEventListener('click', ev => {
  if (ev.target.closest('a')) return;
  const row = ev.target.closest('tr.main');
  if (!row) return;
  const det = row.nextElementSibling;
  det.classList.toggle('open');
  det.style.display = det.classList.contains('open') ? 'table-row' : 'none';
});

for (const el of [controls.q, controls.region, controls.dr, controls.sig,
                  controls.eng.chatgpt, controls.eng.perplexity, controls.eng.claude]) {
  el.addEventListener(el.type === 'search' ? 'input' : 'change', () => apply());
}

const PRESETS = {
  prime: { dr: '85', eng: ['chatgpt', 'perplexity', 'claude'] },
  chatgpt: { eng: ['chatgpt'] },
  perplexity: { eng: ['perplexity'] },
  wtwc: { sig: 'wtwc' },
  norules: { sig: 'norules' },
  wall: { sig: 'wall' },
};
document.querySelectorAll('.preset').forEach(btn => btn.addEventListener('click', () => {
  const p = PRESETS[btn.dataset.preset] || {};
  controls.q.value = ''; controls.region.value = '';
  controls.dr.value = p.dr || ''; controls.sig.value = p.sig || '';
  for (const [k, box] of Object.entries(controls.eng)) box.checked = (p.eng || []).includes(k);
  apply();
}));
document.getElementById('reset').addEventListener('click', () => {
  controls.q.value = ''; controls.region.value = ''; controls.dr.value = ''; controls.sig.value = '';
  for (const box of Object.values(controls.eng)) box.checked = false;
  sort = { key: 'open', dir: 'desc' }; applySort(); apply();
});

function visibleRows() { return rows.filter(r => r.style.display !== 'none'); }
document.getElementById('csv').addEventListener('click', () => {
  const head = ['name','domain','region','dr','chatgpt','perplexity','claude','training_blocked','userfetch_blocked','signals','edge','status','checked'];
  const esc = v => '"' + String(v).replaceAll('"', '""') + '"';
  const lines = [head.join(',')];
  for (const r of visibleRows()) {
    const d = r.dataset;
    lines.push([d.fullname, d.domain, d.region, d.dr, d.chatgpt, d.perplexity, d.claude,
                d.training, d.userfetch, d.signals, d.edge, d.status, d.checked].map(esc).join(','));
  }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([lines.join('\\n')], { type: 'text/csv' }));
  a.download = 'ai-bot-blocking-' + document.body.dataset.date + '.csv';
  a.click(); URL.revokeObjectURL(a.href);
});
document.getElementById('copy').addEventListener('click', ev => {
  const doms = visibleRows().map(r => r.dataset.domain).join('\\n');
  navigator.clipboard.writeText(doms).then(() => {
    ev.target.textContent = 'Copied';
    setTimeout(() => { ev.target.textContent = 'Copy domains'; }, 1500);
  });
});

readUrl(); applySort(); apply(false);
if (location.hash.startsWith('#d-')) {
  const dom = location.hash.slice(3);
  const row = rows.find(r => r.dataset.domain === dom);
  if (row) {
    const det = row.nextElementSibling;
    det.classList.add('open'); det.style.display = 'table-row';
    row.scrollIntoView({ block: 'center' });
  }
}
"""

CHECKER_JS = """
const CHECKER_URL = document.body.dataset.checker;
const checkForm = document.getElementById('checkform');
if (checkForm && CHECKER_URL) {
  const escT = s => { const d = document.createElement('span'); d.textContent = s; return d.innerHTML; };
  checkForm.addEventListener('submit', async ev => {
    ev.preventDefault();
    const out = document.getElementById('checkresult');
    let dom = document.getElementById('checkdomain').value.trim().toLowerCase()
      .replace(/^https?:\\/\\//, '').replace(/^www\\./, '').split('/')[0];
    if (!dom) return;
    out.textContent = 'Checking ' + dom + '...';
    try {
      const resp = await fetch(CHECKER_URL + '/check?domain=' + encodeURIComponent(dom));
      const j = await resp.json();
      if (j.error) {
        out.innerHTML = '<span class="chip s-unknown">Unreadable</span> ' + escT(j.error) +
          (j.note ? ' <span class="meta">' + escT(j.note) + '</span>' : '');
        return;
      }
      const label = { open: 'Open', limited: 'Path rules', blocked: 'Blocked' };
      const nameFor = { chatgpt: 'ChatGPT', perplexity: 'Perplexity', claude: 'Claude' };
      let htmlOut = '';
      for (const [k, info] of Object.entries(j.engines)) {
        htmlOut += '<span class="bot"><span class="chip s-' + info.state + '">' + label[info.state] +
          '</span> ' + nameFor[k] + '</span> ';
      }
      htmlOut += '<span class="meta">Training bots blocked: ' + j.training.blocked + '/' + j.training.total + '.</span>';
      const tracked = rows.find(r => r.dataset.domain === j.domain);
      if (tracked) htmlOut += ' <a href="#d-' + escT(j.domain) + '">In the daily tracker</a>';
      out.innerHTML = htmlOut;
    } catch (ex) {
      out.textContent = 'Checker unavailable right now.';
    }
  });
}
"""


def build_vertical(v, dr, dr_date, checker):
    """Render docs/<key>.html for one vertical. Returns summary stats for the index."""
    vkey, noun, noun_one = v["key"], v["noun"], v["noun_one"]
    latest, prev, date = load_snapshots(vkey)
    if latest is None:
        print(f"  {vkey}: no snapshots yet, skipping")
        return None
    names, regions = load_directory(vkey)
    tabs_html = "".join(
        f'<a class="vtab{" on" if o["key"] == vkey else ""}" href="{o["key"]}.html">{e(o["label"])}</a>'
        for o in VERTICALS if os.path.exists(os.path.join(DATA, o["key"]))) + \
        '<a class="vtab" href="index.html">Compare</a>'
    all_results = latest["results"]
    for r in all_results:
        d = r["domain"]
        r["region"] = regions.get(d, "?")
        r["name"] = names.get(d, d)
        r["dr"] = dr.get(d)
    readable = [r for r in all_results if not r["error"]]
    unreadable = [r for r in all_results if r["error"]]
    n = len(readable)
    total = len(all_results)
    changes = diff(latest, prev)

    def states(r):
        return {key: engine_state(r, bot) for key, _, bot in ENGINES}

    open3 = sum(1 for r in readable if all(s != "blocked" for s in states(r).values()))
    closed3 = sum(1 for r in readable if all(s == "blocked" for s in states(r).values()))
    prime = sum(1 for r in readable
                if all(s != "blocked" for s in states(r).values())
                and isinstance(r["dr"], (int, float)) and r["dr"] >= 85)
    exception = sum(1 for r in readable if "exception" in signals_for(r))

    os.makedirs(DOCS, exist_ok=True)
    json.dump(latest, open(os.path.join(DOCS, f"{vkey}-latest.json"), "w"), indent=1)

    # ---- publisher rows ----
    def sort_key(r):
        st = states(r) if not r["error"] else {}
        openc = sum(1 for s in st.values() if s != "blocked") if not r["error"] else -1
        return (-openc, -(r["dr"] or 0), r["name"].lower())

    rowparts = []
    for r in sorted(all_results, key=sort_key):
        d, name = r["domain"], r["name"]
        err = bool(r["error"])
        st = states(r) if not err else {k: "unknown" for k, _, _ in ENGINES}
        t_cnt = blocked_count(r, TRAINING) if not err else -1
        u_cnt = blocked_count(r, USERFETCH) if not err else -1
        openc = sum(1 for s in st.values() if s != "blocked") if not err else -1
        sigs = signals_for(r) if not err else ["unreadable"]
        rdr = r["dr"] if isinstance(r["dr"], (int, float)) else ""
        edge = EDGE_LABEL.get(r.get("edge", ""), "Other") if not err else ""

        fav = (f'<img class="fav" src="https://www.google.com/s2/favicons?domain={e(d)}&amp;sz=32"'
               f' width="16" height="16" loading="lazy" alt="" onerror="this.style.visibility=\'hidden\'">')
        engine_cells = "".join(
            f'<td><span class="chip s-{st[key]}">{STATE_LABEL[st[key]]}</span></td>'
            for key, _, _ in ENGINES)
        badge_html = " ".join(
            f'<span class="badge b-{k}" title="{e(SIGNALS[k][1])}">{e(SIGNALS[k][0])}</span>'
            for k in sigs if k in ("exception", "norules", "unreadable"))
        if err:
            tr_cell = '<span class="chip s-unknown">Unknown</span>'
        else:
            tr_cell = (f'<span class="chip t-stance" title="{t_cnt} of {len(TRAINING)} training bots fully blocked">'
                       f'{train_stance(t_cnt)}</span>')

        data_attrs = (f'data-domain="{e(d)}" data-name="{e(name.lower())}" data-fullname="{e(name)}"'
                      f' data-region="{r["region"]}" data-dr="{rdr}"'
                      + "".join(f' data-{k}="{st[k]}"' for k, _, _ in ENGINES)
                      + f' data-open="{openc}" data-training="{t_cnt}" data-userfetch="{u_cnt}"'
                      f' data-signals="{",".join(sigs)}" data-edge="{e(edge)}"'
                      f' data-status="{"unreadable" if err else "ok"}" data-checked="{e(date)}"')

        # detail row
        if err:
            detail = (f'<div class="botgroups"><div class="meta">Could not read robots.txt: {e(r["error"])}. '
                      'The edge refused our identified audit agent, so declared policy is unreadable from outside. '
                      f'<a href="https://{e(d)}/robots.txt" rel="noopener">Try the live robots.txt</a>.</div></div>')
        else:
            search_bots = "".join(bot_chip(r, b) for b in SEARCH)
            train_bots = "".join(bot_chip(r, b) for b in TRAINING)
            user_bots = "".join(bot_chip(r, b) for b in USERFETCH)
            cfnote = ' Cloudflare managed robots.txt block present.' if r.get("cloudflare_managed") else ''
            detail = f'''<div class="botgroups">
<div class="botgroup"><b>AI search (citations)</b><span class="bots">{search_bots}</span></div>
<div class="botgroup"><b>Training (licensing)</b><span class="bots">{train_bots}</span></div>
<div class="botgroup"><b>User fetch (on demand)</b><span class="bots">{user_bots}</span> <span class="note">Perplexity-User generally ignores robots.txt, so a block against it is a statement, not a control.</span></div>
<div class="meta">Edge: {e(edge)}.{cfnote} <a href="https://{e(d)}/robots.txt" rel="noopener">View live robots.txt</a> &middot; <a href="#d-{e(d)}">Link to this row</a></div>
</div>'''

        rowparts.append(f'''<tr class="main{' unreadable' if err else ''}" {data_attrs}>
<td class="pub">{fav}<span class="name">{e(name)}</span> <span class="dom">{e(d)}</span></td>
<td class="hide-m">{r['region']}</td>
<td>{f'{rdr:g}' if rdr != '' else ''}</td>
{engine_cells}
<td class="hide-m">{tr_cell}</td>
<td class="sigcell hide-m">{badge_html}</td></tr>
<tr class="detail" id="det-{e(d)}"><td colspan="9">{detail}</td></tr>''')

    # ---- per-bot blocking bars, grouped by class ----
    bar_groups = []
    for cls_name, cls_note, bots in (
            ("AI search crawlers", "blocking these costs citations", SEARCH),
            ("Training crawlers", "a licensing decision", TRAINING),
            ("User fetchers", "on-demand page reads", USERFETCH)):
        bars = []
        for b in sorted(bots, key=lambda x: -sum(1 for r in readable if r["bots"][x]["verdict"] == "blocked")):
            cnt = sum(1 for r in readable if r["bots"][b]["verdict"] == "blocked")
            pct = cnt * 100 // n
            bars.append(f'''<div class="barrow" title="{cnt} of {n} readable publishers fully block {e(b)}">
<span class="barlabel">{e(b)}</span>
<span class="bartrack"><span class="bar" style="width:{pct}%"></span></span>
<span class="barval">{pct}%</span></div>''')
        bar_groups.append(f'<div class="bargroup"><h3>{cls_name} <span class="note">{cls_note}</span></h3>{"".join(bars)}</div>')

    # ---- UK vs US summary contrast ----
    def region_share(region, pred):
        pool = [r for r in readable if r["region"] == region]
        return (sum(1 for r in pool if pred(r)) * 100 // len(pool)) if pool else 0

    contrasts = []
    for label, pred in (
            ("Block at least one training bot", lambda r: blocked_count(r, TRAINING) > 0),
            ("Block at least one AI search crawler", lambda r: any(engine_state(r, b) == "blocked" for _, _, b in ENGINES)),
            ("No AI rules at all", lambda r: "norules" in signals_for(r))):
        uk, us = region_share("UK", pred), region_share("US", pred)
        contrasts.append(f'<tr><td>{e(label)}</td><td>{uk}%</td><td>{us}%</td></tr>')

    # ---- change feed ----
    if prev:
        ordered = sorted(changes.items(),
                         key=lambda kv: (0 if any(bot in SEARCH for bot, *_ in kv[1]) else 1, kv[0]))
        items = []
        for dom, evs in ordered[:25]:
            evs_html = "; ".join(f'{e(bot)} {e(a)} to {e(b)} ({dirn})' for bot, a, b, dirn in evs)
            items.append(f'<li><b>{e(names.get(dom, dom))}</b>: {evs_html}</li>')
        more = len(ordered) - 25
        if more > 0:
            items.append(f'<li>and {more} more domains in <a href="{vkey}-latest.json">the snapshot</a></li>')
        changes_html = "".join(items) or "<li>No changes against the previous snapshot.</li>"
        if latest.get("collector") != prev.get("collector"):
            changes_html = ("<li>The previous snapshot was collected from a different network "
                            "vantage point, and sites serve different robots.txt by IP reputation, "
                            "so the two are not compared. Change tracking resumes with the next "
                            "snapshot collected the same way.</li>")
    else:
        changes_html = "<li>First snapshot; changes appear from the second run.</li>"

    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Who Blocks AI Bots? {total} UK and US {v["title"]}, tracked daily</title>
<meta name="description" content="Daily robots.txt audit of {total} UK and US {v["title"]}: which AI answer engines each one is open to, which training bots they block, with Domain Rating, filters and CSV export.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body data-date="{e(date)}"{f' data-checker="{e(checker)}"' if checker else ''}>
<div class="topbar-outer"><nav class="topbar">
<a class="wordmark" href="https://leefoot.com">Lee Foot<span class="dot">.</span></a>
<div class="navlinks"><a href="https://leefoot.com/tools">Tools</a><a href="https://leefoot.com/services">Services</a><a class="cta" href="https://leefoot.com/contact">Book a Call</a></div>
</nav></div>
<div class="wrap">
<div class="eyebrow">{e(v["label"])}. Updated daily, last run {e(date)}</div>
<h1>Who blocks AI bots?</h1>
<div class="vtabs">{tabs_html}</div>
<p class="standfirst">A daily robots.txt audit of {total} UK and US {v["title"]} ({n} readable today). For each one: which AI answer engines its content is open to (declared policy for the ChatGPT, Perplexity and Claude search crawlers), which training bots it blocks, and its Ahrefs Domain Rating. {v["purpose"]} Blocking a training bot is a licensing position; blocking a search bot removes you from AI answers.</p>

<div class="tiles">
<div class="tile"><div class="n">{open3}</div><div class="l">open to all three AI answer engines</div></div>
<div class="tile"><div class="n">{prime}</div><div class="l">DR 85+ and open to all three</div></div>
<div class="tile"><div class="n">{closed3}</div><div class="l">closed to all three</div></div>
<div class="tile"><div class="n">{exception}</div><div class="l">block training bots but spare GPTBot</div></div>
</div>
<p class="tilenote">As of {e(date)}. {n} of {total} tracked {noun} readable. Cite as: AI Bot Blocking Tracker, Lee Foot, {e(date)}.</p>

<details class="howto" open><summary>How to read this</summary><ul>
<li>This is <b>declared policy</b> (robots.txt), not enforcement. Edge firewalls can block silently, and Open does not guarantee citations; {noun} blocking search bots still get cited sometimes via retrieval from search results.</li>
<li><b>Open</b> means the engine's search crawler is not blocked. <b>Path rules</b> means an AI-specific path restriction exists (hover the chips in the detail row for the paths). Generic sitewide housekeeping rules are treated as Open. The <b>Training bots</b> column is the separate licensing stance across the eight training crawlers; it does not affect whether a placement can be cited.</li>
<li><b>Google AI Overviews and AI Mode are not in this data.</b> Their eligibility follows ordinary Googlebot indexing, which robots rules for AI bots do not affect. Google-Extended governs Gemini training only.</li>
</ul></details>

{f"""<h2>Check any site</h2>
<p class="standfirst">Not in the list? Check any domain's declared AI bot policy live. Frequently checked domains go into the review queue for tracking.</p>
<form id="checkform" class="controls" style="margin-top:0">
<input id="checkdomain" type="search" placeholder="example.com" aria-label="Domain to check">
<button class="preset" type="submit">Check robots.txt</button>
<span id="checkresult" class="meta" role="status"></span>
</form>""" if checker else ""}

<h2>{noun.capitalize()}</h2>
<div class="presets">
<button class="preset" data-preset="prime">Prime targets: DR 85+, open to all three</button>
<button class="preset" data-preset="chatgpt">Open to ChatGPT</button>
<button class="preset" data-preset="perplexity">Open to Perplexity</button>
<button class="preset" data-preset="wtwc">Won't train, will cite</button>
<button class="preset" data-preset="norules">No AI rules</button>
<button class="preset" data-preset="wall">Walls</button>
</div>
<div class="controls">
<input id="q" type="search" placeholder="{noun_one.capitalize()} or domain" aria-label="Filter {noun}">
<select id="region" aria-label="Region"><option value="">UK and US</option><option>UK</option><option>US</option></select>
<select id="drmin" aria-label="Minimum Domain Rating"><option value="">Any DR</option><option value="70">DR 70+</option><option value="80">DR 80+</option><option value="85">DR 85+</option><option value="90">DR 90+</option></select>
<label><input type="checkbox" id="e-chatgpt">Open to ChatGPT</label>
<label><input type="checkbox" id="e-perplexity">Perplexity</label>
<label><input type="checkbox" id="e-claude">Claude</label>
<select id="sig" aria-label="Signal"><option value="">Any signal</option>
<option value="wtwc">Won't train, will cite</option><option value="exception">OpenAI exception</option>
<option value="wall">Wall</option><option value="norules">No AI rules</option><option value="unreadable">Unreadable</option></select>
<span class="count" id="count"></span>
<div class="actions"><button id="csv">Download CSV</button><button id="copy">Copy domains</button><button id="reset">Reset</button></div>
</div>
<div class="tablewrap"><table id="t"><thead>
<tr><th rowspan="2" data-sort="name">{noun_one.capitalize()}</th><th rowspan="2" class="hide-m" data-sort="region">Region</th><th rowspan="2" data-sort="dr" title="Ahrefs Domain Rating">DR</th><th colspan="3" class="group">Citing (AI answer engines)</th><th rowspan="2" class="hide-m" data-sort="training" title="Stance across the 8 training bots; hover a row's chip for the count">Training bots</th><th rowspan="2" class="hide-m">Notes</th></tr>
<tr><th data-sort="chatgpt" title="OAI-SearchBot: ChatGPT search index">ChatGPT</th><th data-sort="perplexity" title="PerplexityBot: Perplexity search index">Perplexity</th><th data-sort="claude" title="Claude-SearchBot: Claude search index">Claude</th></tr>
</thead><tbody>
{"".join(rowparts)}
</tbody></table></div>

<h2>Share of {noun} blocking each bot</h2>
<p class="standfirst">Full blocks (Disallow: /) among the {n} readable {noun}.</p>
<div class="barchart">{"".join(bar_groups)}</div>

<h2>UK vs US</h2>
<div class="tablewrap" style="max-height:none"><table class="contrast"><thead><tr><th></th><th>UK</th><th>US</th></tr></thead>
<tbody>{"".join(contrasts)}</tbody></table></div>

<h2>Recent changes</h2>
<ul class="changes">{changes_html}</ul>

<h2>Method and limits</h2>
<ul>
<li>One GET per domain per day to /robots.txt only, with an identifying user agent, rate limited to one request per two seconds.</li>
<li>Rules follow RFC 9309 longest-match semantics. Engine states: Blocked = Disallow: / for that engine's search crawler; Path rules = an explicit AI-specific path restriction; Open = anything else, including generic sitewide rules inherited from the * group.</li>
<li>Unreadable rows are {noun} whose edge (WAF or CDN) refused even a robots.txt read from an identified audit agent. They are excluded from the headline percentages and marked Unknown.</li>
<li>Domain Rating by <a href="https://ahrefs.com/">Ahrefs</a> (<a href="https://ahrefs.com/legal/domain-rating-license">licence</a>){f", last refreshed {e(dr_date)}" if dr_date else ""}. DR moves slowly and is refreshed periodically, not daily.</li>
<li>Favicons via Google's public favicon service.</li>
</ul>

<footer>Data: <a href="{vkey}-latest.json">{vkey}-latest.json</a> (dated snapshots in the repo hold full history). Built by <a href="https://leefoot.com">Lee Foot</a>, ecommerce SEO consultant. More free tools at <a href="https://leefoot.com/tools">leefoot.com/tools</a>. Domain Rating by <a href="https://ahrefs.com/">Ahrefs</a>.</footer>
</div>
<script>{JS}{CHECKER_JS if checker else ""}</script></body></html>'''

    with open(os.path.join(DOCS, f"{vkey}.html"), "w") as f:
        f.write(page)
    nchanges = sum(len(x) for x in changes.values())
    print(f"  {vkey}: {n} readable + {len(unreadable)} unreadable of {total}; "
          f"open3={open3} prime={prime} closed3={closed3} exception={exception}; changes={nchanges}")
    return {"v": v, "date": date, "total": total, "n": n, "open3": open3, "closed3": closed3,
            "prime": prime, "exception": exception, "readable": readable, "changes": nchanges}


def build_index(summaries, dr_date):
    """Cross-vertical comparison page: the research view."""
    date = max(s["date"] for s in summaries)
    colours = ["#E8623D", "#1F7A8C", "#7A5510", "#4A5866"]
    for i, s in enumerate(summaries):
        s["colour"] = colours[i % len(colours)]

    tabs_html = "".join(f'<a class="vtab" href="{s["v"]["key"]}.html">{e(s["v"]["label"])}</a>'
                        for s in summaries) + '<a class="vtab on" href="index.html">Compare</a>'
    legend = "".join(f'<span><i class="swatch" style="background:{s["colour"]}"></i>'
                     f'{e(s["v"]["label"])} <span class="meta">(n={s["n"]})</span></span>' for s in summaries)

    def share(s, pred):
        pool = s["readable"]
        return (sum(1 for r in pool if pred(r)) * 100 // len(pool)) if pool else 0

    # headline measures, then every bot
    measures = [
        ("Open to all three AI answer engines",
         lambda r: all(engine_state(r, b) != "blocked" for _, _, b in ENGINES)),
        ("Blocks at least one AI search crawler",
         lambda r: any(engine_state(r, b) == "blocked" for _, _, b in ENGINES)),
        ("Blocks at least one training bot", lambda r: blocked_count(r, TRAINING) > 0),
        ("No AI rules at all", lambda r: "norules" in signals_for(r)),
    ]
    for bot in SEARCH + TRAINING:
        measures.append((f"Blocks {bot}",
                         lambda r, _b=bot: r["bots"].get(_b, {}).get("verdict") == "blocked"))

    rows = []
    for label, pred in measures:
        vals = [(s, share(s, pred)) for s in summaries]
        bars = "".join(
            f'<div class="cmpbar" title="{e(s["v"]["label"])}: {pct}% of {s["n"]} readable">'
            f'<span class="track"><span class="fill" style="width:{pct}%;background:{s["colour"]}"></span></span>'
            f'<span class="val">{pct}%</span></div>' for s, pct in vals)
        rows.append(f'<div class="cmprow"><span class="barlabel">{e(label)}</span>'
                    f'<div class="cmpbars">{bars}</div></div>')

    cards = "".join(
        f'<a class="tile" href="{s["v"]["key"]}.html" style="text-decoration:none">'
        f'<div class="n">{s["total"]}</div><div class="l"><b>{e(s["v"]["label"])}</b>: {e(s["v"]["blurb"])} '
        f'{s["n"]} readable on {e(s["date"])}.</div></a>' for s in summaries)

    # biggest divergence between verticals, for the standfirst
    spread = []
    for label, pred in measures:
        vals = [share(s, pred) for s in summaries]
        spread.append((max(vals) - min(vals), label, vals))
    spread.sort(reverse=True)
    top_gap = spread[0] if spread else (0, "", [])

    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Who Blocks AI Bots? AI crawler policy compared across industries</title>
<meta name="description" content="Daily robots.txt audit comparing how different industries block AI crawlers: which AI answer engines they are open to and which training bots they block.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body data-date="{e(date)}">
<div class="topbar-outer"><nav class="topbar">
<a class="wordmark" href="https://leefoot.com">Lee Foot<span class="dot">.</span></a>
<div class="navlinks"><a href="https://leefoot.com/tools">Tools</a><a href="https://leefoot.com/services">Services</a><a class="cta" href="https://leefoot.com/contact">Book a Call</a></div>
</nav></div>
<div class="wrap">
<div class="eyebrow">Updated daily, last run {e(date)}</div>
<h1>Who blocks AI bots?</h1>
<div class="vtabs">{tabs_html}</div>
<p class="standfirst">A daily robots.txt audit of {sum(s["total"] for s in summaries)} sites across {len(summaries)} industries, tracking which AI answer engines each is open to and which AI training crawlers it blocks. Published studies of AI blocking almost all look at news alone; this compares industries on identical measures, collected the same way on the same day.</p>

<div class="tiles">{cards}</div>
<p class="tilenote">As of {e(date)}. Cite as: AI Bot Blocking Tracker, Lee Foot, {e(date)}.</p>

<h2>Industry comparison</h2>
<p class="standfirst">Share of readable sites in each industry. Unreadable sites (edge firewalls that refuse an identified robots.txt reader) are excluded from every percentage.</p>
<div class="legend">{legend}</div>
<div class="barchart">{"".join(rows)}</div>

<h2>How to read this</h2>
<ul>
<li>This is <b>declared policy</b> (robots.txt), not enforcement, and it is not a measure of whether a site actually gets cited.</li>
<li>Blocking a <b>training</b> crawler is a licensing decision and does not remove a site from AI answers. Blocking an <b>AI search</b> crawler does.</li>
<li><b>Google AI Overviews and AI Mode are not in this data.</b> Their eligibility follows ordinary Googlebot indexing. Google-Extended governs Gemini training only.</li>
<li>Each industry list is built the same way: candidate domains from public registries and enumeration, validated against the Tranco top 1M ranked-domain list, classified to the industry, then ranked. Per-industry pages carry the full method.</li>
<li>Domain Rating by <a href="https://ahrefs.com/">Ahrefs</a>{f", last refreshed {e(dr_date)}" if dr_date else ""}.</li>
</ul>

<footer>Snapshots: {" ".join(f'<a href="{s["v"]["key"]}-latest.json">{s["v"]["key"]}</a>' for s in summaries)}. Dated snapshots in the <a href="https://github.com/searchsolved/ai-bot-blocking-tracker">repo</a> hold the full history. Built by <a href="https://leefoot.com">Lee Foot</a>. Domain Rating by <a href="https://ahrefs.com/">Ahrefs</a>.</footer>
</div></body></html>'''

    with open(os.path.join(DOCS, "index.html"), "w") as f:
        f.write(page)
    print(f"  index: {len(summaries)} verticals; widest gap {top_gap[0]} points on "
          f"{top_gap[1]!r} {top_gap[2]}")


def main():
    dr, dr_date = load_dr()
    checker = checker_url()
    os.makedirs(DOCS, exist_ok=True)
    summaries = []
    for v in VERTICALS:
        s = build_vertical(v, dr, dr_date, checker)
        if s:
            summaries.append(s)
    if not summaries:
        raise SystemExit("no vertical had any snapshots")
    if len(summaries) > 1:
        build_index(summaries, dr_date)
    else:
        # single vertical: it is the landing page
        only = summaries[0]["v"]["key"]
        with open(os.path.join(DOCS, "index.html"), "w") as f:
            f.write(open(os.path.join(DOCS, f"{only}.html")).read())
        print(f"  index: copy of {only}.html (only one vertical has data)")


if __name__ == "__main__":
    main()
