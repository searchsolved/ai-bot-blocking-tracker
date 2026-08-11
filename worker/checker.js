/**
 * Live robots.txt checker for the AI Bot Blocking Tracker.
 *
 * Endpoints:
 *   GET /check?domain=example.com  -> parsed engine states + training summary
 *   GET /suggestions?min=25        -> domains checked at least `min` times
 *
 * Parsing mirrors audit_robots.py: RFC 9309 longest-prefix group matching,
 * verdicts blocked / partial / allowed / no-rule, engine states
 * blocked / limited (explicit AI path rules) / open.
 *
 * KV binding: SUGGEST (counts checks per domain for the review queue).
 */

const TRAINING = ["GPTBot", "CCBot", "ClaudeBot", "anthropic-ai", "Google-Extended",
  "Applebot-Extended", "Meta-ExternalAgent", "Bytespider"];
const SEARCH = ["OAI-SearchBot", "Claude-SearchBot", "PerplexityBot"];
const USERFETCH = ["ChatGPT-User", "Claude-User", "Perplexity-User"];
const ALL_BOTS = [...TRAINING, ...SEARCH, ...USERFETCH];
const ENGINES = [["chatgpt", "OAI-SearchBot"], ["perplexity", "PerplexityBot"], ["claude", "Claude-SearchBot"]];

const UA = "LeeFootAIBotAudit/0.1 (+https://leefoot.com; live checker)";
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "public, max-age=300",
};

function parseGroups(text) {
  const groups = [];
  let agents = [], rules = [], seenRule = false;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.split("#")[0].trim();
    const idx = line.indexOf(":");
    if (!line || idx < 0) continue;
    const field = line.slice(0, idx).trim().toLowerCase();
    const value = line.slice(idx + 1).trim();
    if (field === "user-agent") {
      if (seenRule && agents.length) {
        groups.push([agents, rules]);
        agents = []; rules = []; seenRule = false;
      }
      agents.push(value.toLowerCase());
    } else if (field === "allow" || field === "disallow") {
      if (agents.length) { rules.push([field, value]); seenRule = true; }
    }
  }
  if (agents.length) groups.push([agents, rules]);
  return groups;
}

function effectiveGroup(groups, bot) {
  const botL = bot.toLowerCase();
  let best = null, bestLen = -1, star = null;
  for (const [agents, rules] of groups) {
    for (const a of agents) {
      if (a === "*") { if (star === null) star = rules; }
      else if (botL === a || botL.startsWith(a)) {
        if (a.length > bestLen) { best = rules; bestLen = a.length; }
      }
    }
  }
  if (best !== null) return [best, true];
  return [star, false];
}

function classify(rules) {
  if (rules === null || rules === undefined) return "no-rule";
  const disallowAll = rules.some(([d, v]) => d === "disallow" && v === "/");
  const allowAll = rules.some(([d, v]) => d === "allow" && (v === "/" || v === ""));
  const hasDisallow = rules.some(([d, v]) => d === "disallow" && v !== "");
  if (disallowAll && !allowAll) return "blocked";
  if (hasDisallow) return "partial";
  return "allowed";
}

function engineState(verdict, explicit) {
  if (verdict === "blocked") return "blocked";
  if (verdict === "partial" && explicit) return "limited";
  return "open";
}

async function fetchRobots(domain) {
  for (const prefix of ["https://", "https://www."]) {
    try {
      const resp = await fetch(`${prefix}${domain}/robots.txt`, {
        headers: { "User-Agent": UA, "Accept": "text/plain,*/*" },
        signal: AbortSignal.timeout(10000),
        redirect: "follow",
      });
      if (resp.ok) return { text: await resp.text(), url: resp.url };
      var err = `HTTP ${resp.status}`;
    } catch (ex) {
      var err = String(ex.message || ex).slice(0, 80);
    }
  }
  return { error: err };
}

// Best-effort per-IP throttle (isolate-scoped; back it with a Cloudflare
// rate limiting rule on /check at deploy time for a hard guarantee).
const ipHits = new Map();
function throttled(ip) {
  const now = Date.now();
  const hits = (ipHits.get(ip) || []).filter(t => now - t < 60000);
  hits.push(now);
  ipHits.set(ip, hits);
  if (ipHits.size > 5000) ipHits.clear();
  return hits.length > 10;
}

async function handleCheck(url, env, request) {
  let domain = (url.searchParams.get("domain") || "").trim().toLowerCase();
  domain = domain.replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0];
  if (!/^[a-z0-9][a-z0-9.-]{2,250}\.[a-z]{2,}$/.test(domain)) {
    return new Response(JSON.stringify({ error: "invalid domain" }), { status: 400, headers: CORS });
  }
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  if (throttled(ip)) {
    return new Response(JSON.stringify({ error: "rate limited, try again in a minute" }),
      { status: 429, headers: CORS });
  }

  // Serve a cached result if the domain was checked in the last 10 minutes,
  // so public traffic never re-fetches a publisher's robots.txt in bursts.
  if (env.SUGGEST) {
    const cached = await env.SUGGEST.get(`result:${domain}`);
    if (cached) {
      await bumpCount(env, domain);
      return new Response(cached, { headers: { ...CORS, "X-Cache": "hit" } });
    }
  }

  const result = await fetchRobots(domain);
  if (result.error) {
    await bumpCount(env, domain);
    return new Response(JSON.stringify({
      domain, error: result.error,
      note: "Could not read robots.txt. If this is a WAF 403, declared policy is unreadable from outside.",
    }), { headers: CORS });
  }

  const groups = parseGroups(result.text);
  const bots = {};
  for (const bot of ALL_BOTS) {
    const [rules, explicit] = effectiveGroup(groups, bot);
    const verdict = classify(rules);
    const entry = { verdict, explicit };
    if (verdict === "partial" && rules) {
      entry.paths = rules.filter(([d, v]) => d === "disallow" && v).map(([, v]) => v).slice(0, 10);
    }
    bots[bot] = entry;
  }
  const engines = {};
  for (const [key, bot] of ENGINES) {
    engines[key] = { bot, state: engineState(bots[bot].verdict, bots[bot].explicit) };
  }
  const trainingBlocked = TRAINING.filter(b => bots[b].verdict === "blocked").length;
  const userFetchBlocked = USERFETCH.filter(b => bots[b].verdict === "blocked").length;

  await bumpCount(env, domain);
  const payload = JSON.stringify({
    domain, fetched: result.url, engines,
    training: { blocked: trainingBlocked, total: TRAINING.length },
    userFetch: { blocked: userFetchBlocked, total: USERFETCH.length },
    bots,
  });
  if (env.SUGGEST) {
    await env.SUGGEST.put(`result:${domain}`, payload, { expirationTtl: 600 });
  }
  return new Response(payload, { headers: CORS });
}

async function bumpCount(env, domain) {
  if (!env.SUGGEST) return;
  try {
    const key = `count:${domain}`;
    const current = parseInt(await env.SUGGEST.get(key), 10) || 0;
    await env.SUGGEST.put(key, String(current + 1));
  } catch (ex) { /* counting is best-effort */ }
}

async function handleSuggestions(url, env) {
  if (!env.SUGGEST) {
    return new Response(JSON.stringify({ error: "no KV binding" }), { status: 500, headers: CORS });
  }
  const min = parseInt(url.searchParams.get("min"), 10) || 25;
  const out = [];
  let cursor;
  do {
    const page = await env.SUGGEST.list({ prefix: "count:", cursor });
    for (const k of page.keys) {
      const count = parseInt(await env.SUGGEST.get(k.name), 10) || 0;
      if (count >= min) {
        const domain = k.name.slice(6);
        const uk = /\.(co\.uk|org\.uk|uk|scot|wales|cymru|london)$/.test(domain);
        out.push({ domain, count, region_guess: uk ? "UK" : "REVIEW" });
      }
    }
    cursor = page.list_complete ? null : page.cursor;
  } while (cursor);
  out.sort((a, b) => b.count - a.count);
  return new Response(JSON.stringify({ min, suggestions: out }), { headers: CORS });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    if (url.pathname === "/check") return handleCheck(url, env, request);
    if (url.pathname === "/suggestions") return handleSuggestions(url, env);
    return new Response(JSON.stringify({
      endpoints: ["/check?domain=example.com", "/suggestions?min=25"],
    }), { headers: CORS });
  },
};
