from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_missing_argo_key_directs_suggestion_user_to_settings(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/suggestions",
            json={"source": {"kind": "doi", "doi": "10.1000/missing-key"}},
        )

    assert response.status_code == 200
    assert response.json()["state"] == "retry"
    assert response.json()["action"] == "settings"
    assert "Paramètres" in response.json()["message"]


def test_dangerous_url_is_rejected_by_api_before_evaluation(settings, monkeypatch) -> None:
    called = False

    def evaluator(*_args):
        nonlocal called
        called = True

    monkeypatch.setattr("app.suggestions.service.evaluate_suggestion", evaluator)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/suggestions",
            json={
                "source": {
                    "kind": "url",
                    "url": "https://127.0.0.1/private",
                }
            },
        )

    assert response.status_code == 422
    assert called is False


def test_fake_pdf_and_missing_transmission_consent_are_rejected(settings) -> None:
    with TestClient(create_app(settings)) as client:
        no_consent = client.post(
            "/api/suggestions/pdf",
            data={"transmit_pdf_confirmed": "false"},
            files={"file": ("paper.pdf", b"%PDF-1.7\nbody", "application/pdf")},
        )
        fake = client.post(
            "/api/suggestions/pdf",
            data={"transmit_pdf_confirmed": "true"},
            files={"file": ("paper.pdf", b"not a pdf", "application/pdf")},
        )

    assert no_consent.status_code == 422
    assert fake.status_code == 422


def test_suggestions_have_no_listing_or_tracking_route(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/suggestions")

    assert response.status_code == 404
