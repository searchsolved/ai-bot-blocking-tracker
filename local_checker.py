#!/usr/bin/env python3
"""Local stand-in for the Cloudflare checker Worker.

Serves GET /check?domain=example.com with the same response shape as
worker/checker.js, using the same parsing as the daily audit. For local
preview only; production uses the Worker.

Usage: python3 local_checker.py  (listens on http://localhost:8643)
"""

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from audit_robots import BOTS, audit_domain

ENGINES = {"chatgpt": "OAI-SearchBot", "perplexity": "PerplexityBot", "claude": "Claude-SearchBot"}
TRAINING = [b for b, c in BOTS.items() if c == "training"]
USERFETCH = [b for b, c in BOTS.items() if c == "user-fetch"]


def engine_state(entry):
    if entry["verdict"] == "blocked":
        return "blocked"
    if entry["verdict"] == "partial" and entry["explicit"]:
        return "limited"
    return "open"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path != "/check":
            self.send_json({"endpoints": ["/check?domain=example.com"]})
            return
        q = urllib.parse.parse_qs(url.query)
        domain = (q.get("domain", [""])[0]).strip().lower()
        domain = domain.replace("https://", "").replace("http://", "").split("/")[0]
        if domain.startswith("www."):
            domain = domain[4:]
        if "." not in domain:
            self.send_json({"error": "invalid domain"}, 400)
            return
        r = audit_domain(domain)
        if r["error"]:
            self.send_json({"domain": domain, "error": r["error"],
                            "note": "Could not read robots.txt. If this is a WAF 403, declared policy is unreadable from outside."})
            return
        engines = {k: {"bot": bot, "state": engine_state(r["bots"][bot])} for k, bot in ENGINES.items()}
        self.send_json({
            "domain": domain, "fetched": r["fetched"], "engines": engines,
            "training": {"blocked": sum(1 for b in TRAINING if r["bots"][b]["verdict"] == "blocked"),
                         "total": len(TRAINING)},
            "userFetch": {"blocked": sum(1 for b in USERFETCH if r["bots"][b]["verdict"] == "blocked"),
                          "total": len(USERFETCH)},
            "bots": r["bots"],
        })


if __name__ == "__main__":
    print("local checker on http://localhost:8643")
    HTTPServer(("127.0.0.1", 8643), Handler).serve_forever()
