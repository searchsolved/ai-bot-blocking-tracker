#!/usr/bin/env python3
"""Audit which publishers block AI bots in robots.txt, split by bot class.

Fetches robots.txt only (the file that exists to be fetched), one request
per domain, rate limited to 1 request per 2 seconds, with an identifying
user agent. Classifies each bot's effective rule and rolls up per bot class:
training, search/index, user fetch.

Usage:
    python3 audit_robots.py domains.txt [--out results.json]
    python3 audit_robots.py --canary          # built-in 10 domain canary list
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

UA = "LeeFootAIBotAudit/0.1 (+https://leefoot.com; robots.txt transparency audit; contact: lee@leefoot.com)"
RATE_SECONDS = 2.0
TIMEOUT = 15

# Bot roster, 2026. name -> class
BOTS = {
    # Training corpus crawlers
    "GPTBot": "training",
    "CCBot": "training",
    "ClaudeBot": "training",
    "anthropic-ai": "training",
    "Google-Extended": "training",
    "Applebot-Extended": "training",
    "Meta-ExternalAgent": "training",
    "Bytespider": "training",
    # AI search / index crawlers (blocking these costs citations)
    "OAI-SearchBot": "search",
    "Claude-SearchBot": "search",
    "PerplexityBot": "search",
    # User-triggered fetchers
    "ChatGPT-User": "user-fetch",
    "Claude-User": "user-fetch",
    "Perplexity-User": "user-fetch",
}

CANARY = [
    "bbc.co.uk", "theguardian.com", "dailymail.co.uk", "telegraph.co.uk",
    "thetimes.com", "ft.com", "thesun.co.uk", "mirror.co.uk",
    "independent.co.uk", "nytimes.com",
]

CF_MANAGED_SIGNATURE = "The collection of content and other data on this site through automated means"


def fetch_robots(domain):
    """Fetch robots.txt for a domain. Returns (text, final_url, headers, error)."""
    for prefix in ("https://", "https://www."):
        url = f"{prefix}{domain}/robots.txt"
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/plain,*/*"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read(512 * 1024).decode("utf-8", errors="replace")
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return body, resp.geturl(), headers, None
        except urllib.error.HTTPError as ex:
            if ex.code in (404, 410):
                # No robots.txt file: RFC 9309 semantics, everything allowed.
                headers = {k.lower(): v for k, v in ex.headers.items()} if ex.headers else {}
                return "", url, headers, None
            err = f"HTTP {ex.code}"
        except Exception as ex:
            err = str(ex)[:80]
    return None, None, {}, err


def edge_provider(headers):
    """Best-effort CDN/edge identification from response headers."""
    server = headers.get("server", "").lower()
    if "cloudflare" in server or "cf-ray" in headers:
        return "cloudflare"
    if "akamai" in server or "x-akamai-transformed" in headers:
        return "akamai"
    if "x-served-by" in headers and "cache" in headers.get("x-served-by", ""):
        return "fastly"
    if "x-amz-cf-id" in headers:
        return "cloudfront"
    if "x-vercel-id" in headers:
        return "vercel"
    return server or "unknown"


def parse_groups(text):
    """Parse robots.txt into a list of (agent_tokens, rules) groups.

    rules is a list of (directive, value) with directive in {allow, disallow}.
    """
    groups = []
    agents, rules = [], []
    seen_rule = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if seen_rule and agents:
                groups.append((agents, rules))
                agents, rules = [], []
                seen_rule = False
            agents.append(value.lower())
        elif field in ("allow", "disallow"):
            if agents:
                rules.append((field, value))
                seen_rule = True
    if agents:
        groups.append((agents, rules))
    return groups


def effective_group(groups, bot):
    """Return (rules, matched_token) for the group governing this bot.

    RFC 9309 style: the group whose agent token is the longest
    case-insensitive prefix of the bot name wins; '*' is the fallback.
    """
    bot_l = bot.lower()
    best, best_len = None, -1
    star = None
    for agents, rules in groups:
        for a in agents:
            if a == "*":
                if star is None:
                    star = rules
            elif bot_l == a or bot_l.startswith(a):
                if len(a) > best_len:
                    best, best_len = (rules, a), len(a)
    if best:
        return best[0], best[1]
    return (star, "*") if star is not None else (None, None)


def classify(rules):
    """Classify a rule set for a bot: full-block, partial, allowed."""
    if rules is None:
        return "no-rule"
    disallow_all = any(d == "disallow" and v == "/" for d, v in rules)
    allow_all = any(d == "allow" and v in ("/", "") for d, v in rules)
    has_disallow = any(d == "disallow" and v not in ("",) for d, v in rules)
    if disallow_all and not allow_all:
        return "blocked"
    if has_disallow:
        return "partial"
    return "allowed"


def audit_domain(domain):
    text, url, headers, err = fetch_robots(domain)
    result = {"domain": domain, "fetched": url, "error": err,
              "edge": edge_provider(headers), "cloudflare_managed": False, "bots": {}}
    if text is None:
        return result
    result["cloudflare_managed"] = CF_MANAGED_SIGNATURE in text
    groups = parse_groups(text)
    for bot in BOTS:
        rules, token = effective_group(groups, bot)
        verdict = classify(rules)
        explicit = token not in (None, "*")
        entry = {"verdict": verdict, "via": token or "none", "explicit": explicit}
        if verdict == "partial" and rules:
            entry["paths"] = [v for d, v in rules if d == "disallow" and v][:10]
        result["bots"][bot] = entry
    return result


def summarise(results):
    lines = []
    ok = [r for r in results if not r["error"]]
    lines.append(f"Fetched {len(ok)}/{len(results)} robots.txt files.")
    header = f"{'domain':<20} {'training blocked':<17} {'search blocked':<15} {'user-fetch blocked':<19} {'edge':<12} {'CF managed'}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in results:
        if r["error"]:
            lines.append(f"{r['domain']:<20} fetch error: {r['error']}")
            continue
        counts = {}
        for cls in ("training", "search", "user-fetch"):
            bots = [b for b, c in BOTS.items() if c == cls]
            blocked = sum(1 for b in bots if r["bots"][b]["verdict"] == "blocked")
            counts[cls] = f"{blocked}/{len(bots)}"
        cf = "yes" if r["cloudflare_managed"] else ""
        lines.append(f"{r['domain']:<20} {counts['training']:<17} {counts['search']:<15} {counts['user-fetch']:<19} {r['edge']:<12} {cf}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domains_file", nargs="?", help="file with one domain per line")
    ap.add_argument("--canary", action="store_true", help="run the built-in 10 domain canary")
    ap.add_argument("--out", default=None, help="write full JSON results here")
    args = ap.parse_args()

    if args.canary:
        domains = CANARY
    elif args.domains_file:
        with open(args.domains_file) as f:
            domains = [d.split("|")[0].strip().lower() for d in f
                       if d.strip() and not d.startswith("#")]
    else:
        ap.error("give a domains file or --canary")

    results = []
    for i, domain in enumerate(domains):
        if i:
            time.sleep(RATE_SECONDS)
        print(f"[{i+1}/{len(domains)}] {domain}", file=sys.stderr)
        results.append(audit_domain(domain))

    print(summarise(results))
    if args.out:
        payload = {"run_at": datetime.now(timezone.utc).isoformat(), "user_agent": UA,
                   "bots": BOTS, "results": results}
        with open(args.out, "w") as f:
            json.dump(payload, f, indent=1)
        print(f"\nFull results: {args.out}")


if __name__ == "__main__":
    main()
