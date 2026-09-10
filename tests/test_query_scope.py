from app.retrieval.query_scope import classify_query_scope


def test_query_scope_rejects_a_foreign_general_knowledge_question() -> None:
    decision = classify_query_scope("Quelle est la capitale de la France ?")

    assert decision.accepted is False
    assert decision.reason_code == "clearly_foreign_topic"


def test_query_scope_rejects_human_nutrition_even_when_cider_is_mentioned() -> None:
    decision = classify_query_scope("Le cidre améliore-t-il la glycémie des patients ?")

    assert decision.accepted is False


def test_query_scope_accepts_scientific_cider_processes_without_requiring_the_word_cider() -> None:
    decision = classify_query_scope("Quels facteurs influencent la fermentation ?")

    assert decision.accepted is True


def test_query_scope_allows_a_short_follow_up_to_an_in_scope_question() -> None:
    decision = classify_query_scope(
        "Pourquoi ?",
        history=[
            {
                "role": "user",
                "content": "Comment la fermentation malolactique modifie-t-elle le cidre ?",
            }
        ],
    )

    assert decision.accepted is True


def test_query_scope_does_not_inherit_scope_for_a_new_foreign_topic() -> None:
    decision = classify_query_scope(
        "Explique-moi la mécanique quantique.",
        history=[{"role": "user", "content": "Quels polyphénols trouve-t-on dans le cidre ?"}],
    )

    assert decision.accepted is False


def test_query_scope_rejects_an_unrelated_scientific_topic_without_a_cider_signal() -> None:
    decision = classify_query_scope("Quelles preuves soutiennent le changement climatique ?")

    assert decision.accepted is False
