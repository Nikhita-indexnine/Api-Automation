# api_client.py - minimal HTTP client wrapper around requests
import requests

class APIClient:
    def __init__(self, base_url, timeout=30):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.timeout = timeout

    def _url(self, endpoint):
        return f"{self.base_url}/{endpoint.lstrip('/')}"

    def post(self, endpoint, json_payload=None, data=None, headers=None):
        url = self._url(endpoint)
        if json_payload is not None:
            return self.session.post(url, json=json_payload, headers=headers, timeout=self.timeout)
        else:
            return self.session.post(url, data=data, headers=headers, timeout=self.timeout)

    def get(self, endpoint, params=None, headers=None):
        url = self._url(endpoint)
        return self.session.get(url, params=params, headers=headers, timeout=self.timeout)
