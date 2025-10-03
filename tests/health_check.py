#!/usr/bin/env python3
"""
GET /api/health

- Simple health check endpoint
- Prints JSON response
- Saves response to ./reports/health_<timestamp>.csv
"""

import requests
import json
import time
import csv
from datetime import datetime
from pathlib import Path

BASE_URL = "http://35.226.27.129:8000"
ENDPOINT = "/api/health"

url = f"{BASE_URL.rstrip('/')}{ENDPOINT}"
headers = {
    "accept": "application/json",
}

# ---------- REPORT PATH ----------
REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
TIMESTAMP = time.strftime("%Y%m%d-%H%M%S")
OUT_CSV = REPORTS_DIR / f"health_{TIMESTAMP}.csv"

try:
    resp = requests.get(url, headers=headers, timeout=30)
    print("Status:", resp.status_code)

    try:
        body = resp.json()
        print("Response JSON:", json.dumps(body, indent=2))
    except Exception:
        body = {"raw_text": resp.text}
        print("Response Text:", resp.text)

    # ---------- WRITE CSV ----------
    ts = time.time()
    row = dict(body) if isinstance(body, dict) else {"raw_text": str(body)}
    row["_status"] = resp.status_code
    row["_date"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    row["_timestamp_local"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    fieldnames = sorted(row.keys())
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerow(row)

    print("WROTE CSV:", OUT_CSV)

except Exception as e:
    print("Request failed:", str(e))
