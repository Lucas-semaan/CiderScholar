"""Conservative, reproducible editorial decisions for the cider review queue."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

Decision = Literal["accepted", "rejected"]


@dataclass(frozen=True, slots=True)
class EditorialDecision:
    decision: Decision
    reason: str


def _fold(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return normalized.encode("ascii", "ignore").decode("ascii")


_CIDER = re.compile(r"\b(?:hard\s+)?ciders?\b|\bcidres?\b|\bcidricol\w*\b|\bsidras?\b")
_CIDER_SCIENCE = re.compile(
    r"ferment|microb|yeast|levur|bacter|oenococc|polyphen|phenol|tannin|tanin|"
    r"procyan|oxid|astring|protein|peptide|nitrogen|azote|amino|haze|trouble|"
    r"juice|jus|must|mout|press|clarif|filtrat|pectin|pasteur|composition|"
    r"turbid|distill|matur|oak|barrel|volatile|arom|sensor|flavou?r|taste|gout|"
    r"quality|qualit|sugar|sucre|acid|ethanol|glycerol|methanol|processing|"
    r"process|procede|storage|conserv|ripen|making|technolog|physico|biochem|"
    r"vitamin|sulphite|sulfite|color|couleur|variet|cultivar|genotype|harvest|"
    r"recolte|orchard|verger|pommer|pomme|apple|fruit|biocontrol|ravageur|"
    r"bioagress|ecosystem|ecolog|production|characteristic|model|traitement|"
    r"condition|food"
)
_DERIVED_PRODUCT = re.compile(
    r"\bpommeau\s+de\s+normandie\b|"
    r"\b(?:apple|cider)\s+(?:brand(?:y|ies)|spirits?|distillates?|wines?)\b|"
    r"\bapplejack\b|\beau[x]?\s+de\s+vie\s+de\s+cidre\b|\bapple\s+vermouth\b"
)
_DIRECT_MATERIAL = re.compile(
    r"\b(?:cider|cidre)\s+apples?\b|\bpommes?\s+a\s+cidre\b|"
    r"\bapple\s+(?:juices?|musts?|pomaces?|dregs?|press\s+cakes?|"
    r"based\s+beverages?|"
    r"processing\s+by-?products?|polyphenols?|phenolics?|tannins?|"
    r"procyanidins?|proteins?|peptides?|amino\s+acids?|aromas?|flavou?rs?|"
    r"volatiles?|sugars?|organic\s+acids?|pectins?)\b|"
    r"\b(?:juices?|musts?|pomaces?)\s+(?:from|of)\s+apples?\b"
)
_TRANSFERABLE_PROCESS = re.compile(
    r"ferment|brewing|microb|yeast|levur|bacter|oenococc|brettanomyces|"
    r"saccharomyces|spoilage|antimicrobial|pathogen|salmonella|"
    r"pasteur|pulsed electric|high.pressure|clarif|filtrat|stabili|"
    r"malolactic|biogenic amine|sulphite|sulfite|haze|trouble"
)
_CIDER_BRIDGE = re.compile(
    r"\b(?:hard\s+)?ciders?\b|\bcidres?\b|\bcidricol\w*\b|\bsidras?\b|"
    r"\bapple\s+(?:juices?|musts?|pomaces?|based\s+beverages?)\b"
)
_APPLE = re.compile(r"\bapples?\b|\bpommes?\b|\bmalus(?:\s+domestica)?\b")
_APPLE_CIDER_SCIENCE = re.compile(
    r"ferment|microb|yeast|levur|bacter|polyphen|phenol|tannin|tanin|"
    r"procyan|oxid|protein|peptide|nitrogen|azote|amino|haze|trouble|"
    r"juice|jus|must|mout|pomace|press|clarif|filtrat|pectin|pasteur|"
    r"composition|volatile|arom|flavou?r|sugar|sucre|organic acid|"
    r"ethanol|methanol|browning|sensory|sensor|distill|beverage|wine"
)
_FALSE_APPLE = re.compile(
    r"\b(?:ackee|balsam|cashew|custard|elephant|kei|monkey|pine|pond|rose|"
    r"star|sugar|velvet|water|wax|wood)[ -]+apples?\b"
)
_OUT_OF_SCOPE = re.compile(
    r"program|software|traffic|intersection|astronom|learning analytics|spam|"
    r"cirrhos|poison|alcoolisme|alcoholism|blood glucose|clinical|patient|"
    r"cancer|carcinoma|apoptosis|bioavailability|ileostom|colonic|gut health|"
    r"inflammatory|healthy subjects|"
    r"anti-inflammatory|genoprotect|allerg|statin|itch|covid|marketing|"
    r"economic|socio|consumer|consommateur|willingness|campaign|manual labour|"
    r"\bhistor|histoire|premiers temps|invent(?:ion|ed)|recette|exposition|"
    r"gabelle|revolte|cartulaire|fete|eloge|pieds de pommes sales|hahnenschrei|"
    r"heteronomie|archaeolog|archeolog|neolith|bronze|gallo|meroving|jurassic|"
    r"geolog|paleont|cemet|cimeti|eglise|church|castle|chateau|politic|"
    r"election|landscape|paysage|tourism|war|guerre|monastery|abbey|abbaye|"
    r"brucell|toxoplas|douche|shower|epee|sword|voltaire|kabupaten|kecamatan|"
    r"kidney|saliva|dietary|diet\b|in vivo|growth performance|human plasma|"
    r"sidra rabba|sidra noach|^the sidra\.?$|framework|zolpidem|"
    r"process for producing|patent|budget|enterprise budget|signes de qualite|"
    r"^cidre[-:]?\s*(?:a distributed|programming)|"
    r"^cidre\s*-\s*a small astronomical|"
    r"sheep|wether|broiler|catfish|poultry|meatball|bread|bakery|yoghurt|"
    r"yogurt|snack|animal feed|biofuel|fuel ethanol|hydrothermal carbonization|"
    r"\bfeed|wastewater|cosmetic|mycelial protein|tomato|avocado|passion fruit|"
    r"mulberry|pomegranate|kombucha|chokeberry|blueberry|pineapple|star apple|"
    r"cashew|cacao|agroforest|genom|transcript|methylom|sugar transporter gene|"
    r"transformation du cidre au quebec"
)
_KNOWN_METADATA_MISMATCHES = {
    "10.1037/bne0000030",
    "10.1093/jambio/lxae019",
    "10.1104/pp.64.4.538",
    "10.1371/journal.pone.0126962",
}


def classify_editorial_record(record: Mapping[str, object]) -> EditorialDecision:
    """Admit direct cider science and tightly bounded supporting matrices only."""

    title = _fold(record.get("title"))
    abstract = _fold(record.get("abstract"))
    if not title:
        return EditorialDecision("rejected", "Titre absent ou illisible.")
    doi = _fold(record.get("doi")).strip()
    if doi in _KNOWN_METADATA_MISMATCHES:
        return EditorialDecision(
            "rejected", "DOI, titre et abstract incohérents après contrôle Crossref."
        )
    if _FALSE_APPLE.search(title) or _OUT_OF_SCOPE.search(title):
        return EditorialDecision("rejected", "Hors périmètre ou homonyme non cidricole.")
    if "calvados" in title and not re.search(
        r"cider|cidre|apple|pomme|brandy|spirit|distill|beverage|boisson|alcool|"
        r"eau de vie|arom|volatile|composition|authentic|production",
        title,
    ):
        return EditorialDecision("rejected", "Calvados désigne ici le territoire, pas la boisson.")
    if "pommeau" in title and not re.search(
        r"normandie|apple|pomme|cidre|brandy|mistelle|beverage|boisson|alcool|"
        r"composition|physico|sensor",
        title,
    ):
        return EditorialDecision("rejected", "Pommeau est ici un homonyme du produit cidricole.")
    if re.search(r"\bsidra\b", title) and not re.search(
        r"ferment|apple|manzana|cider|cidre|natural|elabora|probiotic|alcohol|"
        r"wine|arom|microb|yeast|bacter|production|quality|making|bebida",
        title,
    ):
        return EditorialDecision("rejected", "Sidra est ici un lieu, un acronyme ou un homonyme.")
    if _CIDER.search(title):
        if _CIDER_SCIENCE.search(title):
            return EditorialDecision(
                "accepted", "Le titre traite explicitement de science du cidre."
            )
        return EditorialDecision(
            "rejected", "Le cidre est cité sans objet scientifique ou technique identifiable."
        )
    if _DERIVED_PRODUCT.search(title):
        return EditorialDecision("accepted", "Produit dérivé cidricole explicitement étudié.")
    if _DIRECT_MATERIAL.search(title):
        return EditorialDecision(
            "accepted", "Matière première ou procédé directement utile à la filière cidricole."
        )
    if _APPLE.search(title) and _APPLE_CIDER_SCIENCE.search(title):
        return EditorialDecision(
            "accepted", "Science de la pomme directement exploitable pour l'axe cidre."
        )
    if _TRANSFERABLE_PROCESS.search(title) and _CIDER_BRIDGE.search(abstract):
        return EditorialDecision(
            "accepted",
            "Matrice périphérique avec mécanisme ou procédé explicitement transférable au cidre.",
        )
    return EditorialDecision(
        "rejected", "Lien au cidre indirect, incident ou insuffisamment spécifique."
    )
