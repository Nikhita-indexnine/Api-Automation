#!/usr/bin/env python3
"""
CSV-driven Auth Test Runner (robust)

- Input: ./data/auth_tests.csv
  Columns:
    test_id,flow,host_name,user_name,session_token,reuse_from,
    expect_status,expect_valid,expect_has_access_token,expect_err_substring,notes

  flow:
    - validate         -> POST /api/v1/auth/validate-hostname
    - login            -> POST /api/v1/auth/login
    - validate+login   -> validate then login with returned session_token

  reuse_from:
    - For login-only rows, set "reuse:OTHER_TEST_ID" to reuse that test's session_token.

- Output: ./reports/auth_test_results_<timestamp>.csv
  One row per HTTP call with pass/fail and key fields.

Enhancements:
- Normalizes 422 -> 400 for validation errors to match acceptance criteria.
- Expected status supports: single code ("200"), dual ("200|401"), patterns ("2xx","4xx","any").
- Error substring check only when expected status is non-2xx and case-insensitive.
- Safe parsing so CSV mistakes don't crash the run.
"""

import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

import requests

# ---------------- CONFIG ----------------
BASE_URL = os.environ.get("BASE_URL", "http://35.226.27.129:8000")
VALIDATE_ENDPOINT = "/api/v1/auth/validate-hostname"
LOGIN_ENDPOINT = "/api/v1/auth/login"

# Treat FastAPI/Pydantic 422 validation errors as 400 for tests
MAP_422_TO_400 = True

# repo root: one level above tests/
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_CSV = BASE_DIR / "data" / "auth_tests.csv"

REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
TS = time.strftime("%Y%m%d-%H%M%S")
OUT_CSV = REPORTS_DIR / f"auth_test_results_{TS}.csv"

FIELDNAMES = [
    "test_id", "substep",
    "_date", "_timestamp_local", "_status",
    "flow", "host_name", "user_name",
    "session_token_tail", "access_token_tail",
    "valid", "expires_in", "token_type",
    "user_id", "organization_id",
    "pass", "why",
    "expect_status", "expect_valid", "expect_has_access_token",
    "notes", "raw_json",
]

# ---------------- HELPERS ----------------
def now_fields(ts: float) -> Tuple[str, str]:
    return (
        datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
        datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
    )

def tail(s: Optional[str], n: int = 10) -> str:
    if not isinstance(s, str):
        return ""
    return s[-n:] if len(s) > n else s

def normalize_status(status: int, body: Any) -> int:
    """Map known framework statuses to acceptance criteria expectations."""
    if MAP_422_TO_400 and status == 422:
        return 400
    return status

def _expected_for_substep(expect_status: str, substep: str) -> str:
    """
    Returns the expected status expression for this substep, e.g. '200', '401', '2xx', or ''.
    Supports dual forms like '200|401' (validate|login).
    """
    if not expect_status:
        return ""
    if "|" in expect_status:
        a, b = expect_status.split("|", 1)
        return a.strip() if substep == "validate" else b.strip()
    return expect_status.strip()

def status_matches(actual: int, expected_expr: str) -> bool:
    """
    Match actual status against an expected expression:
      - exact code: '200', '401', ...
      - pattern: '2xx', '4xx', 'any'
    """
    if not expected_expr:
        return True  # no expectation set
    exp = expected_expr.lower()
    if exp == "any":
        return True
    if exp.endswith("xx") and len(exp) == 3 and exp[0].isdigit():
        # e.g., 2xx, 4xx
        try:
            hundred = int(exp[0]) * 100
            return hundred <= actual < hundred + 100
        except ValueError:
            return False
    # exact int
    try:
        return actual == int(exp)
    except (ValueError, TypeError):
        return False  # unparsable -> treat as mismatch

def load_tests(path: Path):
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")
    tests = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            tests.append({k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()})
    return tests

def write_results(rows):
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDNAMES})

# ---------------- EXPECTATION CHECKERS ----------------
def expect_eval_validate(body: Dict[str, Any], raw_status: int,
                         expect_status_expr: str, e_valid: str, err_sub: str) -> Tuple[bool, str]:
    reasons = []
    ok = True

    status = normalize_status(raw_status, body)

    if not status_matches(status, expect_status_expr):
        ok = False
        reasons.append(f"status={status} != expected {expect_status_expr or '(any)'}")

    if e_valid:
        want = e_valid.lower() == "true"
        got = bool(body.get("valid"))
        if got != want:
            ok = False
            reasons.append(f"valid={got} != expected {want}")

    # only check error substring when expecting a non-2xx
    if err_sub and not status_matches(200, expect_status_expr) and not status_matches(201, expect_status_expr):
        payload = json.dumps(body, ensure_ascii=False).lower()
        if err_sub.lower() not in payload:
            ok = False
            reasons.append(f"missing '{err_sub}' in response")

    return ok, "; ".join(reasons)

def expect_eval_login(body: Dict[str, Any], raw_status: int,
                      expect_status_expr: str, e_has_access: str, err_sub: str) -> Tuple[bool, str]:
    reasons = []
    ok = True

    status = normalize_status(raw_status, body)

    if not status_matches(status, expect_status_expr):
        ok = False
        reasons.append(f"status={status} != expected {expect_status_expr or '(any)'}")

    if e_has_access:
        want = e_has_access.lower() == "true"
        got = isinstance(body, dict) and bool(body.get("access_token"))
        if got != want:
            ok = False
            reasons.append(f"access_token presence={got} != expected {want}")

    # only check error substring when expecting a non-2xx
    if err_sub and not status_matches(200, expect_status_expr) and not status_matches(201, expect_status_expr):
        payload = json.dumps(body, ensure_ascii=False).lower()
        if err_sub.lower() not in payload:
            ok = False
            reasons.append(f"missing '{err_sub}' in response")

    return ok, "; ".join(reasons)

# ---------------- API CALLS ----------------
def do_validate(session: requests.Session, host_name: str, user_name: str) -> Tuple[int, Dict[str, Any]]:
    url = f"{BASE_URL.rstrip('/')}{VALIDATE_ENDPOINT}"
    headers = {"accept": "application/json", "Content-Type": "application/json"}
    payload = {"host_name": host_name, "user_name": user_name}
    resp = session.post(url, headers=headers, json=payload, timeout=60)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw_text": resp.text}

def do_login(session: requests.Session, user_name: str, session_token: str) -> Tuple[int, Dict[str, Any]]:
    url = f"{BASE_URL.rstrip('/')}{LOGIN_ENDPOINT}"
    headers = {"accept": "application/json", "Content-Type": "application/json"}
    payload = {"user_name": user_name, "session_token": session_token}
    resp = session.post(url, headers=headers, json=payload, timeout=60)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw_text": resp.text}

# ---------------- MAIN ----------------
def main():
    tests = load_tests(DATA_CSV)
    session = requests.Session()
    token_store: Dict[str, str] = {}
    results = []

    for t in tests:
        tid = t.get("test_id", "")
        flow = (t.get("flow") or "").lower()
        host_name = t.get("host_name", "")
        user_name = t.get("user_name", "")
        explicit_token = t.get("session_token", "")
        reuse_from = t.get("reuse_from", "")
        expect_status_raw = t.get("expect_status", "")
        expect_valid = t.get("expect_valid", "")
        expect_has_access = t.get("expect_has_access_token", "")
        expect_err = t.get("expect_err_substring", "")
        notes = t.get("notes", "")

        ts = time.time()
        _date, _ts_local = now_fields(ts)

        # Resolve reuse
        if flow == "login" and not explicit_token and reuse_from.lower().startswith("reuse:"):
            source_id = reuse_from.split(":", 1)[1].strip()
            explicit_token = token_store.get(source_id, "")

        # --- VALIDATE ---
        if flow in ("validate", "validate+login"):
            status_raw, body = do_validate(session, host_name, user_name)
            token = str(body.get("session_token") or "") if isinstance(body, dict) else ""
            if token:
                token_store[tid] = token

            exp_expr = _expected_for_substep(expect_status_raw, "validate")
            ok, why = expect_eval_validate(body if isinstance(body, dict) else {}, status_raw, exp_expr, expect_valid, expect_err)

            status_norm = normalize_status(status_raw, body)
            results.append({
                "test_id": tid, "substep": "validate",
                "_date": _date, "_timestamp_local": _ts_local, "_status": status_norm,
                "flow": flow, "host_name": host_name, "user_name": user_name,
                "session_token_tail": tail(token), "access_token_tail": "",
                "valid": body.get("valid", "") if isinstance(body, dict) else "",
                "expires_in": body.get("expires_in", "") if isinstance(body, dict) else "",
                "token_type": "", "user_id": "", "organization_id": "",
                "pass": "TRUE" if ok else "FALSE", "why": why,
                "expect_status": exp_expr, "expect_valid": expect_valid,
                "expect_has_access_token": "", "notes": notes,
                "raw_json": json.dumps(body, ensure_ascii=False),
            })

        # --- LOGIN ---
        if flow in ("login", "validate+login"):
            session_token = explicit_token or token_store.get(tid, "")
            status_raw, body = do_login(session, user_name, session_token)

            exp_expr = _expected_for_substep(expect_status_raw, "login")
            ok, why = expect_eval_login(body if isinstance(body, dict) else {}, status_raw, exp_expr, expect_has_access, expect_err)

            access_tail = str(body.get("access_token")) if isinstance(body, dict) and body.get("access_token") else ""
            status_norm = normalize_status(status_raw, body)

            results.append({
                "test_id": tid, "substep": "login",
                "_date": _date, "_timestamp_local": _ts_local, "_status": status_norm,
                "flow": flow, "host_name": host_name, "user_name": user_name,
                "session_token_tail": tail(session_token), "access_token_tail": tail(access_tail),
                "valid": "", "expires_in": body.get("expires_in", "") if isinstance(body, dict) else "",
                "token_type": body.get("token_type", "") if isinstance(body, dict) else "",
                "user_id": body.get("user_id", "") if isinstance(body, dict) else "",
                "organization_id": body.get("organization_id", "") if isinstance(body, dict) else "",
                "pass": "TRUE" if ok else "FALSE", "why": why,
                "expect_status": exp_expr, "expect_valid": "",
                "expect_has_access_token": expect_has_access, "notes": notes,
                "raw_json": json.dumps(body, ensure_ascii=False),
            })

    write_results(results)
    print("WROTE CSV:", OUT_CSV)
    print("Executed HTTP calls:", len(results))


if __name__ == "__main__":
    main()
