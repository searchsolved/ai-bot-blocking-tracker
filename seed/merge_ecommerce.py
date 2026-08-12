#!/usr/bin/env python3
"""Build verticals/ecommerce.txt from the enumerated UK and US retail lists.

Same method as the news list: enumerate candidates from knowledge, validate
every one against the Tranco top 1M ranked-domain list (which kills dead and
misremembered domains), then take the top TARGET by rank.

Region rule: a domain enumerated in only one market gets that market. A brand
in both (ikea.com, zara.com, nike.com) is assigned by TLD, defaulting to US
for global .com storefronts. Multinationals are therefore filed under their
primary market, which the page documents.
"""

import json
import re

PER_REGION = 250  # balanced sample: fair UK vs US contrast within the vertical

# Obvious non-retail that tends to leak into brand enumeration.
BLOCKLIST = {
    "doordash.com", "ubereats.com", "grubhub.com", "deliveroo.co.uk", "justeat.co.uk",
    "opentable.com", "airbnb.com", "booking.com", "expedia.com", "ticketmaster.com",
    "netflix.com", "spotify.com", "paypal.com", "klarna.com", "shopify.com",
    "google.com", "facebook.com", "instagram.com", "pinterest.com", "youtube.com",
}

rank = {}
for line in open("top-1m.csv"):
    r, d = line.strip().split(",", 1)
    rank[d] = int(r)


def tranco_rank(domain):
    if domain in rank:
        return rank[domain]
    parts = domain.split(".")
    for i in range(1, len(parts) - 1):
        cand = ".".join(parts[i:])
        if re.search(r"\.(edu|gov|mil)$|\.(ac|gov|sch)\.uk$", cand):
            return None
        if cand in rank:
            return rank[cand]
    return None


def norm(d):
    d = d.strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0]
    return re.sub(r"^www\.", "", d)


def read(path, region):
    out = {}
    try:
        for line in open(path):
            bits = line.rstrip("\n").split("\t")
            if len(bits) < 2 or not bits[0].strip():
                continue
            d, name = norm(bits[0]), bits[1].strip()
            if d.startswith("#") or "." not in d:
                continue
            out[d] = name
    except FileNotFoundError:
        print(f"WARNING: {path} missing")
    return out


uk = read("ecom_uk_enumerated.tsv", "UK")
us = read("ecom_us_enumerated.tsv", "US")
print(f"enumerated: {len(uk)} UK, {len(us)} US")

pool, both = {}, 0
for d in set(uk) | set(us):
    if d in BLOCKLIST:
        continue
    in_uk, in_us = d in uk, d in us
    if in_uk and in_us:
        both += 1
        region = "UK" if re.search(r"\.(co\.)?uk$", d) else "US"
    else:
        region = "UK" if in_uk else "US"
    name = uk.get(d) or us.get(d)
    tr = tranco_rank(d)
    if tr:
        pool[d] = (name, region, tr)

print(f"in both lists: {both}; validated in Tranco: {len(pool)} of {len(set(uk) | set(us))}")

ranked_all = sorted(pool.items(), key=lambda kv: kv[1][2])
uk_rows = [(d, v) for d, v in ranked_all if v[1] == "UK"][:PER_REGION]
us_rows = [(d, v) for d, v in ranked_all if v[1] == "US"][:PER_REGION]
ranked = sorted(uk_rows + us_rows, key=lambda kv: kv[1][2])

with open("../verticals/ecommerce.txt", "w") as f:
    f.write("# UK ecommerce and retail (enumerated Aug 2026, Tranco-validated, "
            f"top {len(uk_rows)} by rank; multinationals filed under primary market)\n")
    for d, (name, _, _) in uk_rows:
        f.write(f"{d} | {name}\n")
    f.write("# US ecommerce and retail (same method)\n")
    for d, (name, _, _) in us_rows:
        f.write(f"{d} | {name}\n")

print(f"wrote verticals/ecommerce.txt: {len(ranked)} ({len(uk_rows)} UK, {len(us_rows)} US)")
print("head:", [d for d, _ in ranked[:6]])
print("tail:", [d for d, _ in ranked[-4:]])
