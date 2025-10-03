#!/usr/bin/env python3
"""
Resilient runner for agent message API queries (increased timeouts & retries).

- BASE_TIMEOUT default is now 300s (5 minutes)
- RETRIES default is now 3
- MAX_TIMEOUT default is now 3600s (1 hour)
- All values can still be overridden via env vars TIMEOUT, RETRIES, MAX_TIMEOUT
"""
import csv
import json
import os
import time
import logging
from datetime import datetime
from pathlib import Path
from requests import Request, exceptions as req_exceptions
from api_client import APIClient
from utils.payload_loader import get_logger

logger = get_logger("agent-runner")

# CONFIG (override via env if you prefer)
# BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
# ENDPOINT = os.environ.get("ENDPOINT", "/api/v1/agent/message")

BASE_URL = os.environ.get("BASE_URL", "http://35.226.27.129:8000")
ENDPOINT = os.environ.get("ENDPOINT", "/api/v1/agent/stream")
# Use the JWT you've been testing with (or set AGENT_JWT env var)
JWT = os.environ.get("AGENT_JWT") or (
    "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJ1c2VyX2lkIjoiMSIsInVzZXJuYW1lIjoiQ2xvdWRPd25lckBndmUubG9jYWwiLCJleHAiOjE3NTg4NzY0MzgsInR5cGUiOiJhY2Nlc3MifQ."
    "rq3a__Df-a67nvQrTJ8zAzrGGzGVC9ayaesik8bWM0DZXNi7kOKXjsF2wf2IVSyHz-OiSf_4X-wSVNckRI4W_tYFhp7w8GkvJju6G_kbxbrlONDH9M5_grwKTYhRe-Nx_hXVhH84SzmUw-dnYQ0A3MUh4kbFhNiggFgysRLDZy31zsg2y4LRq0w6SegT-CHf334_zqhuafMjUmL88t_NSNSo8xOX7VImtx_5BrzTPwtTzOmnI1QQ_HxbE458-xrpMKfsng65HnIv1O2IkgNPn7VynjJ1czXSg5KRu5uKY_uxDAbpZQj1RJ1eWgl8pMvYdRkya0yIs96XvcxevYXG3Q"
)

# Timing / retries (tunable via env)
# Increased defaults so long server-side queries won't cause failures
BASE_TIMEOUT = float(os.environ.get("TIMEOUT", "300"))        # seconds, base read timeout (default 300s)
RETRIES = int(os.environ.get("RETRIES", "3"))                # number of retries on timeout/conn error (default 3)
MAX_TIMEOUT = float(os.environ.get("MAX_TIMEOUT", "3600"))   # maximum timeout growth ceiling (default 3600s / 1 hour)

# files & paths
HERE = Path(__file__).parent.parent
CSV_PATH = HERE / "data" / "testcases.csv"
REPORTS_DIR = HERE / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
TIMESTAMP = time.strftime("%Y%m%d-%H%M%S")
OUT_JSON = REPORTS_DIR / f"agent_query_results_{TIMESTAMP}.json"
OUT_CSV = REPORTS_DIR / f"agent_query_results_{TIMESTAMP}.csv"

# Read queries from CSV
queries = []
if not CSV_PATH.exists():
    logger.error("CSV file not found: %s", CSV_PATH)
    raise SystemExit(1)

with open(CSV_PATH, newline="", encoding="utf-8") as fh:
    reader = csv.DictReader(fh)
    for row in reader:
        q = (row.get("query") or row.get("Query") or row.get("text") or "").strip()
        qid = (row.get("id") or row.get("Id") or row.get("TestCaseID") or "").strip()
        if q:
            queries.append({"id": qid, "query": q})

if not queries:
    logger.warning("No queries found in CSV: %s", CSV_PATH)

# API client (reuse your client wrapper - expects client.session)
client = APIClient(BASE_URL, timeout=BASE_TIMEOUT)


def prepare_and_send(query_text: str, retries: int = RETRIES) -> tuple:
    """
    Send the POST request, with retries/backoff on ReadTimeout / ConnectionError.

    Returns: (status, body)
      - status will be int status_code on HTTP response
      - or a sentinel string like 'TIMEOUT' or 'REQUEST_ERROR' on failure
      - body will be parsed JSON or text or error string
    """
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "python-requests/2.32.5",
    }
    if JWT:
        headers["Authorization"] = JWT if JWT.lower().startswith("bearer ") else f"Bearer {JWT}"

    payload = {
        "thread_id": "1",
        "session_id": "1",
        "content": [{"type": "text", "text": query_text}],
    }

    req = Request("POST", f"{BASE_URL.rstrip('/')}/{ENDPOINT.lstrip('/')}", headers=headers, json=payload)
    prepared = client.session.prepare_request(req)

    # Log prepared request (with Authorization redacted for logs)
    safe_headers = dict(prepared.headers)
    if "Authorization" in safe_headers:
        safe_headers["Authorization"] = safe_headers["Authorization"].split(" ", 1)[0] + " [REDACTED]"
    logger.info("=== PREPARED REQUEST ===")
    logger.info("%s %s", prepared.method, prepared.url)
    for k, v in safe_headers.items():
        logger.info("REQ-HEADER %s: %s", k, v)
    body_preview = prepared.body
    try:
        if isinstance(body_preview, bytes):
            body_preview = body_preview.decode("utf-8", errors="ignore")
    except Exception:
        pass
    logger.info("REQ-BODY: %s", body_preview)
    logger.info("========================")

    attempt = 0
    # start with base timeout, and increase on retries
    attempt_timeout = float(client.timeout or BASE_TIMEOUT) if client.timeout else BASE_TIMEOUT

    while True:
        attempt += 1
        try:
            t0 = time.time()
            resp = client.session.send(prepared, timeout=attempt_timeout)
            elapsed = time.time() - t0
            logger.info("Attempt %d -> status %s (elapsed %.3fs, timeout %.1fs)", attempt, resp.status_code, elapsed, attempt_timeout)
            # try parse json, fallback to text
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            return resp.status_code, body

        except req_exceptions.ReadTimeout as e:
            logger.warning("Attempt %d -> ReadTimeout after %.1fs: %s", attempt, attempt_timeout, str(e))
            if attempt > retries:
                logger.error("Exceeded retries (%d). Returning TIMEOUT.", retries)
                return "TIMEOUT", f"ReadTimeout after {attempt_timeout}s: {str(e)}"
            # exponential backoff & increase timeout (but cap it)
            backoff = min(0.5 * (2 ** (attempt - 1)), 8.0)
            attempt_timeout = min(attempt_timeout * 1.75, MAX_TIMEOUT)
            logger.info("Sleeping %.2fs before retrying (next timeout %.1fs)...", backoff, attempt_timeout)
            time.sleep(backoff)
            continue

        except req_exceptions.ConnectionError as e:
            logger.warning("Attempt %d -> ConnectionError: %s", attempt, str(e))
            if attempt > retries:
                logger.error("Exceeded retries (%d) on connection errors. Returning REQUEST_ERROR.", retries)
                return "REQUEST_ERROR", str(e)
            backoff = min(0.5 * (2 ** (attempt - 1)), 8.0)
            logger.info("Sleeping %.2fs before retrying connection...", backoff)
            time.sleep(backoff)
            continue

        except Exception as e:
            logger.exception("Unexpected exception while sending request: %s", str(e))
            return "REQUEST_ERROR", str(e)


# run and collect
results = []
for row in queries:
    qid = row["id"]
    qtext = row["query"]
    logger.info("Running query id=%s: %s", qid, qtext)
    status, body = prepare_and_send(qtext, retries=RETRIES)

    now_iso = datetime.utcnow().isoformat() + "Z"

    entry = {
        "id": qid,
        "query": qtext,
        "status": status,
        "body": body,
        "timestamp": time.time(),
        "date": now_iso,
    }
    results.append(entry)
    time.sleep(0.1)

# write JSON
OUT_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False))
logger.info("Wrote JSON report: %s", OUT_JSON)

# write CSV summary
with open(OUT_CSV, "w", newline="", encoding="utf-8") as csvfh:
    fieldnames = ["id", "query", "status", "date", "timestamp", "resp_text", "body"]
    writer = csv.DictWriter(csvfh, fieldnames=fieldnames)
    writer.writeheader()
    for r in results:
        try:
            ts = float(r.get("timestamp", time.time()))
            date_val = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            timestamp_human = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            date_val = datetime.now().strftime("%Y-%m-%d")
            timestamp_human = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        body = r.get("body", "")
        resp_text = ""
        if isinstance(body, dict):
            content = body.get("content")
            if isinstance(content, dict):
                resp_text = content.get("text", "")
            elif isinstance(content, list) and len(content) > 0 and isinstance(content[0], dict):
                resp_text = content[0].get("text", "") or content[0].get("message", "") or ""
            if not resp_text:
                resp_text = body.get("message", "") or body.get("error", "")

        body_text = body
        if not isinstance(body_text, str):
            try:
                body_text = json.dumps(body_text, ensure_ascii=False)
            except Exception:
                body_text = str(body_text)

        writer.writerow({
            "id": r.get("id", ""),
            "query": r.get("query", ""),
            "status": r.get("status", ""),
            "date": date_val,
            "timestamp": timestamp_human,
            "resp_text": resp_text,
            "body": body_text
        })

logger.info("Wrote CSV report: %s", OUT_CSV)
logger.info("Done. %d queries executed.", len(results))
