#!/usr/bin/env python3
"""
CSV-driven runner for the Feedback API.

- Reads CSV at ./data/feedback_testcases.csv with columns:
    id,thread_id,message_id,rating,comment
  (message_id optional; rating numeric 1 or -1)
- Posts each row to BASE_URL + ENDPOINT (/api/v1/feedback/feedback)
- JWT is hardcoded (replace if expired)
- Produces JSON and CSV reports in ./reports/
"""

import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path
import requests

# ---------- CONFIG ----------
BASE_URL = os.environ.get("BASE_URL", "http://35.226.27.129:8000")
ENDPOINT = os.environ.get("ENDPOINT", "/api/v1/feedback/feedback")

# ✅ Embedded working JWT (replace if expired)
JWT = (
    "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoiMSIsInVzZXJuYW1lIjoiQ2xvdWRPd25lckBndmUubG9jYWwiLCJleHAiOjE3NTg4NzAzNjMsInR5cGUiOiJhY2Nlc3MifQ.bw2XhvtXWsYr2aHgF3cfd5d7C-ovXVLAndVhf5O8dHHdVcoh7m-bij4sCyw1_JL1pDMNtOTp8Dq_Rtcg7K4hV2JCjq6hVrnFQtsioN1QIUGXqN3kflhuwtmK3Ake7GB28WWEg-h2Y7Pebd8Qcztmz7mrPFlxIfSCZMJr-H9540zBYTT8sYaRL1Zy-SwqF8aVBihXKWhJnL5NkjkmGxIW5mhlXnoM5SLPmJZe_R_oAwOzAK4kXn4LZYTPM4LiJz2WHPL9vbFhNZ6sPSh9RpJZQ9ZGUNu62zo_MzT-8osjlluUdmTtIpMpLTcJFfNAIAGAqNnTgnkZMIjXKaqlfgcwaQ"
)

TIMEOUT = int(os.environ.get("TIMEOUT", "60"))

# ---------- PATHS ----------
HERE = Path(__file__).resolve().parent.parent if Path(__file__).parent.parent.exists() else Path(__file__).parent
CSV_PATH = HERE / "data" / "feedback_testcases.csv"
REPORTS_DIR = HERE / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

TIMESTAMP = time.strftime("%Y%m%d-%H%M%S")
OUT_JSON = REPORTS_DIR / f"feedback_results_{TIMESTAMP}.json"
OUT_CSV = REPORTS_DIR / f"feedback_results_{TIMESTAMP}.csv"

# ---------- HELPER ----------
def extract_response_fields(body):
    """Normalize response into dict for CSV columns."""
    out = {
        "resp_id": "",
        "resp_user_id": "",
        "resp_thread_id": "",
        "resp_message_id": "",
        "resp_rating": "",
        "resp_comment": "",
        "resp_created_at": "",
    }
    if isinstance(body, dict):
        out["resp_id"] = body.get("id", "")
        out["resp_user_id"] = body.get("user_id", "")
        out["resp_thread_id"] = body.get("thread_id", "")
        out["resp_message_id"] = body.get("message_id", "")
        out["resp_rating"] = body.get("rating", "")
        out["resp_comment"] = body.get("comment", "")
        out["resp_created_at"] = body.get("created_at", "") or body.get("createdAt", "")
    return out

# ---------- LOAD CSV ----------
if not CSV_PATH.exists():
    raise SystemExit(f"CSV file not found: {CSV_PATH}")

testcases = []
with CSV_PATH.open("r", encoding="utf-8", newline="") as fh:
    reader = csv.DictReader(fh)
    for row in reader:
        tid = (row.get("id") or "").strip()
        thread_id = (row.get("thread_id") or "").strip()
        message_id = (row.get("message_id") or "").strip()
        rating = (row.get("rating") or "").strip()
        comment = (row.get("comment") or "").strip()
        if not (thread_id or message_id or rating or comment):
            continue
        testcases.append({
            "id": tid,
            "thread_id": thread_id,
            "message_id": message_id,
            "rating": rating,
            "comment": comment
        })

if not testcases:
    raise SystemExit("No feedback testcases found in CSV.")

# ---------- SESSION ----------
session = requests.Session()
url = f"{BASE_URL.rstrip('/')}/{ENDPOINT.lstrip('/')}"

headers = {
    "accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "python-requests/2.32.5",
}
if JWT:
    headers["Authorization"] = JWT if JWT.lower().startswith("bearer ") else f"Bearer {JWT}"

# ---------- RUN ----------
results = []
for tc in testcases:
    tc_id = tc["id"]
    payload = {"thread_id": tc["thread_id"]}
    if tc["rating"] != "":
        try:
            payload["rating"] = int(tc["rating"])
        except ValueError:
            payload["rating"] = tc["rating"]
    if tc["message_id"]:
        payload["message_id"] = tc["message_id"]
    if tc["comment"] != "":
        payload["comment"] = tc["comment"]

    print(f"Running {tc_id}: payload={payload}")

    try:
        resp = session.post(url, json=payload, headers=headers, timeout=TIMEOUT)
    except Exception as e:
        status = "REQUEST_ERROR"
        body = str(e)
    else:
        status = resp.status_code
        try:
            body = resp.json()
            print()
        except Exception:
            body = resp.text

    ts = time.time()
    now_iso = datetime.utcnow().isoformat() + "Z"

    extracted = extract_response_fields(body) if isinstance(body, dict) else extract_response_fields({})

    entry = {
        "test_id": tc_id,
        "payload": payload,
        "status": status,
        "body": body,
        "resp_fields": extracted,
        "timestamp": ts,
        "date_iso": now_iso
    }
    results.append(entry)

    time.sleep(0.1)

# ---------- WRITE JSON ----------
OUT_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print("WROTE JSON:", OUT_JSON)

# ---------- WRITE CSV ----------
with OUT_CSV.open("w", encoding="utf-8", newline="") as csvfh:
    fieldnames = [
        "test_id", "status", "date", "timestamp_local",
        "thread_id", "message_id", "rating", "comment",
        "resp_id", "resp_user_id", "resp_thread_id", "resp_message_id",
        "resp_rating", "resp_comment", "resp_created_at",
        "body_raw"
    ]
    writer = csv.DictWriter(csvfh, fieldnames=fieldnames)
    writer.writeheader()
    for r in results:
        ts = float(r.get("timestamp", time.time()))
        date_val = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        timestamp_human = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

        payload = r.get("payload", {})
        resp_fields = r.get("resp_fields", {})

        body_raw = r["body"]
        if not isinstance(body_raw, str):
            try:
                body_raw = json.dumps(body_raw, ensure_ascii=False)
            except Exception:
                body_raw = str(body_raw)

        writer.writerow({
            "test_id": r.get("test_id", ""),
            "status": r.get("status", ""),
            "date": date_val,
            "timestamp_local": timestamp_human,
            "thread_id": payload.get("thread_id", ""),
            "message_id": payload.get("message_id", ""),
            "rating": payload.get("rating", ""),
            "comment": payload.get("comment", ""),
            "resp_id": resp_fields.get("resp_id", ""),
            "resp_user_id": resp_fields.get("resp_user_id", ""),
            "resp_thread_id": resp_fields.get("resp_thread_id", ""),
            "resp_message_id": resp_fields.get("resp_message_id", ""),
            "resp_rating": resp_fields.get("resp_rating", ""),
            "resp_comment": resp_fields.get("resp_comment", ""),
            "resp_created_at": resp_fields.get("resp_created_at", ""),
            "body_raw": body_raw
        })

print("WROTE CSV:", OUT_CSV)
print("Done. Tests executed:", len(results))
