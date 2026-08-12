#!/usr/bin/env python3
"""Apply the ecommerce classification pass to verticals/ecommerce.txt.

The candidate pool was the top 250 per market by Tranco rank; this drops the
ones that failed classification (software companies, digital-only, non-UK/US
operations, defunct, duplicates) and applies region corrections. The result is
"the highest-ranked retailers per market that pass classification", which is
smaller than 250 per market and is reported as such.
"""

import re
from collections import Counter


def norm(d):
    d = d.strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0]
    return re.sub(r"^www\.", "", d)


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
        if cand in rank:
            return rank[cand]
    return 10 ** 9


keep, drops = {}, Counter()
for line in open("ecom_classified.tsv"):
    bits = line.rstrip("\n").split("\t")
    if len(bits) < 4:
        continue
    d, verdict, region, name = norm(bits[0]), bits[1].strip().upper(), bits[2].strip().upper(), bits[3].strip()
    if verdict == "KEEP" and region in ("UK", "US"):
        keep[d] = (name or d, region, tranco_rank(d))
    else:
        drops[(bits[4].strip().lower() if len(bits) > 4 and bits[4].strip() else "other")] += 1

uk = sorted([(d, v) for d, v in keep.items() if v[1] == "UK"], key=lambda kv: kv[1][2])
us = sorted([(d, v) for d, v in keep.items() if v[1] == "US"], key=lambda kv: kv[1][2])

with open("../verticals/ecommerce.txt", "w") as f:
    f.write(f"# UK ecommerce and retail (enumerated Aug 2026, Tranco-validated, classified; "
            f"top {len(uk)} by rank. Multinationals filed under primary market)\n")
    for d, (name, _, _) in uk:
        f.write(f"{d} | {name}\n")
    f.write(f"# US ecommerce and retail (same method; top {len(us)})\n")
    for d, (name, _, _) in us:
        f.write(f"{d} | {name}\n")

print(f"kept {len(keep)} ({len(uk)} UK, {len(us)} US); dropped {sum(drops.values())}: {dict(drops)}")
print("UK head:", [d for d, _ in uk[:6]])
print("US head:", [d for d, _ in us[:6]])
