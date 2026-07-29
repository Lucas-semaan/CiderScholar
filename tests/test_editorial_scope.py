from app.updates.editorial_scope import classify_editorial_record


def _decision(title: str) -> str:
    return classify_editorial_record({"title": title}).decision


def test_editorial_scope_keeps_cider_as_the_primary_axis() -> None:
    assert _decision("Oenococcus sicerae sp. nov., isolated from French cider") == "accepted"
    assert _decision("Influence of Prefermentary Clarification on Apple Musts") == "accepted"
    assert (
        _decision("Composition and mechanisms of haze formation in apple-based beverages")
        == "accepted"
    )
    assert _decision("Saccharomyces bayanus Enhances Volatile Profile of Apple Brandies") == (
        "accepted"
    )


def test_editorial_scope_rejects_homonyms_and_incidental_topics() -> None:
    assert _decision("CIDRE: A Distributed Shared Arrays Library") == "rejected"
    assert _decision("Un établissement rural à Fleury-sur-Orne (Calvados)") == "rejected"
    assert _decision("Le pommeau de douche de Smart & Blue primé !") == "rejected"
    assert _decision("Pectin, a versatile polysaccharide present in plant cell walls") == (
        "rejected"
    )


def test_editorial_scope_rejects_health_and_non_apple_false_friends() -> None:
    assert _decision("Colonic availability of apple polyphenols") == "rejected"
    assert _decision("Nutritional profile of cashew apple juice") == "rejected"


def test_editorial_scope_rejects_confirmed_metadata_mismatch() -> None:
    decision = classify_editorial_record(
        {
            "title": "Microbiologie et technologie du cidre",
            "doi": "10.1371/journal.pone.0126962",
        }
    )
    assert decision.decision == "rejected"
    assert "Crossref" in decision.reason


def test_editorial_scope_keeps_transferable_peripheral_models() -> None:
    assert (
        classify_editorial_record(
            {
                "title": "Brettanomyces bruxellensis in spontaneous beer fermentations",
                "abstract": "This yeast can cause spoilage or desirable aromas in cider.",
            }
        ).decision
        == "accepted"
    )
    assert (
        classify_editorial_record(
            {
                "title": "Antimicrobial activity against food spoilage yeasts",
                "abstract": "The organisms were tested in an apple juice model system.",
            }
        ).decision
        == "accepted"
    )
    assert (
        classify_editorial_record(
            {
                "title": "Marketing practices in the brewing industry",
                "abstract": "The introduction mentions cider.",
            }
        ).decision
        == "rejected"
    )
