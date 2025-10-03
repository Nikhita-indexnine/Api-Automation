#!/usr/bin/env python3
"""
GET request runner for the Agent Inventory API with CSV export.

- Hits /api/v1/agent/inventory
- Uses hardcoded JWT (replace if expired)
- Prints JSON response to console
- Saves response into ./reports/inventory_results_TIMESTAMP.csv
"""

import requests
import json
import os
import csv
import time
from datetime import datetime
from pathlib import Path

BASE_URL = os.environ.get("BASE_URL", "http://35.226.27.129:8000")
ENDPOINT = "/api/v1/agent/inventory"

# ✅ Replace if expired
JWT = (
    "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoxLCJvcmdhbml6YXRpb25faWQiOjEsImV4cCI6MTc1OTM4ODE4NiwidHlwZSI6ImFjY2VzcyJ9.bshRFjEkLYRjiNvQx_wIB-V_0emkIXZ823rwgYnB3nZR4sjJEK2uxf7UzC0gyiLJK0f05iAghfE4Yq7t_lOEN7UKbPOyP7HYZiXcsDHLBLykOPVhNNs8t1JKw0CLlFff5ZlJQqx1zAMme4J6bj0oP8zlmfHnAGaQmdX_I7ZMDZm0J8Nm5nC6D-HC3EbvKeLHq8ETVVjUpSpd4HMFfOURn2oliS9J1S3zkRU1e03SnpCwhn5OEE4NNaagjrHt-kUzpY9GweXMbBJBQote1UPlI1KIMZVLtVuQBkDxActg5a3oCLugDGKbfr0H-yDkdE02SGmknEeqlsTh2W3dtecfwA"
)

url = f"{BASE_URL.rstrip('/')}{ENDPOINT}"

headers = {
    "accept": "application/json",
    "Authorization": f"Bearer {JWT}",
}

# ---------- REPORT PATH ----------
REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

TIMESTAMP = time.strftime("%Y%m%d-%H%M%S")
OUT_CSV = REPORTS_DIR / f"inventory_results_{TIMESTAMP}.csv"

try:
    resp = requests.get(url, headers=headers, timeout=60)
    print("Status:", resp.status_code)

    # Try parse as JSON
    try:
        body = resp.json()
        print("Response JSON:", json.dumps(body, indent=2))
    except Exception:
        body = {"raw_text": resp.text}
        print("Response Text:", resp.text)

    # ---------- WRITE CSV ----------
    # If body is a dict with a list (like inventory: [...]), flatten rows
    rows = []
    if isinstance(body, dict):
        # If API returns something like {"inventory": [ ... ]}
        for key, val in body.items():
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        row = {**item}
                        row["__parent_key__"] = key
                        rows.append(row)
            else:
                rows.append({key: val})
    elif isinstance(body, list):
        for item in body:
            rows.append(item if isinstance(item, dict) else {"value": item})
    else:
        rows.append({"value": str(body)})

    if rows:
        # Collect all fieldnames across all rows
        fieldnames = sorted({k for row in rows for k in row.keys()})
        with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print("WROTE CSV:", OUT_CSV)
    else:
        print("No rows to write in CSV.")

except Exception as e:
    print("Request failed:", str(e))
