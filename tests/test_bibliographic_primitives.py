from __future__ import annotations

import pytest

from app.updates.models import author_names, verified_normalized_doi


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10.1234/APPLE", "10.1234/apple"),
        (" 10.1234/apple ", "10.1234/apple"),
        ("https://doi.org/10.1234/apple", None),
        ("doi:10.1234/apple", None),
        ("10.1234/apple;", None),
        ("10.1234/apple unrelated text", None),
        ("10.1234/", None),
        ("", None),
        (None, None),
        (1234, None),
    ],
)
def test_verified_identity_never_accepts_an_extracted_or_corrected_doi(value, expected) -> None:
    assert verified_normalized_doi(value) == expected


def test_author_cleanup_preserves_order_and_distinct_names() -> None:
    assert author_names(
        [
            {"name": "  Ada <b>Test</b> "},
            {"name": "Zoé &amp; Co"},
            {"name": "Ada Test"},
            {"name": "ada test"},
            {"name": ""},
            {"name": 42},
            {"given": "Unconfirmed surname"},
            None,
        ]
    ) == ["Ada Test", "Zoé & Co", "ada test"]
    assert author_names({"name": "not a provider list"}) == []
