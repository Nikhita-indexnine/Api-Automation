# utils/payload_loader.py - simple CSV loader that yields parsed_request JSON
import csv
import json
import os
import logging

def get_logger(name: str = "api-tests"):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

def load_payload_from_csv(csv_path):
    rows = []
    with open(csv_path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh, delimiter='\t')
        for r in reader:
            # Attempt to parse Sample_Request column as JSON if present
            sample = r.get('Sample_Request') or r.get('SampleRequest') or r.get('Sample')
            parsed = None
            if sample:
                try:
                    parsed = json.loads(sample)
                except Exception:
                    parsed = None
            rows.append({
                'TestCaseID': r.get('ID') or r.get('TestCaseID') or '',
                'row': r,
                'parsed_request': parsed
            })
    return rows
