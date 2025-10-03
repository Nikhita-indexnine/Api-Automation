# VM / vSphere Query Test Repo

This repository contains pytest-based tests and helpers to exercise a vSphere-style
message API endpoint (`/api/v1/agent/message`) by sending natural-language queries
(e.g. "List VMs with CPU utilization above 80%...") and validating responses.

Structure:
- api_client.py           : thin wrapper around requests to centralize calls
- utils/payload_loader.py : CSV loader helper (sample)
- conftest.py             : pytest fixtures (config, logger)
- tests/                  : pytest test files
  - test_agent_message_csv.py   : original CSV-driven test harness (your working script)
  - test_message_api.py         : TC-001..TC-010 message tests
  - test_message_api_queries.py : parametrized tests sending many queries from dataset
- data/testcases.csv      : sample CSV with queries (and a couple example rows)
- requirements.txt        : Python deps

How to run:
1. Create a virtualenv and install requirements:
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt

2. Set environment variables or create a `tests/config.json` file with:
   {
     "BASE_URL": "https://your.api.server",
     "JWT": "Bearer <token>",
     "TIMEOUT": 30
   }

3. Run pytest:
   pytest -q

Notes:
- This repo is a starting point. Adjust APIClient, config, and test expectations
  to match your real API behavior.
