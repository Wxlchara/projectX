"""Real Uvicorn + HTTP + persisted profiles; no test router or fake gateway."""

import socket
import tempfile

import httpx

from auth_http_smoke import server

SAMPLE_PROFILE = {
    "given_name": "SAMPLE", "surname": "TOURIST", "birth_date": "1995-01-01",
    "citizenship": "RU", "residence_country": "RU", "has_other_citizenship": False,
    "has_foreign_residence_permit": False, "passport_kind": "biometric",
    "passport_issued_on": "2023-01-01", "passport_expires_on": "2033-01-01",
    "employment_status": "employed", "existing_permissions": [],
}
SAMPLE_COMPARISON = {
    "trip": {"purpose": "tourism", "starts_on": "2027-01-20", "ends_on": "2027-01-31",
             "application_country": "RU", "funding_type": "self", "accommodation_type": "hotel"},
    "answers": {"schengen_prior_stays": []},
}


def sign_in(client, username):
    body = {"username": username, "password": "SAMPLE-Password-123"}
    assert client.post("/api/v1/auth/register", json=body).status_code == 201
    session = client.post("/api/v1/auth/login", json=body)
    assert session.status_code == 200
    return {"X-CSRF-Token": session.json()["csrf_token"]}


def main():
    with socket.socket() as address:
        address.bind(("127.0.0.1", 0))
        port = address.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="visapilot-profile-") as data_dir:
        with httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=10) as alice, \
                httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=10) as bob:
            with server(port, data_dir):
                destinations = alice.get("/api/v1/destinations").json()["items"]
                assert len(destinations) == 12
                for destination in destinations:
                    assert alice.get("/api/v1/scenarios/" + destination["scenario_id"]).status_code == 200
                assert alice.get("/api/v1/profile").status_code == 401
                a_headers, b_headers = sign_in(alice, "sample_alice"), sign_in(bob, "sample_bob")
                assert alice.put("/api/v1/profile", json=SAMPLE_PROFILE).status_code == 403
                response = alice.put("/api/v1/profile", json=SAMPLE_PROFILE, headers=a_headers)
                assert response.status_code == 200 and response.json()["revision"] == 2
                saved = response.json()
                assert bob.put("/api/v1/profile", json=SAMPLE_PROFILE | {"citizenship": "BY"}, headers=b_headers).status_code == 200
                result = alice.post("/api/v1/comparisons", json=SAMPLE_COMPARISON, headers=a_headers)
                assert result.status_code == 200 and len(result.json()["items"]) == 12
                assert all(item["profile_revision"] == 2 and item["required_documents"] is None for item in result.json()["items"])
                foreign = bob.post("/api/v1/comparisons", json=SAMPLE_COMPARISON, headers=b_headers)
                assert all(item["applicability"]["status"] == "unsupported_profile" for item in foreign.json()["items"])
                assert alice.get("/api/v1/profile").json() == saved
            with server(port, data_dir):
                assert alice.get("/api/v1/profile").json() == saved
                assert bob.get("/api/v1/profile").json()["citizenship"] == "BY"
                assert alice.post("/api/v1/comparisons", json=SAMPLE_COMPARISON, headers=a_headers).status_code == 200
    print("HTTP rules/profile smoke passed: 12 scenarios, sessions/CSRF, persisted revisions, comparison and two-account isolation across process restart")


if __name__ == "__main__":
    main()
