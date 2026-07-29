"""Selective local Markdown/PDF exports without response metadata or secrets."""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import fitz

from app.database.sqlite import Database


def _selected_conversations(
    database: Database,
    conversation_ids: list[str],
    message_ids: set[str],
) -> list[dict]:
    conversations = []
    for conversation_id in conversation_ids:
        conversation = database.chat_conversation(conversation_id)
        if conversation is None:
            raise ValueError(f"unknown conversation {conversation_id}")
        messages = [
            message
            for message in conversation["messages"]
            if not message_ids or message["id"] in message_ids
        ]
        conversations.append({**conversation, "messages": messages})
    found_ids = {
        message["id"] for conversation in conversations for message in conversation["messages"]
    }
    if message_ids.difference(found_ids):
        raise ValueError("selected message does not belong to a selected conversation")
    return conversations


def _markdown(conversations: list[dict], created_at: datetime) -> str:
    lines = [
        "# Export CiderScholar",
        "",
        f"Créé le {created_at.astimezone(UTC).isoformat()}",
        "",
    ]
    for conversation in conversations:
        lines.extend((f"## {conversation['title']}", ""))
        for message in conversation["messages"]:
            label = "Utilisateur" if message["role"] == "user" else "CiderScholar"
            lines.extend((f"### {label}", "", str(message["content"]), ""))
    return "\n".join(lines).rstrip() + "\n"


def _pdf(markdown: str, destination: Path) -> None:
    document = fitz.open()
    page = document.new_page()
    y = 54.0
    for raw_line in markdown.splitlines():
        line = raw_line.lstrip("#").strip()
        font_size = 16 if raw_line.startswith("# ") else 13 if raw_line.startswith("## ") else 10
        if not line:
            y += 8
            continue
        for wrapped in textwrap.wrap(line, width=95) or [""]:
            if y > page.rect.height - 54:
                page = document.new_page()
                y = 54.0
            page.insert_text(
                (54, y),
                wrapped,
                fontsize=font_size,
                fontname="helv",
                color=(0.1, 0.15, 0.12),
            )
            y += font_size * 1.45
        y += 3
    temporary = destination.with_name(f"{destination.stem}.tmp.pdf")
    document.save(temporary)
    document.close()
    temporary.replace(destination)


def export_conversations(
    database: Database,
    *,
    conversation_ids: list[str],
    message_ids: list[str] | None,
    format: Literal["markdown", "pdf"],
    destination_root: str | Path,
    created_at: datetime | None = None,
) -> Path:
    if not conversation_ids or len(conversation_ids) != len(set(conversation_ids)):
        raise ValueError("select at least one unique conversation")
    selected_message_ids = set(message_ids or [])
    conversations = _selected_conversations(database, conversation_ids, selected_message_ids)
    timestamp = created_at or datetime.now(UTC)
    content = _markdown(conversations, timestamp)
    extension = ".md" if format == "markdown" else ".pdf"
    destination = Path(destination_root) / f"conversations-{uuid4()}{extension}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if format == "markdown":
        temporary = destination.with_suffix(".md.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(destination)
    else:
        _pdf(content, destination)
    return destination
