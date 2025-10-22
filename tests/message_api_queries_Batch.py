#!/usr/bin/env python3
"""
This script is a batch runner designed to test the Agent strem API efficiently. It reads a list of queries from a CSV file, sends them to the /api/v1/agent/stream endpoint and streams responses,it smartly extracts the latest tool_runner output as the assistant’s final reply. The tool runs in configurable batches, includes retry and timeout handling , and logs detailed request/response info. After execution, it automatically generates timestamped JSON and CSV reports for all results and merges them into a combined report. In short, it provides a reliable, automated way to run bulk API tests, capture real assistant responses, and analyze response over multiple queries.

Batching runner for agent message API queries.

This version:
- Matches Swagger/cURL headers by default (accept: application/json).
- Authorization header is HARD-CODED to the provided JWT (no env/conditions).
- If server returns text/event-stream, will still parse it and extract the second-last
  tool_runner 'output' into resp_text.
"""

import csv
import json
import os
import time
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Tuple, Any
from requests import Request, exceptions as req_exceptions

from api_client import APIClient
from utils.payload_loader import get_logger

logger = get_logger("agent-runner")

# ------------ Config (env-overridable) ------------
BASE_URL = os.environ.get("BASE_URL", "http://35.226.27.129:8000")
ENDPOINT = os.environ.get("ENDPOINT", "/api/v1/agent/stream")

# ------------ HARD-CODED JWT (as requested; no conditions) ------------
JWT = (
    "Bearer "
    "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJ1c2VyX2lkIjoxLCJvcmdhbml6YXRpb25faWQiOjEsImluZnJhX2lkIjoxLCJleHAiOjE3NTk3MzExMzcsInR5cGUiOiJhY2Nlc3MifQ."
    "dTUS4WQV5HfN5q_mpn_e1ekt9bPneoao0ZHGXquhfRGs6kzQwIEShFHSV34jWjONM7aC1mwp-NJLcpdjMXjiyzCByROOR90zfjeaiUd8V3phm2Pl0rqmSzZwF_s0OidIi3Inh4p8c7xaxDkSfKzLtUxUTlSV5LTyKKh6vM8fZevum5CLGOfebnmHfuwkCK4Ic47Qk_ugbokBrUuaiLFPvaiNCS2ZqmQHMj13vgUD8zV-3MTyz68jngLN2aCb_isJeb3v_Dcw3JQFpxR6FOqHTQ5J2PXnMug1cOhO2FUmsXgwF3s11MUgLP1ayWRseUHqVcFl6cT9o9mLNxuT99PJHg"
)


# Default payload IDs (can be overridden per-row via CSV columns)
THREAD_ID = os.environ.get("THREAD_ID", "1")
SESSION_ID = os.environ.get("SESSION_ID", "1")
DEVICE_ID = os.environ.get("DEVICE_ID", "3f48396f-ffa1-4411-81f5-a9a4bd777a41")

# Timeouts & retries
BASE_TIMEOUT = float(os.environ.get("TIMEOUT", "600"))     # seconds
RETRIES = int(os.environ.get("RETRIES", "3"))
MAX_TIMEOUT = float(os.environ.get("MAX_TIMEOUT", "1800")) # seconds

# Batching controls
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "10"))
BATCH_DELAY = float(os.environ.get("BATCH_DELAY", "15"))
START_INDEX = int(os.environ.get("START_INDEX", "0"))

# Paths & reporting
HERE = Path(__file__).parent.parent
CSV_PATH = HERE / "data" / "testcases.csv"
REPORTS_DIR = HERE / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

TIMESTAMP = time.strftime("%Y%m%d-%H%M%S")
OUT_JSON = REPORTS_DIR / f"agent_query_results_{TIMESTAMP}.json"
OUT_CSV  = REPORTS_DIR / f"agent_query_results_{TIMESTAMP}.csv"

# ---------- helper functions ----------
def load_queries(csv_path: Path) -> List[Dict[str, str]]:
    if not csv_path.exists():
        logger.error("CSV file not found: %s", csv_path)
        raise SystemExit(1)
    qs: List[Dict[str, str]] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rdr = csv.DictReader(fh)
        for idx, row in enumerate(rdr):
            q_text = (row.get("query") or row.get("Query") or row.get("text") or "").strip()
            if not q_text:
                continue
            qid = (row.get("id") or row.get("Id") or row.get("TestCaseID") or "").strip() or f"row-{idx}"
            # Optional per-row overrides:
            th_id = (row.get("thread_id") or "").strip()
            ss_id = (row.get("session_id") or "").strip()
            dv_id = (row.get("device_id") or "").strip()
            qs.append({
                "id": qid,
                "query": q_text,
                "thread_id": th_id,
                "session_id": ss_id,
                "device_id": dv_id,
            })
    return qs

def chunked(iterable: List[Any], n: int):
    for i in range(0, len(iterable), n):
        yield iterable[i:i+n]

def write_json(results: List[Dict[str, Any]], path: Path):
    try:
        path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Wrote JSON: %s (entries=%d)", path, len(results))
    except Exception:
        logger.exception("Failed writing JSON to %s", path)

def write_csv(results: List[Dict[str, Any]], path: Path):
    fieldnames = ["id", "query", "status", "date", "timestamp", "resp_text", "body"]
    try:
        with path.open("w", newline="", encoding="utf-8") as csvfh:
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

                writer.writerow({
                    "id": r.get("id", ""),
                    "query": r.get("query", ""),
                    "status": r.get("status", ""),
                    "date": date_val,
                    "timestamp": timestamp_human,
                    "resp_text": r.get("resp_text", ""),
                    "body": r.get("body", ""),
                })
        logger.info("Wrote CSV: %s (entries=%d)", path, len(results))
    except Exception:
        logger.exception("Failed writing CSV to %s", path)

# ---------- combine CSV reports ----------
def combine_all_csv_reports(reports_dir: Path, out_filename: str = None) -> Path:
    pattern = "agent_query_results_*.csv"
    files = sorted(reports_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    if not files:
        logger.warning("No CSV report files found matching %s in %s", pattern, reports_dir)
        return Path()

    logger.info("Found %d CSV report(s) to combine.", len(files))
    seen = {}
    combined_rows: List[Dict[str, str]] = []

    for f in files:
        try:
            with f.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    row_id = (row.get("id") or row.get("test_id") or "").strip()
                    row_query = (row.get("query") or row.get("payload") or "").strip()
                    if not row_query:
                        row_query = (row.get("resp_text") or "").strip()
                    key = (row_id, row_query) if (row_id or row_query) else (None, json.dumps(row, sort_keys=True))
                    seen[key] = row
        except Exception:
            logger.exception("Failed reading CSV file %s, skipping.", f)
            continue

    for _, row in seen.items():
        out = {
            "id": row.get("id", row.get("test_id", "")),
            "query": row.get("query", "") or row.get("payload", "") or "",
            "status": row.get("status", ""),
            "date": row.get("date", ""),
            "timestamp": row.get("timestamp", ""),
            "resp_text": row.get("resp_text", "") or row.get("resp_fields", "") or "",
            "body": row.get("body", "") or ""
        }
        combined_rows.append(out)

    out_fn = out_filename or f"combined_agent_query_results_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    out_path = reports_dir / out_fn
    try:
        with out_path.open("w", newline="", encoding="utf-8") as csvfh:
            fieldnames = ["id", "query", "status", "date", "timestamp", "resp_text", "body"]
            writer = csv.DictWriter(csvfh, fieldnames=fieldnames)
            writer.writeheader()
            for r in combined_rows:
                writer.writerow(r)
        logger.info("Wrote combined CSV: %s (rows=%d)", out_path, len(combined_rows))
    except Exception:
        logger.exception("Failed to write combined CSV to %s", out_path)
        raise
    return out_path

# ---------- HTTP send logic (resilient) ----------
client = APIClient(BASE_URL, timeout=BASE_TIMEOUT)

def _normalize_ids(row: Dict[str, str]) -> Tuple[str, str, str]:
    th = row.get("thread_id") or THREAD_ID
    ss = row.get("session_id") or SESSION_ID
    dv = row.get("device_id") or DEVICE_ID
    return str(th), str(ss), str(dv)

def _extract_tool_runner_output_from_stream(body: Any) -> str:
    # If server already gave us dict (non-stream error), handle it
    if isinstance(body, dict):
        if body.get("type") == "step_update":
            data = body.get("data") or {}
            if data.get("step") == "tool_runner" and "output" in data:
                return str(data["output"])
        # if it has direct "output"
        if "output" in body:
            out = body.get("output")
            return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        return ""

    if not isinstance(body, str):
        try:
            return json.dumps(body, ensure_ascii=False)
        except Exception:
            return str(body)

    lines = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("data:")]
    if not lines:
        return ""  # not an SSE-like body

    parsed = []
    for ln in lines:
        json_part = ln[len("data:"):].strip()
        if json_part.startswith('"') and json_part.endswith('"'):
            try:
                json_part = json.loads(json_part)  # unescape once
            except Exception:
                pass
        try:
            obj = json.loads(json_part)
            parsed.append(obj)
        except Exception:
            try:
                fixed = json_part.replace('""', '"')
                obj = json.loads(fixed)
                parsed.append(obj)
            except Exception:
                continue

    # Prefer the most recent tool_runner step_update before completed
    for obj in reversed(parsed[:-1] if parsed else []):
        if obj.get("type") == "step_update":
            d = obj.get("data") or {}
            if d.get("step") == "tool_runner" and "output" in d:
                out = d.get("output")
                return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)

    # Fallback: second-last event's 'output'
    if len(parsed) >= 2:
        obj = parsed[-2]
        d = obj.get("data") or {}
        out = d.get("output")
        if out is not None:
            return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)

    return ""

def prepare_and_send(query_text: str, row_ids: Dict[str, str], retries: int = RETRIES) -> Tuple[Any, Any]:
    """
    Send a single query payload with retries/backoff.
    Returns (status, body) where body is str or dict.
    """
    # Match Swagger exactly by default:
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "python-requests/2.32.5",
        "Authorization": JWT,  # <-- hard-coded token
    }

    thread_id, session_id, device_id = _normalize_ids(row_ids)

    payload = {
        "thread_id": thread_id,
        "session_id": session_id,
        "content": [{"type": "text", "text": query_text}],
        "device_id": device_id,
    }

    req = Request(
        "POST",
        f"{BASE_URL.rstrip('/')}/{ENDPOINT.lstrip('/')}",
        headers=headers,
        json=payload
    )
    prepared = client.session.prepare_request(req)

    # redact Authorization for logs
    safe_headers = dict(prepared.headers)
    if "Authorization" in safe_headers:
        try:
            scheme, _ = safe_headers["Authorization"].split(" ", 1)
            safe_headers["Authorization"] = f"{scheme} [REDACTED]"
        except Exception:
            safe_headers["Authorization"] = "[REDACTED]"

    logger.info("PREPARED %s %s", prepared.method, prepared.url)
    for k, v in safe_headers.items():
        logger.debug("REQ-HEADER %s: %s", k, v)

    body_preview = prepared.body
    try:
        if isinstance(body_preview, bytes):
            body_preview = body_preview.decode("utf-8", errors="ignore")
    except Exception:
        body_preview = str(body_preview)
    logger.debug("REQ-BODY: %s", body_preview)

    attempt = 0
    attempt_timeout = float(client.timeout or BASE_TIMEOUT) if client.timeout else BASE_TIMEOUT

    while True:
        attempt += 1
        try:
            t0 = time.time()
            resp = client.session.send(prepared, timeout=attempt_timeout, stream=False)
            elapsed = time.time() - t0
            logger.info("Attempt %d -> status=%s elapsed=%.2fs timeout=%.1fs",
                        attempt, resp.status_code, elapsed, attempt_timeout)

            ctype = (resp.headers.get("Content-Type") or "").lower()
            text_body = None
            data_body = None

            # Try JSON first (Swagger returns JSON)
            try:
                data_body = resp.json()
            except Exception:
                text_body = resp.text

            # If server actually streamed SSE with application/event-stream
            if "text/event-stream" in ctype:
                body_text = text_body if text_body is not None else resp.text
                return resp.status_code, body_text

            # Otherwise return whichever we have
            return resp.status_code, (data_body if data_body is not None else (text_body or ""))

        except req_exceptions.ReadTimeout as e:
            logger.warning("Attempt %d ReadTimeout after %.1fs: %s", attempt, attempt_timeout, str(e))
            if attempt > retries:
                logger.error("Exceeded retries (%d) -> TIMEOUT", retries)
                return "TIMEOUT", f"ReadTimeout after {attempt_timeout}s: {str(e)}"
            backoff = min(0.5 * (2 ** (attempt - 1)), 8.0)
            attempt_timeout = min(attempt_timeout * 1.75, MAX_TIMEOUT)
            logger.info("Sleeping %.2fs then retrying (next timeout=%.1fs)", backoff, attempt_timeout)
            time.sleep(backoff)
            continue

        except req_exceptions.ConnectionError as e:
            logger.warning("Attempt %d ConnectionError: %s", attempt, str(e))
            if attempt > retries:
                logger.error("Exceeded retries (%d) -> REQUEST_ERROR", retries)
                return "REQUEST_ERROR", str(e)
            backoff = min(0.5 * (2 ** (attempt - 1)), 8.0)
            logger.info("Sleeping %.2fs then retrying connection", backoff)
            time.sleep(backoff)
            continue

        except Exception as e:
            logger.exception("Unexpected exception: %s", str(e))
            return "REQUEST_ERROR", str(e)

# ---------- Main flow (batched) ----------
def main():
    queries = load_queries(CSV_PATH)
    total = len(queries)
    logger.info("Loaded %d queries from %s", total, CSV_PATH)

    start = max(0, START_INDEX)
    if start >= total:
        logger.error("START_INDEX (%d) >= total queries (%d). Nothing to do.", start, total)
        return

    results: List[Dict[str, Any]] = []
    slice_to_process = queries[start:]
    batches = list(chunked(slice_to_process, BATCH_SIZE))
    logger.info("Processing %d batches (batch_size=%d) starting at index %d", len(batches), BATCH_SIZE, start)

    processed_count = start
    for batch_idx, batch in enumerate(batches, start=0):
        logger.info("Starting batch %d/%d (queries %d..%d)",
                    batch_idx+1, len(batches), processed_count+1, processed_count+len(batch))

        for q in batch:
            qid = q.get("id", "")
            qtext = q.get("query", "")
            logger.info("Running query id=%s: %s", qid, qtext[:120])

            row_ids = {
                "thread_id": q.get("thread_id") or "",
                "session_id": q.get("session_id") or "",
                "device_id": q.get("device_id") or "",
            }

            status, body = prepare_and_send(qtext, row_ids=row_ids, retries=RETRIES)

            # If SSE-like, parse for second-last tool_runner output
            if isinstance(body, str) and body.strip().startswith("data:"):
                parsed_output = _extract_tool_runner_output_from_stream(body)
                body_text = body
            else:
                parsed_output = _extract_tool_runner_output_from_stream(body)
                try:
                    body_text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, default=str)
                except Exception:
                    body_text = str(body)

            entry = {
                "id": qid,
                "query": qtext,
                "status": status,
                "resp_text": parsed_output,
                "body": body_text,
                "timestamp": time.time(),
                "date": datetime.now(timezone.utc).isoformat(),
            }
            results.append(entry)
            processed_count += 1

            time.sleep(0.1)

        write_json(results, OUT_JSON)
        write_csv(results, OUT_CSV)

        if batch_idx < len(batches) - 1:
            logger.info("Batch %d complete. Sleeping %.1fs before next batch...", batch_idx+1, BATCH_DELAY)
            time.sleep(BATCH_DELAY)

    logger.info("All batches processed. Total results: %d", len(results))
    logger.info("Final JSON: %s", OUT_JSON)
    logger.info("Final CSV: %s", OUT_CSV)

    try:
        combined = combine_all_csv_reports(REPORTS_DIR)
        if combined and combined.exists():
            logger.info("Combined CSV produced: %s", combined)
        else:
            logger.warning("No combined CSV produced.")
    except Exception:
        logger.exception("Combining CSV reports failed.")

if __name__ == "__main__":
    main()
