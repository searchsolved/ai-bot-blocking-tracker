#!/usr/bin/env python3
"""Refresh data/dr.json from the Ahrefs API using an API key.

Usage:
    AHREFS_API_KEY=xxx python3 fetch_dr.py [--canary] [--force]

- Reads domains from domains.txt.
- Skips the refresh entirely if dr.json is younger than MAX_AGE_DAYS
  (DR moves slowly; there is no reason to fetch it daily). --force overrides.
- --canary fetches the first 5 domains only and prints them, no file write.
- Rate limited to 1 request per second, one retry per domain.

Endpoint note: PUBLIC_PATH below is the free Domain Rating endpoint as of
Aug 2026 (zero API unit cost; the same endpoint the Ahrefs MCP tool
public-domain-rating-free wraps). If it 404s on your key, check
https://docs.ahrefs.com API reference for the current path and update the
constant; the paid fallback /v3/site-explorer/domain-rating also returns DR
but costs units and needs date=YYYY-MM-DD.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "dr.json")
PUBLIC_PATH = "https://api.ahrefs.com/v3/public/domain-rating-free"
MAX_AGE_DAYS = 28
LICENSE = "http://ahrefs.com/legal/domain-rating-license"


def load_domains():
    """Every domain across every vertical list in verticals/."""
    import glob
    seen, out = set(), []
    for path in sorted(glob.glob(os.path.join(BASE, "verticals", "*.txt"))):
        for line in open(path):
            d = line.split("|")[0].strip().lower()
            if d and not d.startswith("#") and d not in seen:
                seen.add(d)
                out.append(d)
    return out


def fetch_one(key, domain):
    url = f"{PUBLIC_PATH}?target={domain}&output=json"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "LeeFootAIBotAudit/0.1 (+https://leefoot.com)",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.load(resp)
    node = payload.get("domain_rating", payload)
    val = node.get("domain_rating") if isinstance(node, dict) else None
    return float(val) if val is not None else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canary", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("AHREFS_API_KEY")
    if not key:
        sys.exit("AHREFS_API_KEY not set")

    domains = load_domains()
    existing = {}
    if os.path.exists(OUT):
        existing = json.load(open(OUT)).get("ratings", {})

    if args.canary:
        domains = domains[:5]
    elif not args.force:
        missing = [d for d in domains if existing.get(d) is None]
        fetched = json.load(open(OUT)).get("fetched", "1970-01-01") if os.path.exists(OUT) else "1970-01-01"
        stale = datetime.strptime(fetched, "%Y-%m-%d").date() <= date.today() - timedelta(days=MAX_AGE_DAYS)
        if not missing and not stale:
            print(f"dr.json is fresh ({fetched}) and complete; skipping. Use --force to refetch all.")
            return
        if missing and not stale:
            # New domains only: enrich without refetching the whole set.
            print(f"dr.json fresh ({fetched}); fetching {len(missing)} new domains only.")
            domains = missing

    ratings, failed = {}, []
    for i, d in enumerate(domains):
        if i:
            time.sleep(1)
        try:
            ratings[d] = fetch_one(key, d)
        except Exception as ex:
            failed.append((d, str(ex)[:60]))
            ratings[d] = None
        print(f"[{i+1}/{len(domains)}] {d} -> {ratings[d]}", file=sys.stderr)

    for d, _ in list(failed):
        time.sleep(1)
        try:
            ratings[d] = fetch_one(key, d)
            failed = [f for f in failed if f[0] != d]
        except Exception:
            pass

    if args.canary:
        print(json.dumps(ratings, indent=1))
        return

    merged = dict(existing)
    merged.update(ratings)
    json.dump({"fetched": date.today().isoformat(),
               "source": "Ahrefs public Domain Rating endpoint (free), API key",
               "license": LICENSE, "ratings": merged},
              open(OUT, "w"), indent=1)
    print(f"Wrote {OUT}: {len(ratings)} domains, {len(failed)} failed: {failed}")


if __name__ == "__main__":
    main()
