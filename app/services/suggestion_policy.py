"""Privacy boundary for turning local documents into corpus suggestions."""

from __future__ import annotations

from enum import StrEnum

from app.corpora import CorpusScope


class SuggestionIntent(StrEnum):
    """How a document entered the suggestion flow."""

    AUTOMATIC = "automatic"
    EXPLICIT_USER_ACTION = "explicit_user_action"


class ExplicitPrivateSuggestionRequiredError(PermissionError):
    """A private document was selected without an explicit user action."""


def authorize_document_suggestion(scope: CorpusScope, intent: SuggestionIntent) -> None:
    """Forbid private-to-shared disclosure unless the user explicitly initiated it."""

    if scope is CorpusScope.PRIVATE and intent is not SuggestionIntent.EXPLICIT_USER_ACTION:
        raise ExplicitPrivateSuggestionRequiredError(
            "Un document privé ne peut être proposé qu'après une action explicite."
        )
