from scripts.audit_microbiology_full_text import classify_subtopics, classify_title


def test_classify_title_separates_fermentation_and_contaminants() -> None:
    fermentation = classify_title(
        "Microbial succession of Saccharomyces and Oenococcus during cider fermentation"
    )
    contaminants = classify_title("Penicillium expansum and patulin contamination in apple juice")

    assert fermentation == {"microorganismes_des_fermentations"}
    assert contaminants == {"contaminants_et_alterations"}


def test_classify_title_excludes_non_apple_cider_homonyms() -> None:
    assert not classify_title("Microbial fermentation of sap from the cider gum")
    assert not classify_title("Spent cider yeast changes the swine gut microbiome")


def test_classify_subtopics_is_multilabel() -> None:
    subtopics = classify_subtopics(
        "Biocontrol of Penicillium expansum in apple juice",
        "Ultraviolet inactivation reduced patulin and fungal spoilage.",
    )

    assert "moisissures_et_patuline" in subtopics
    assert "maitrise_et_inactivation" in subtopics
