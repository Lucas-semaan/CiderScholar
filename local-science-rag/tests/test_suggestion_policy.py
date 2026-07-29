from __future__ import annotations

import pytest

from app.corpora import CorpusScope
from app.services.suggestion_policy import (
    ExplicitPrivateSuggestionRequiredError,
    SuggestionIntent,
    authorize_document_suggestion,
)


def test_private_document_cannot_be_suggested_implicitly() -> None:
    with pytest.raises(ExplicitPrivateSuggestionRequiredError, match="action explicite"):
        authorize_document_suggestion(CorpusScope.PRIVATE, SuggestionIntent.AUTOMATIC)


def test_explicit_user_action_can_suggest_a_private_document() -> None:
    authorize_document_suggestion(
        CorpusScope.PRIVATE,
        SuggestionIntent.EXPLICIT_USER_ACTION,
    )


def test_common_document_does_not_require_private_disclosure_confirmation() -> None:
    authorize_document_suggestion(CorpusScope.COMMON, SuggestionIntent.AUTOMATIC)
