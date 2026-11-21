"""Characterize liveness through the original FastAPI route, without replacing its implementation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


def health_json() -> str:
    from fastapi.testclient import TestClient
    from cfo.api.app import app
    return TestClient(app).get("/healthz").text
