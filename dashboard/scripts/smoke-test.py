"""Check a deployed dashboard without printing or saving confidential records.

Usage: python3 smoke-test.py https://dashboard-host /absolute/path/to/access.env
The credentials file must contain DASHBOARD_USERNAME and DASHBOARD_PASSWORD.
"""
import base64
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

base = sys.argv[1].rstrip("/")
credentials = dict(line.split("=", 1) for line in Path(sys.argv[2]).read_text().splitlines() if line and not line.startswith("#"))
token = base64.b64encode((credentials["DASHBOARD_USERNAME"] + ":" + credentials["DASHBOARD_PASSWORD"]).encode()).decode()

for path, headers in [
    ("/", {}),
    ("/api/dashboard", {}),
    ("/?_rsc=check", {"RSC": "1"}),
    ("/api/dashboard", {"Authorization": "Basic d3Jvbmc6d3Jvbmc="}),
    ("/api/dashboard", {"x-middleware-subrequest": "proxy:proxy:proxy:proxy:proxy"}),
]:
    try:
        urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=30)
        raise AssertionError("Request without valid login was allowed")
    except urllib.error.HTTPError as error:
        assert error.code == 401
        assert "no-store" in error.headers["Cache-Control"]

assert json.load(urllib.request.urlopen(base + "/healthz", timeout=30))["status"] == "ok"
with urllib.request.urlopen(urllib.request.Request(base + "/api/dashboard", headers={"Authorization": "Basic " + token}), timeout=30) as response:
    assert "no-store" in response.headers["Cache-Control"]
    data = json.load(response)
    assert data["asOf"] and data["year"]["transactions"] > 0
    assert isinstance(data["employees"], list) and isinstance(data["decisions"], list)
    assert data["forecast"] is None or len(data["forecast"]["points"]) > 0

with urllib.request.urlopen(urllib.request.Request(base + "/", headers={"Authorization": "Basic " + token}), timeout=30) as response:
    html = response.read().decode()
    assert "Your finance overview" in html
    assert "Synthetic demo balance" not in html

print("PASS: login protection, RSC protection, no-store headers, health probe, database response and rendered dashboard.")
