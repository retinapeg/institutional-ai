import pytest
from fastapi.testclient import TestClient

from institutional_ai.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def test_health_and_dashboard(client):
    assert client.get("/health").json() == {
        "status": "ok",
        "provider": "deterministic",
        "mode": "deterministic",
    }

    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    for section in (
        "organisation",
        "workers",
        "reports",
        "disagreements",
        "organisation-changes",
        "director-report",
        "audit-log",
    ):
        assert f'id="{section}"' in response.text


def test_project_lifecycle_and_latest(client):
    response = client.post("/api/projects", json={"mission": "Optimise the test fleet."})
    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "CREATED"

    response = client.post(f"/api/projects/{created['id']}/run")
    assert response.status_code == 200
    complete = response.json()
    assert complete["status"] == "COMPLETE"
    assert complete["workers"]
    assert complete["reports"]
    assert complete["reviews"]
    assert any(item["status"] == "APPROVED" for item in complete["role_requests"].values())
    assert complete["director_report"]["disagreements"]
    assert complete["audit_events"]
    assert client.get(f"/api/projects/{created['id']}").json() == complete
    assert client.get("/api/projects/latest").json() == complete


def test_current_and_historical_organisation(client):
    created = client.post("/api/projects", json={"mission": "Map the organisation."}).json()
    complete = client.post(f"/api/projects/{created['id']}/run").json()

    path = f"/api/projects/{created['id']}/organisation"
    current = client.get(path)
    assert current.status_code == 200
    assert current.json() == complete["graph_edges"]

    first_edge_event = min(edge["valid_from_event"] for edge in current.json())
    historical = client.get(path, params={"at_event": first_edge_event - 1})
    assert historical.status_code == 200
    assert historical.json() == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/projects/missing"),
        ("post", "/api/projects/missing/run"),
        ("get", "/api/projects/missing/organisation"),
    ],
)
def test_missing_project_returns_404(client, method, path):
    assert getattr(client, method)(path).status_code == 404


@pytest.mark.parametrize(
    "payload", [{}, {"mission": ""}, {"mission": "   "}, {"mission": "x" * 2_001}]
)
def test_invalid_mission_returns_422(client, payload):
    assert client.post("/api/projects", json=payload).status_code == 422
