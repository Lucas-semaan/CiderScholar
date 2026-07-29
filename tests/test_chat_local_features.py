from __future__ import annotations

from contextlib import closing

import fitz
from fastapi.testclient import TestClient

from app.database.sqlite import Database
from app.main import create_app


def _conversation_with_messages(database: Database) -> tuple[str, str, str]:
    database.initialize()
    conversation = database.create_chat_conversation("Fermentation locale")
    database.append_chat_message(
        conversation_id=conversation["id"],
        role="user",
        content="Question sur un mot-clé introuvable dans le titre : levure.",
    )
    database.append_chat_message(
        conversation_id=conversation["id"],
        role="assistant",
        content="Réponse scientifique locale.",
    )
    stored = database.chat_conversation(conversation["id"])
    assert stored is not None
    return (
        conversation["id"],
        stored["messages"][0]["id"],
        stored["messages"][1]["id"],
    )


def test_search_favorite_and_content_free_feedback_are_local(settings) -> None:
    database = Database(settings.paths.database_path)
    conversation_id, user_message_id, assistant_message_id = _conversation_with_messages(database)
    with TestClient(create_app(settings)) as client:
        search = client.get("/api/chatbot/conversations/search", params={"query": "levure"})
        favorite = client.put(
            f"/api/chatbot/conversations/{conversation_id}/favorite",
            json={"favorite": True},
        )
        feedback = client.put(
            f"/api/chatbot/messages/{assistant_message_id}/feedback",
            json={"helpful": False},
        )
        invalid_feedback = client.put(
            f"/api/chatbot/messages/{user_message_id}/feedback",
            json={"helpful": True},
        )
        stored = client.get(f"/api/chatbot/conversations/{conversation_id}")

    assert search.status_code == 200
    assert [item["id"] for item in search.json()["conversations"]] == [conversation_id]
    assert favorite.json() == {"favorite": True}
    assert feedback.json() == {"helpful": False}
    assert invalid_feedback.status_code == 409
    assert stored.json()["favorite"] is True
    assert stored.json()["messages"][1]["helpful"] is False
    with closing(database.connect()) as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(chat_message_feedback)")
        }
    assert columns == {"message_id", "helpful", "created_at", "updated_at"}


def test_conversation_search_does_not_materialize_unmatched_summaries(
    settings, monkeypatch
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    matching = database.create_chat_conversation("Fermentation de cidre")
    database.create_chat_conversation("Analyse des polyphÃ©nols")

    def unexpected_full_listing(self: Database) -> list[dict[str, object]]:
        raise AssertionError("search must not load every conversation summary")

    monkeypatch.setattr(Database, "list_chat_conversations", unexpected_full_listing)

    results = database.search_chat_conversations("cidre")

    assert [item["id"] for item in results] == [matching["id"]]


def test_selective_markdown_and_pdf_exports_contain_only_selected_messages(settings) -> None:
    database = Database(settings.paths.database_path)
    conversation_id, user_message_id, _ = _conversation_with_messages(database)
    with TestClient(create_app(settings)) as client:
        payload = {
            "conversation_ids": [conversation_id],
            "message_ids": [user_message_id],
        }
        markdown = client.post(
            "/api/chatbot/exports",
            json={**payload, "format": "markdown"},
        )
        pdf = client.post(
            "/api/chatbot/exports",
            json={**payload, "format": "pdf"},
        )

    assert markdown.status_code == 200
    assert "levure" in markdown.text
    assert "Réponse scientifique locale" not in markdown.text
    assert pdf.status_code == 200
    document = fitz.open(stream=pdf.content, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)
    document.close()
    assert "levure" in text
    assert "Réponse scientifique locale" not in text
    exported = list((settings.paths.exports_dir / "conversations").iterdir())
    assert len(exported) == 2
    assert all(path.name.startswith("conversations-") for path in exported)
