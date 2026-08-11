#!/usr/bin/env python3
"""Build the expanded domains.txt from classified candidates + UK enumeration.

Inclusion: classification KEEPs plus Tranco-validated enumerated UK titles,
ranked by Tranco, top TARGET overall, with the existing curated 200 always
retained (time-series continuity).
"""

import json
import re

TARGET = 1000

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
        # a university, school or government parent must not confer its rank
        # on a hosted subdomain (student papers inheriting cam.ac.uk etc.)
        if re.search(r"\.(edu|gov|mil)$|\.(ac|gov|sch)\.uk$", cand) or cand in ("cam.ac.uk",):
            return None
        if cand in rank:
            return rank[cand]
    return None

def norm(d):
    d = d.strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0]
    return re.sub(r"^www\.", "", d)

# explicit classifier drops override everything, including the curated list
drops = set()
for line in open("classified.tsv"):
    bits = line.rstrip("\n").split("\t")
    if len(bits) >= 2 and bits[1].strip().upper() == "DROP":
        drops.add(norm(bits[0]))

# curated core (the original 200): kept unless explicitly dropped by the classifier
curated, region_now = {}, "UK"
for line in open("curated_core.txt"):
    line = line.strip()
    if line.startswith("#"):
        region_now = "US" if "US" in line else "UK"
    elif line:
        parts = [p.strip() for p in line.split("|", 1)]
        d = norm(parts[0])
        if d not in drops:
            curated[d] = (parts[1] if len(parts) > 1 else parts[0], region_now)

pool = {}  # domain -> (name, region, tranco)
for d, (name, region) in curated.items():
    pool[d] = (name, region, tranco_rank(d) or 10**9)

kept = dropped = 0
for line in open("classified.tsv"):
    bits = line.rstrip("\n").split("\t")
    if len(bits) < 4:
        continue
    d, verdict, region, name = norm(bits[0]), bits[1].strip().upper(), bits[2].strip().upper(), bits[3].strip()
    if verdict != "KEEP" or region not in ("UK", "US"):
        dropped += 1
        continue
    kept += 1
    if d not in pool:
        tr = tranco_rank(d)
        if tr:
            pool[d] = (name or d, region, tr)

enum_added = enum_rejected = 0
try:
    for line in open("uk_enumerated.tsv"):
        bits = line.rstrip("\n").split("\t")
        if len(bits) < 2:
            continue
        d, name = norm(bits[0]), bits[1].strip()
        if d in pool:
            continue
        tr = tranco_rank(d)
        if tr:
            pool[d] = (name or d, "UK", tr)
            enum_added += 1
        else:
            enum_rejected += 1
except FileNotFoundError:
    print("WARNING: uk_enumerated.tsv missing")

ranked = sorted(pool.items(), key=lambda kv: kv[1][2])
final = [kv for kv in ranked if kv[0] in curated]
for kv in ranked:
    if len(final) >= TARGET:
        break
    if kv[0] not in curated:
        final.append(kv)
final.sort(key=lambda kv: kv[1][2])

uk = [(d, v) for d, v in final if v[1] == "UK"]
us = [(d, v) for d, v in final if v[1] == "US"]
with open("../domains.txt", "w") as f:
    f.write("# UK news publishers (registry-derived Aug 2026: Wikidata + homepages.news + "
            "publisher networks + curation, Tranco-validated, LLM-classified)\n")
    for d, (name, _, _) in sorted(uk, key=lambda kv: kv[1][2]):
        f.write(f"{d} | {name}\n")
    f.write("# US news publishers (same method)\n")
    for d, (name, _, _) in sorted(us, key=lambda kv: kv[1][2]):
        f.write(f"{d} | {name}\n")

print(f"classified: kept {kept}, dropped {dropped}; enumerated: added {enum_added}, "
      f"no-tranco {enum_rejected}")
print(f"final list: {len(final)} ({len(uk)} UK, {len(us)} US)")
print("head:", [d for d, _ in final[:5]], "tail:", [d for d, _ in final[-3:]])
