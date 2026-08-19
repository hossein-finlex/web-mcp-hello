"""HTTP surface. These assertions are the frontend's contract — do not loosen them."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(book):
    from app.main import create_app

    # lifespan is skipped: the fixtures own the schema and the data.
    return TestClient(create_app())


def test_health_reports_the_shape_the_frontend_reads(client):
    body = client.get("/api/health").json()
    for key in ("ok", "model", "mock", "credentials", "database", "contracts", "server_tools"):
        assert key in body, key
    assert body["contracts"] == 6
    assert set(body["server_tools"]) == {
        "run_renewal_batch",
        "generate_renewal_report",
        "benchmark_rates",
    }


def test_list_is_ordered_by_expiry(client):
    contracts = client.get("/api/contracts").json()["contracts"]
    ends = [c["end_date"] for c in contracts]
    assert ends == sorted(ends)
    assert {"status", "days_to_expiry"} <= set(contracts[0])


def test_search_binds_query_parameters_to_the_filter_model(client):
    body = client.get(
        "/api/contracts/search",
        params={"product": "Cyber", "sort_by": "premium", "sort_dir": "desc", "limit": 1},
    ).json()
    assert body["returned"] == 1
    assert body["total"] == 2, "total counts matches before the limit"
    assert body["sort"] == {"by": "premium", "dir": "desc"}
    assert body["contracts"][0]["id"] == "FL-0002"


def test_search_route_is_not_shadowed_by_the_id_route(client):
    """`/api/contracts/search` must not be read as a contract called "search"."""
    assert client.get("/api/contracts/search").status_code == 200


@pytest.mark.parametrize(
    "params",
    [
        {"product": "Aviation"},
        {"status": "nonsense"},
        {"expiring_within_days": -1},
        {"sort_by": "notes"},
        {"sort_dir": "sideways"},
    ],
)
def test_search_rejects_bad_parameters(client, params):
    assert client.get("/api/contracts/search", params=params).status_code == 422


def test_summary_aggregates(client):
    body = client.get("/api/summary", params={"group_by": "product"}).json()
    assert body["totals"]["contracts"] == 6
    assert any(g["key"] == "Cyber" and g["contracts"] == 2 for g in body["groups"])


def test_summary_rejects_bad_grouping(client):
    assert client.get("/api/summary", params={"group_by": "notes"}).status_code == 422


def test_crud_round_trip(client):
    created = client.post(
        "/api/contracts",
        json={
            "insured_company": "Roundtrip GmbH",
            "product": "Cyber",
            "insurer": "Markel",
            "sum_insured": 2_000_000,
            "premium": 15_000,
            "deductible": 25_000,
            "start_date": "2026-09-01",
            "end_date": "2027-08-31",
        },
    )
    assert created.status_code == 201
    cid = created.json()["id"]

    patched = client.patch(f"/api/contracts/{cid}", json={"premium": 17_500})
    assert patched.json()["premium"] == 17_500

    assert client.get(f"/api/contracts/{cid}").json()["premium"] == 17_500
    assert client.delete(f"/api/contracts/{cid}").status_code == 204
    assert client.get(f"/api/contracts/{cid}").status_code == 404


def test_renew_endpoint_rolls_the_term(client):
    before = client.get("/api/contracts/FL-0003").json()
    after = client.post("/api/contracts/FL-0003/renew", json={"months": 12}).json()
    assert after["start_date"] == before["end_date"]
    assert after["end_date"] > before["end_date"]


def test_missing_resources_are_404(client):
    assert client.get("/api/contracts/FL-9999").status_code == 404
    assert client.get("/api/batches/BATCH-9999").status_code == 404
    assert client.get("/api/reports/RPT-9999").status_code == 404


def test_tools_endpoint_serves_generated_schemas(client):
    tools = client.get("/api/tools").json()["server_tools"]
    assert len(tools) == 3
    for tool in tools:
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object"


# --------------------------------------------------------------------------- #
# Operations that used to be reachable only through the assistant.
# --------------------------------------------------------------------------- #

def test_bulk_renewal_previews_by_default(client):
    before = client.get("/api/contracts/FL-0002").json()["end_date"]

    body = client.post("/api/renewals/batch", json={"expiring_within_days": 60}).json()

    assert body["committed"] is False
    assert body["matched"] == 2
    assert body["batch_id"].startswith("BATCH-")
    assert client.get("/api/contracts/FL-0002").json()["end_date"] == before


def test_bulk_renewal_commits_when_asked(client):
    before = client.get("/api/contracts/FL-0002").json()["end_date"]

    body = client.post(
        "/api/renewals/batch", json={"expiring_within_days": 60, "commit": True}
    ).json()

    assert body["committed"] is True
    assert len(body["renewed"]) == 2
    assert client.get("/api/contracts/FL-0002").json()["end_date"] > before


def test_bulk_renewal_refuses_an_unfiltered_run(client):
    assert client.post("/api/renewals/batch", json={}).status_code == 422


def test_bulk_renewal_reports_excluded_drafts(client):
    body = client.post("/api/renewals/batch", json={"product": "Crime"}).json()
    assert body["matched"] == 0


def test_report_can_be_created_over_http(client):
    created = client.post(
        "/api/reports/renewal",
        json={"expiring_within_days": 60, "group_by": "insurer"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["headline"]["contracts"] == 2

    # And read back through the existing artifact route.
    assert client.get(f"/api/reports/{body['report_id']}").status_code == 200


def test_report_rejects_bad_grouping_over_http(client):
    assert client.post("/api/reports/renewal", json={"group_by": "notes"}).status_code == 422


def test_benchmark_over_http(client):
    body = client.get("/api/market/benchmark", params={"contract_id": "FL-0001"}).json()
    assert body["product"] == "D&O"
    assert body["comparison"]["contract_id"] == "FL-0001"

    assert client.get("/api/market/benchmark", params={"product": "Cyber"}).json()["product"] == "Cyber"
    assert client.get("/api/market/benchmark", params={"product": "Aviation"}).status_code == 422
    assert client.get("/api/market/benchmark", params={"contract_id": "FL-9999"}).status_code == 404


def test_the_assistant_and_http_reach_the_same_operation(client):
    """
    The point of the services layer: a tool and a route are two adapters over one
    function, so they cannot disagree.
    """
    from app import tools

    http = client.post("/api/renewals/batch", json={"expiring_within_days": 60}).json()
    schema = tools.get("run_renewal_batch").definition()["input_schema"]["properties"]

    assert http["matched"] == 2
    # Both surfaces accept the same filter vocabulary.
    assert {"expiring_within_days", "product", "commit", "months"} <= set(schema)
