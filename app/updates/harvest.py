"""Persistent, rate-limited harvesting for the bounded cider-design pilot."""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from contextlib import closing, nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.database.sqlite import Database
from app.updates.base import BibliographicApiDeferred
from app.updates.doi_exclusions import DoiExclusionRegistry
from app.updates.models import BibliographicRecord, clean_text, normalize_doi
from app.updates.openalex import OpenAlexClient
from app.updates.service import CLIENTS

MISSING_ABSTRACT_REASON = (
    "Abstract unavailable after DOI enrichment; excluded from the usable corpus."
)
MISSING_DOI_REASON = (
    "Verified DOI unavailable; retained for review but excluded from the usable corpus."
)

CIDER_PILOT_THEMES: dict[str, str] = {
    "biochimie": (
        '"cider fermentation" biochemical metabolism organic acids sugar ethanol glycerol'
    ),
    "microbiologie": ('"cider fermentation" yeast bacteria microbiome malolactic microorganisms'),
    "polyphenols": ("cider apple polyphenols phenolic tannins procyanidins oxidation astringency"),
    "proteines": ("cider apple juice proteins peptides nitrogen amino acids haze proteomics"),
    "jus_pomme": ('"apple juice" cider processing pressing clarification pectinase composition'),
    "calvados_eau_vie": (
        'calvados "apple brandy" cider eau-de-vie distillation maturation oak volatiles'
    ),
    "pommeau": ('"Pommeau de Normandie" apple mistelle mutage composition chemistry'),
    "aromes_procede": (
        "cider production processing fermentation sensory aroma volatile compounds quality"
    ),
}
CIDER_QUERY_WAVES: tuple[dict[str, str], ...] = (
    CIDER_PILOT_THEMES,
    {
        "biochimie": (
            'cider fermentation "organic acids" ethanol glycerol sugar metabolism kinetics'
        ),
        "microbiologie": (
            "cider fermentation yeast ecology lactic acid bacteria Oenococcus microbiota"
        ),
        "polyphenols": (
            "cider apple tannins procyanidins phenolic oxidation colour astringency analysis"
        ),
        "proteines": ('cider "yeast assimilable nitrogen" amino acids fermentation nutrition'),
        "jus_pomme": ("apple juice cider pressing yield clarification pectin turbidity filtration"),
        "calvados_eau_vie": ('"cider brandy" distillation methanol volatile compounds oak ageing'),
        "pommeau": ('"Pommeau de Normandie" turbidity metals polyphenols composition'),
        "aromes_procede": (
            "cider sensory aroma esters higher alcohols fermentation process quality"
        ),
    },
    {
        "biochimie": (
            "hard cider physicochemical composition pH acidity residual sugar fermentation"
        ),
        "microbiologie": (
            "hard cider spontaneous fermentation microbial succession non-Saccharomyces yeast"
        ),
        "polyphenols": (
            "hard cider phenolic profile polymeric tannins polyphenol retention processing"
        ),
        "proteines": (
            "hard cider nitrogen amino acid yeast nutrition hydrogen sulfide fermentation"
        ),
        "jus_pomme": (
            "cider apple cultivar juice composition maturity pressing fermentation quality"
        ),
        "calvados_eau_vie": (
            "calvados apple brandy maturation barrel wood aroma chemical composition"
        ),
        "pommeau": ("pommeau apple mistelle mutage alcohol sugar phenolic sensory chemistry"),
        "aromes_procede": (
            "hard cider volatile profile fermentation temperature inoculation sensory analysis"
        ),
    },
    {
        "biochimie": ("cidre biochimie fermentation acides organiques sucres glycérol éthanol"),
        "microbiologie": ("cidre levures bactéries fermentation malolactique microbiologie"),
        "polyphenols": (
            "cidre pomme polyphénols tanins procyanidines oxydation couleur astringence"
        ),
        "proteines": (
            "cidre azote assimilable protéines acides aminés nutrition levure fermentation"
        ),
        "jus_pomme": ("jus de pomme cidre pressurage clarification pectine composition rendement"),
        "calvados_eau_vie": (
            "calvados eau-de-vie cidre distillation vieillissement bois composés volatils"
        ),
        "pommeau": ("pommeau de Normandie mutage mistelle composition trouble physico-chimique"),
        "aromes_procede": (
            "cidre arômes composés volatils procédé fermentation qualité sensorielle"
        ),
    },
)
CIDER_BULK_QUERY_WAVES: tuple[dict[str, str], ...] = (
    {
        "biochimie": "cider",
        "microbiologie": '"cider fermentation"',
        "polyphenols": '"cider polyphenols"',
        "proteines": '"cider nitrogen"',
        "jus_pomme": '"apple juice" cider',
        "calvados_eau_vie": "calvados",
        "pommeau": "pommeau",
        "aromes_procede": '"hard cider"',
    },
    {
        "biochimie": "cidre",
        "microbiologie": '"hard cider fermentation"',
        "polyphenols": '"apple cider" phenolic',
        "proteines": '"cider amino acids"',
        "jus_pomme": '"cider apple juice"',
        "calvados_eau_vie": '"apple brandy"',
        "pommeau": '"Pommeau de Normandie"',
        "aromes_procede": '"cider aroma"',
    },
    {
        "biochimie": "sidra",
        "microbiologie": '"cider yeast"',
        "polyphenols": '"cider tannins"',
        "proteines": '"yeast assimilable nitrogen" cider',
        "jus_pomme": '"cider apple" pressing',
        "calvados_eau_vie": '"cider brandy"',
        "pommeau": "mistelle apple",
        "aromes_procede": '"cider volatile compounds"',
    },
    {
        "biochimie": '"fermented apple"',
        "microbiologie": '"cider bacteria"',
        "polyphenols": '"apple juice" polyphenols',
        "proteines": '"cider protein"',
        "jus_pomme": '"apple must" cider',
        "calvados_eau_vie": '"eau-de-vie" apple',
        "pommeau": "apple mistelle",
        "aromes_procede": '"cider sensory"',
    },
)
OPENALEX_SEARCH_COST_USD = 0.001
TITLE_KEY = re.compile(r"[^a-z0-9]+")
DOMAIN_PATTERN = re.compile(r"\b(ciders?|cidres?|cidricoles?|sidras?|calvados|pommeau)\b")
APPLE_BEVERAGE_PATTERN = re.compile(
    r"\b(?:apple (?:juice|must|wine|brandy|beverage)|fermented apple|apple based beverage|"
    r"(?:suco|vinho|mosto)(?: [a-z]+){0,3} de macas?)\b"
)
APPLE_MATERIAL_PATTERN = re.compile(
    r"\b(?:apple (?:fruit|fruits|cultivars?|varieties|pomace|pulp|peels?|skins?|seeds?|"
    r"press cake|processing by-?products?|raw materials?)|pomace from apples?)\b"
)
TRUE_APPLE_PATTERN = re.compile(
    r"\b(?:apples?|pommes?|manzanas?|manzanos?|malus (?:x )?domestica|malus pumila)\b"
)
FALSE_APPLE_PATTERN = re.compile(
    r"\b(?:cashew|sugar|custard|star|wood|elephant|rose|wax|water|monkey|pond|"
    r"kei|velvet|mangrove)[ -]+apples?\b|\bapple[ -]rings?\b"
)
HEALTH_ONLY_PATTERN = re.compile(
    r"\b(cancer|carcin|rats?|mice|mouse|epithelial|neurodegener|gut health|"
    r"antidiabetic|blood lipids?|cholesterol|obesity|cytotoxicity|home remedy|"
    r"antiacne|acne creams?|postoperative|patients?|general practitioners?|"
    r"pregnan[a-z]*|hospitals?|admissions?|liver disease|alcohol related harms?|"
    r"heavy drinkers?|platelets?|cosmeceutical|covid(?: 19)?|pandemic)\b"
)
VINEGAR_PATTERN = re.compile(r"\b(?:vinegars?|vinaigres?)\b")
NON_APPLE_FRUIT_PATTERN = re.compile(
    r"\b(?:apricot|banana|blackberry|blueberry|buni|canistel|cashew|cherry|coconut|"
    r"cranberry|"
    r"dragon fruit|elderberry|gooseberry|guava|kiwi|lychee|mango|orange|peach|"
    r"pear|pineapple|plum|pomegranate|raspberry|strawberry|eggfruit|lekima)\b"
)
NON_APPLE_ADDITION_PATTERN = re.compile(
    r"\b(?:add(?:ed|ition)|blend(?:ed|ing|s)?|extracts?|fortif[a-z]*|supplement[a-z]*)\b"
)
NON_APPLE_SOURCE_PATTERN = re.compile(
    r"\b(?:derived|made|prepared|produced) from\b|\b(?:from|of)\b"
)
OFF_TOPIC_TITLE_PATTERN = re.compile(
    r"\b(?:image description|ontology matching|c\+\+|software package|"
    r"automation tool|traffic flow|(?:un)?signalized intersections?|roundabouts?|"
    r"sequence ensemble relationships?|interaction networks? in human diseases|"
    r"willingness to pay|marketing activities|cider sales|socioeconomics?|whigs?|"
    r"consumption profiles?|consumer experience|quality claims?|"
    r"shaker spirits?|spirituality|topical formulations?|cosmetic[a-z]*|"
    r"linguistic identity|multilingual signage|producer perspectives?|"
    r"implicit reaction|explicit emotional response|"
    r"good cider out of bad apples|"
    r"purchasing quantity|research and extension needs?|back cover|cider gum|"
    r"alcohol availability intervention|cluster based on|cluster base sur|"
    r"hard cider campaign|last hurrah|manual labour|cosmogenic nuclides?|"
    r"climate intervention|dynamical emulator|key value stores?|gui agents?|"
    r"continuous integration|landscape evolution|detrital cosmogenic|"
    r"resilience and sensemaking)\b"
)
NON_BEVERAGE_ABSTRACT_PATTERN = re.compile(
    r"\b(?:database systems?|query processing|computer vision|software|algorithm|"
    r"image captions?|traffic intersections?|human diseases|climate intervention|"
    r"scenario space|key value stores?|gui agents?)\b"
)
BEVERAGE_CONTEXT_PATTERN = re.compile(
    r"\b(?:apples?|pommes?|juice|must|pomace|beverages?|ferment[a-z]*|yeasts?|"
    r"bacter[a-z]*|microb[a-z]*|polyphenol[a-z]*|phenolic[a-z]*|tannin[a-z]*|"
    r"nitrogen|sensory|aroma[a-z]*|volatile[a-z]*|alcohol|ethanol|pressing|"
    r"clarif[a-z]*|distill[a-z]*|cultivars?|orchards?|cidres?|sidras?|calvados|"
    r"pommeau)\b"
)
STRONG_TECHNICAL_CONTEXT_PATTERN = re.compile(
    r"\b(?:apples?|pommes?|juice|must|pomace|beverages?|drinks?|ferment[a-z]*|yeasts?|"
    r"bacter[a-z]*|microb[a-z]*|polyphenol[a-z]*|phenolic[a-z]*|tannin[a-z]*|"
    r"nitrogen|sensory|aroma[a-z]*|volatile[a-z]*|alcohol|ethanol|pressing|"
    r"clarif[a-z]*|distill[a-z]*|brewing|winemaking|brandy|spirits?|cultivars?|"
    r"orchards?|pectin[a-z]*|pasteur[a-z]*|matur[a-z]*|ageing|aging|quality|"
    r"processing|production|microfiltrat[a-z]*|filtrat[a-z]*|analyt[a-z]*|chimi[a-z]*|"
    r"acides?|sucres?|"
    r"levures?|bacteries?|aromes?|qualite|vieillissement|transformation|"
    r"determin[a-z]*|quantif[a-z]*|measur[a-z]*|characteri[sz][a-z]*|"
    r"chromatograph[a-z]*|hplc|spectr[a-z]*|isotop[a-z]*|esters?|fatty acids?|"
    r"acids?|acidos?|monosaccharides?|proanthocyanidin[a-z]*|sulphit[a-z]*|"
    r"sulfit[a-z]*|foam[a-z]*|acoustic[a-z]*|equilibr[a-z]*|bottl[a-z]*|"
    r"proces[a-z]*|elabor[a-z]*|producci[a-z]*|biotecnolog[a-z]*|residuos?|"
    r"valoriz[a-z]*|plagas?|dimension[a-z]*|control)\b"
)
SOCIAL_OR_GEOGRAPHIC_OFF_TOPIC_PATTERN = re.compile(
    r"\b(?:archaeolog[a-z]*|neolithic|neolithique|jurassic|guerre|war|"
    r"deuil|medecins?|medical|prophylaxis|prophylaxie|thrombo[a-z]*|"
    r"free riders?|signaling expectations?|culinary identities|puzzles?|"
    r"politic[a-z]*|electoral|linguistic identity|sector analysis|analyse du secteur|"
    r"product diversification|diversificacion productiva)\b"
)
THEME_PATTERNS: dict[str, re.Pattern[str]] = {
    "biochimie": re.compile(
        r"\b(?:biochem[a-z]*|metabol[a-z]*|organic acids?|sugars?|ethanol|glycerol|"
        r"chemistr[a-z]*|physicochem[a-z]*|fermentation kinetics)\b"
    ),
    "microbiologie": re.compile(
        r"\b(?:yeasts?|bacter[a-z]*|microb[a-z]*|malolactic|microorgan[a-z]*|"
        r"fung[a-z]*|inocul[a-z]*|saccharomyces|hanseniaspora|metschnikowia|"
        r"torulaspora|lachancea|starmerella|pichia|oenococcus|lactobac[a-z]*|"
        r"lactiplantibacillus|leuconostoc|pediococcus|acetobacter|gluconobacter|"
        r"brettanomyces|zygosaccharomyces|alicyclobacillus|penicillium|"
        r"paecilomyces|patulin|mycotoxin[a-z]*|spoilage|contamin[a-z]*|"
        r"pathogen[a-z]*|escherichia|salmonella|listeria|phages?|phageome)\b"
    ),
    "polyphenols": re.compile(
        r"\b(?:polyphenol[a-z]*|phenolic[a-z]*|tannin[a-z]*|procyanidin[a-z]*|"
        r"antioxidant[a-z]*|astringen[a-z]*|oxid[a-z]*|colou?r[a-z]*)\b"
    ),
    "proteines": re.compile(
        r"\b(?:protein[a-z]*|peptide[a-z]*|proteom[a-z]*|nitrogen|amino acids?|"
        r"haze|fining)\b"
    ),
    "jus_pomme": re.compile(
        r"\b(?:juices?|musts?|pressing|process[a-z]*|clarif[a-z]*|filtrat[a-z]*|"
        r"pectin[a-z]*|pasteur[a-z]*|composition|turbidity)\b"
    ),
    "calvados_eau_vie": re.compile(
        r"\b(?:distill[a-z]*|matur[a-z]*|oak|barrels?|volatile[a-z]*|aroma[a-z]*|"
        r"methanol|spirits?|brandy|ageing|aging)\b"
    ),
    "pommeau": re.compile(
        r"\b(?:mistelle|fortif[a-z]*|mutage|brandy|ethanol|spirits?|composition|"
        r"chemistr[a-z]*|sugars?|alcohol|phenolic[a-z]*|volatile[a-z]*|sensory)\b"
    ),
    "aromes_procede": re.compile(
        r"\b(?:aroma[a-z]*|volatile[a-z]*|sensory|flavou?r[a-z]*|process[a-z]*|"
        r"production|ferment[a-z]*|quality|proces[a-z]*|elabor[a-z]*|"
        r"producci[a-z]*|calidad)\b"
    ),
}


class HarvestSourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    queries_completed: int = Field(ge=0)
    records_received: int = Field(ge=0)
    abstracts_received: int = Field(ge=0)


class RelevanceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "review", "rejected"]
    score: float = Field(ge=0.0, le=1.0)
    reason: str


class BibliographicHarvestReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    state: Literal["completed", "partial", "failed"]
    profile: str
    themes: list[str]
    sources: list[str]
    per_source_limit: int
    raw_record_count: int = Field(ge=0)
    unique_record_count: int = Field(ge=0)
    abstract_record_count: int = Field(ge=0)
    accepted_record_count: int = Field(ge=0)
    accepted_abstract_count: int = Field(ge=0)
    source_summaries: list[HarvestSourceSummary]
    errors: list[dict[str, str]]
    query_wave: int = Field(default=0, ge=0)
    result_offset: int = Field(default=0, ge=0)
    openalex_daily_remaining_before_usd: float | None = None
    openalex_daily_remaining_after_usd: float | None = None
    started_at: datetime
    completed_at: datetime


class AbstractBackfillReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None
    state: Literal["completed", "failed", "skipped"]
    candidates: int = Field(ge=0)
    matched_records: int = Field(ge=0)
    abstracts_added: int = Field(ge=0)
    errors: list[dict[str, str]]
    openalex_daily_remaining_before_usd: float | None = None
    openalex_daily_remaining_after_usd: float | None = None


class BulkHarvestReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str
    target_new_accepted_abstracts: int = Field(ge=1)
    baseline_accepted_abstracts: int = Field(ge=0)
    final_accepted_abstracts: int = Field(ge=0)
    new_accepted_abstracts: int = Field(ge=0)
    target_reached: bool
    stop_reason: Literal["target_reached", "max_runs", "no_progress"]
    harvest_runs: list[BibliographicHarvestReport]
    backfill_runs: list[AbstractBackfillReport]


class HarvestNotDue(RuntimeError):
    """The configured weekly interval has not elapsed yet."""


def _canonical_title_key(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title).casefold()
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    title_key = TITLE_KEY.sub("", ascii_title)
    if not title_key:
        title_key = hashlib.sha256(title.encode("utf-8")).hexdigest()
    return title_key


def _canonical_key(record: BibliographicRecord) -> str:
    if record.doi:
        return f"doi:{record.doi}"
    title_key = _canonical_title_key(record.title)
    return f"title:{title_key}:{record.publication_year or 'unknown'}"


def _content_hash(values: dict[str, Any]) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _searchable_text(value: str | None) -> str:
    ascii_text = _folded_text(value)
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def _bibliographic_metadata_query(query: str) -> tuple[str, list[Any]]:
    """Build an AND-of-terms predicate across user-facing notice metadata."""

    normalized_query = " ".join(query.split())
    if not normalized_query:
        return "1 = 1", []
    terms = [term.strip(",;:\"'") for term in normalized_query.split()]
    terms = [term for term in terms if term]
    if len(terms) > 50:
        raise ValueError("bibliographic query cannot exceed 50 terms")
    clauses: list[str] = []
    parameters: list[Any] = []
    for term in terms:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        clauses.append(
            """
            (r.title LIKE ? ESCAPE '\\' COLLATE NOCASE
             OR r.doi LIKE ? ESCAPE '\\' COLLATE NOCASE
             OR r.journal LIKE ? ESCAPE '\\' COLLATE NOCASE
             OR r.authors LIKE ? ESCAPE '\\' COLLATE NOCASE
             OR CAST(r.publication_year AS TEXT) LIKE ? ESCAPE '\\'
             OR CAST(r.citation_count AS TEXT) LIKE ? ESCAPE '\\'
             OR r.relevance_theme LIKE ? ESCAPE '\\' COLLATE NOCASE
             OR r.url LIKE ? ESCAPE '\\' COLLATE NOCASE
             OR EXISTS (
                 SELECT 1 FROM bibliographic_record_sources AS query_source
                 WHERE query_source.record_id = r.id
                   AND (
                       query_source.source LIKE ? ESCAPE '\\' COLLATE NOCASE
                       OR query_source.source_id LIKE ? ESCAPE '\\' COLLATE NOCASE
                   )
             ))
            """
        )
        parameters.extend([pattern] * 10)
    predicate = " AND ".join(clauses)
    if len(terms) == 2 and all(term.replace("-", "").isalpha() for term in terms):
        first, second = terms
        author_aliases = [
            f'%"{first} {second}%',
            f'%"{second} {first}%',
            f'%"{first}, {second}%',
            f'%"{second}, {first}%',
            f'%"{first} {second[0]}%',
            f'%"{second} {first[0]}%',
        ]
        predicate = (
            f"(({predicate}) OR "
            + " OR ".join("r.authors LIKE ? COLLATE NOCASE" for _ in author_aliases)
            + ")"
        )
        parameters.extend(author_aliases)
    return predicate, parameters


def _folded_text(value: str | None) -> str:
    """Case-fold text while preserving punctuation needed by exact domain rules."""

    normalized = unicodedata.normalize("NFKD", value or "").casefold()
    normalized = "".join(
        " "
        if ord(character) > 127 and unicodedata.category(character)[0] in {"P", "S"}
        else character
        for character in normalized
    )
    return normalized.encode("ascii", "ignore").decode("ascii")


def infer_cider_themes(value: str) -> set[str]:
    """Infer scientific themes for retrieval-time ranking without an LLM call."""

    text = _searchable_text(value)
    themes = {theme for theme, pattern in THEME_PATTERNS.items() if pattern.search(text)}
    if re.search(r"\b(calvados|eau de vie|apple brandy|distill[a-z]*)\b", text):
        themes.add("calvados_eau_vie")
    if re.search(r"\b(pommeau|mistelle|mutage)\b", text):
        themes.add("pommeau")
    return themes


def _is_non_apple_cider_title(title: str) -> bool:
    """Detect a non-apple cider base without rejecting fruit additions to apple cider."""

    if TRUE_APPLE_PATTERN.search(title):
        return False
    fruit_matches = list(NON_APPLE_FRUIT_PATTERN.finditer(title))
    cider_matches = list(DOMAIN_PATTERN.finditer(title))
    for fruit in fruit_matches:
        for cider in cider_matches:
            if fruit.end() <= cider.start():
                between = title[fruit.end() : cider.start()]
                if not NON_APPLE_ADDITION_PATTERN.search(between):
                    return True
            elif cider.end() <= fruit.start():
                between = title[cider.end() : fruit.start()]
                if not between.strip() or NON_APPLE_SOURCE_PATTERN.search(between):
                    return True
    return False


def assess_cider_relevance(
    record: BibliographicRecord,
    theme: str,
) -> RelevanceAssessment:
    """Conservative domain gate: preserve noise locally, exclude it from RAG."""

    title = _searchable_text(record.title)
    abstract = _searchable_text(record.abstract)
    theme_pattern = THEME_PATTERNS.get(theme, re.compile(r"a^"))
    combined = f"{title} {abstract}"
    title_theme = bool(theme_pattern.search(title))
    abstract_theme = bool(theme_pattern.search(abstract))
    strong_technical_context = bool(
        STRONG_TECHNICAL_CONTEXT_PATTERN.search(combined) or title_theme or abstract_theme
    )
    title_direct_domain = bool(DOMAIN_PATTERN.search(title))
    abstract_direct_domain = bool(DOMAIN_PATTERN.search(abstract))
    title_domain = bool(
        APPLE_BEVERAGE_PATTERN.search(title)
        or APPLE_MATERIAL_PATTERN.search(title)
        or (title_direct_domain and strong_technical_context)
    )
    abstract_domain = bool(
        APPLE_BEVERAGE_PATTERN.search(abstract)
        or APPLE_MATERIAL_PATTERN.search(abstract)
        or (abstract_direct_domain and strong_technical_context)
    )

    score = 0.0
    reasons: list[str] = []
    if title_domain:
        score += 0.55
        reasons.append("domaine cidricole dans le titre")
    if abstract_domain:
        score += 0.25
        reasons.append("domaine cidricole dans l'abstract")
    if title_theme:
        score += 0.30
        reasons.append(f"thème {theme} dans le titre")
    if abstract_theme:
        score += 0.15
        reasons.append(f"thème {theme} dans l'abstract")
    health_only = bool(HEALTH_ONLY_PATTERN.search(title))
    historical = record.publication_year is not None and record.publication_year < 1900
    vinegar = bool(VINEGAR_PATTERN.search(title))
    false_apple = bool(FALSE_APPLE_PATTERN.search(_folded_text(record.title)))
    non_apple_cider = _is_non_apple_cider_title(title)
    off_topic = bool(OFF_TOPIC_TITLE_PATTERN.search(title))
    acronym_title = bool(re.match(r"^cider(?::|$)", title)) or title == "cider"
    uppercase_acronym = bool(re.search(r"\bCIDER\b", record.title))
    beverage_context = bool(BEVERAGE_CONTEXT_PATTERN.search(f"{title} {abstract}"))
    non_beverage_acronym = (acronym_title or uppercase_acronym) and (
        bool(NON_BEVERAGE_ABSTRACT_PATTERN.search(abstract)) or not beverage_context
    )
    domain_without_technical_context = bool(title_direct_domain or abstract_direct_domain) and not (
        strong_technical_context
        or APPLE_BEVERAGE_PATTERN.search(combined)
        or APPLE_MATERIAL_PATTERN.search(combined)
    )
    social_or_geographic_off_topic = bool(SOCIAL_OR_GEOGRAPHIC_OFF_TOPIC_PATTERN.search(title))
    if health_only:
        score -= 0.40
        reasons.append("orientation santé hors conception cidricole")
    if historical:
        score -= 0.40
        reasons.append("document historique antérieur à 1900")
    if vinegar:
        score -= 0.50
        reasons.append("vinaigre hors périmètre initial")
    if false_apple:
        score -= 0.70
        reasons.append("nom vernaculaire contenant apple mais espèce hors pomme")
    if non_apple_cider:
        score -= 0.50
        reasons.append("cidre explicitement produit avec un fruit autre que la pomme")
    if off_topic or non_beverage_acronym:
        score -= 0.70
        reasons.append("homonyme ou orientation non alimentaire hors périmètre")
    if domain_without_technical_context:
        score -= 0.70
        reasons.append("terme cidricole sans contexte technique de boisson ou de pomme")
    if social_or_geographic_off_topic:
        score -= 0.70
        reasons.append("orientation sociale, historique, géographique ou médicale hors périmètre")
    score = round(min(max(score, 0.0), 1.0), 3)
    excluded_orientation = (
        health_only
        or historical
        or vinegar
        or false_apple
        or non_apple_cider
        or off_topic
        or non_beverage_acronym
        or domain_without_technical_context
        or social_or_geographic_off_topic
    )
    if excluded_orientation:
        status: Literal["accepted", "review", "rejected"] = "rejected"
    elif score >= 0.70 and title_domain:
        status: Literal["accepted", "review", "rejected"] = "accepted"
    elif score >= 0.45 and (title_domain or abstract_domain):
        status = "review"
    else:
        status = "rejected"
    return RelevanceAssessment(
        status=status,
        score=score,
        reason="; ".join(reasons) or "aucun ancrage cidricole détecté",
    )


def assess_cider_relevance_across_themes(
    record: BibliographicRecord,
    themes: Iterable[str] | None = None,
) -> tuple[str, RelevanceAssessment]:
    """Select the strongest deterministic cider-theme assessment for a record."""

    theme_names = tuple(themes or CIDER_PILOT_THEMES)
    if not theme_names:
        raise ValueError("at least one cider relevance theme is required")
    assessed = [(theme, assess_cider_relevance(record, theme)) for theme in theme_names]
    status_order = {"rejected": 0, "review": 1, "accepted": 2}
    return max(
        assessed,
        key=lambda item: (item[1].score, status_order[item[1].status]),
    )


class BibliographicReviewConflictError(Exception):
    """The selected notice can no longer receive a review decision."""


class BibliographicHarvestStore:
    """Normalize harvested records without mixing them with page-based PDFs."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.doi_exclusions = DoiExclusionRegistry.for_database(database.path)
        if not self.doi_exclusions.path.exists():
            self._sync_historical_doi_exclusions()

    def _sync_historical_doi_exclusions(self) -> None:
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT doi, title, relevance_reason AS reason,
                    last_archived_at AS excluded_at
                FROM rejected_bibliographic_archive
                WHERE doi IS NOT NULL
                ORDER BY doi COLLATE NOCASE
                """
            )
            self.doi_exclusions.ensure_historical(dict(row) for row in rows)

    def start_run(
        self,
        settings: Settings,
        *,
        themes: dict[str, str],
        sources: list[str],
    ) -> tuple[str, datetime]:
        run_id = str(uuid.uuid4())
        started_at = datetime.now(UTC)
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO bibliographic_harvest_runs (
                    id, profile, state, themes, sources, per_source_limit,
                    request_delay_seconds, started_at
                ) VALUES (?, ?, 'running', ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    settings.harvest.profile,
                    json.dumps(themes, ensure_ascii=False),
                    json.dumps(sources, ensure_ascii=False),
                    settings.harvest.per_source_limit,
                    settings.harvest.request_delay_seconds,
                    started_at.isoformat(),
                ),
            )
        return run_id, started_at

    def last_completed_at(self, profile: str) -> datetime | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT completed_at
                FROM bibliographic_harvest_runs
                WHERE profile = ? AND state IN ('completed', 'partial')
                ORDER BY completed_at DESC
                LIMIT 1
                """,
                (profile,),
            ).fetchone()
        if row is None or row["completed_at"] is None:
            return None
        return datetime.fromisoformat(str(row["completed_at"]))

    def is_due(self, settings: Settings, *, now: datetime | None = None) -> bool:
        last = self.last_completed_at(settings.harvest.profile)
        if last is None:
            return True
        current = now or datetime.now(UTC)
        return current >= last + timedelta(hours=settings.harvest.cadence_hours)

    def completed_run_count(self, profile: str) -> int:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM bibliographic_harvest_runs
                WHERE profile = ? AND state IN ('completed', 'partial')
                """,
                (profile,),
            ).fetchone()
        return int(row[0] or 0)

    def missing_abstract_candidates(
        self,
        *,
        profile: str,
        limit: int = 100,
        retry_after_days: int = 30,
    ) -> list[Any]:
        if not 1 <= limit <= 100:
            raise ValueError("abstract backfill limit must be between 1 and 100")
        if retry_after_days < 1:
            raise ValueError("abstract retry interval must be positive")
        with closing(self.database.connect()) as connection:
            return list(
                connection.execute(
                    """
                    SELECT r.id, r.doi, r.title, r.relevance_theme
                    FROM bibliographic_records AS r
                    WHERE r.relevance_status = 'accepted'
                      AND (r.abstract IS NULL OR trim(r.abstract) = '')
                      AND r.doi IS NOT NULL AND trim(r.doi) != ''
                      AND NOT EXISTS (
                          SELECT 1
                          FROM bibliographic_harvest_hits AS h
                          JOIN bibliographic_harvest_runs AS run ON run.id = h.run_id
                          WHERE h.record_id = r.id
                            AND run.profile = ?
                            AND datetime(run.completed_at) >= datetime(
                                'now', ?
                            )
                      )
                    ORDER BY r.relevance_score DESC,
                        r.publication_year DESC,
                        r.updated_at,
                        r.id
                    LIMIT ?
                    """,
                    (profile, f"-{retry_after_days} days", limit),
                )
            )

    def record_backfill_attempt(
        self,
        *,
        run_id: str,
        record_id: str,
        theme: str,
        rank: int,
        source: str,
    ) -> None:
        """Persist a lookup miss so it is not retried on every scheduler check."""

        with self.database.transaction() as connection:
            record = connection.execute(
                """
                SELECT relevance_status, relevance_score, relevance_reason
                FROM bibliographic_records WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
            if record is None:
                raise ValueError("bibliographic record no longer exists")
            connection.execute(
                """
                INSERT OR REPLACE INTO bibliographic_harvest_hits (
                    run_id, theme, record_id, source, rank,
                    relevance_status, relevance_score, relevance_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    theme,
                    record_id,
                    source,
                    rank,
                    record["relevance_status"],
                    record["relevance_score"],
                    record["relevance_reason"],
                ),
            )

    def upsert_hit(
        self,
        *,
        run_id: str,
        theme: str,
        rank: int,
        record: BibliographicRecord,
        _connection: Any | None = None,
    ) -> str | None:
        if self.doi_exclusions.is_excluded(record.doi):
            return None
        canonical_key = _canonical_key(record)
        assessment = assess_cider_relevance(record, theme)
        transaction = (
            nullcontext(_connection) if _connection is not None else self.database.transaction()
        )
        with transaction as connection:
            existing = connection.execute(
                """
                SELECT * FROM bibliographic_records
                WHERE canonical_key = ?
                   OR (? IS NOT NULL AND doi = ? COLLATE NOCASE)
                   OR (
                       lower(title) = lower(?)
                       AND COALESCE(publication_year, -1) = COALESCE(?, -1)
                       AND (
                           ? IS NULL OR doi IS NULL OR doi = ? COLLATE NOCASE
                       )
                   )
                ORDER BY CASE
                    WHEN ? IS NOT NULL AND doi = ? COLLATE NOCASE THEN 0
                    WHEN canonical_key = ? THEN 1
                    ELSE 2
                END
                LIMIT 1
                """,
                (
                    canonical_key,
                    record.doi,
                    record.doi,
                    record.title,
                    record.publication_year,
                    record.doi,
                    record.doi,
                    record.doi,
                    record.doi,
                    canonical_key,
                ),
            ).fetchone()
            if existing is None and record.publication_year is not None and record.doi is not None:
                provisional_key = (
                    f"title:{_canonical_title_key(record.title)}:{record.publication_year}"
                )
                existing = connection.execute(
                    """
                    SELECT * FROM bibliographic_records
                    WHERE canonical_key = ? AND doi IS NULL
                    ORDER BY created_at, id
                    LIMIT 1
                    """,
                    (provisional_key,),
                ).fetchone()
            if existing is None:
                record_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"local-science-rag:{canonical_key}",
                    )
                )
                values = self._new_values(record)
                connection.execute(
                    """
                    INSERT INTO bibliographic_records (
                        id, canonical_key, doi, title, abstract, authors,
                        journal, work_type, publisher, publication_year, citation_count, url,
                        content_hash, embedding_status, relevance_status,
                        relevance_score, relevance_reason, relevance_theme
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        canonical_key,
                        values["doi"],
                        values["title"],
                        values["abstract"],
                        values["authors"],
                        values["journal"],
                        values["work_type"],
                        values["publisher"],
                        values["publication_year"],
                        values["citation_count"],
                        values["url"],
                        values["content_hash"],
                        values["embedding_status"],
                        assessment.status,
                        assessment.score,
                        assessment.reason,
                        theme,
                    ),
                )
            else:
                record_id = str(existing["id"])
                values = self._merged_values(dict(existing), record)
                status = str(existing["embedding_status"])
                if values["content_hash"] != existing["content_hash"]:
                    status = "pending" if values["abstract"] else "not_applicable"
                connection.execute(
                    """
                    UPDATE bibliographic_records
                    SET doi = ?, title = ?, abstract = ?, authors = ?, journal = ?,
                        work_type = ?, publisher = ?, publication_year = ?,
                        citation_count = ?, url = ?,
                        content_hash = ?, embedding_status = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        values["doi"],
                        values["title"],
                        values["abstract"],
                        values["authors"],
                        values["journal"],
                        values["work_type"],
                        values["publisher"],
                        values["publication_year"],
                        values["citation_count"],
                        values["url"],
                        values["content_hash"],
                        status,
                        record_id,
                    ),
                )
            connection.execute(
                """
                INSERT INTO bibliographic_record_sources (
                    record_id, source, source_id
                ) VALUES (?, ?, ?)
                ON CONFLICT(record_id, source, source_id) DO UPDATE SET
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (record_id, record.source, record.source_id),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO bibliographic_harvest_hits (
                    run_id, theme, record_id, source, rank,
                    relevance_status, relevance_score, relevance_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    theme,
                    record_id,
                    record.source,
                    rank,
                    assessment.status,
                    assessment.score,
                    assessment.reason,
                ),
            )
            self._refresh_record_relevance(connection, record_id)
        return record_id

    def upsert_hits(
        self,
        *,
        run_id: str,
        hits: Iterable[tuple[str, int, BibliographicRecord]],
    ) -> list[str | None]:
        """Persist one fetched page atomically with a single SQLite transaction."""

        with self.database.transaction() as connection:
            return [
                self.upsert_hit(
                    run_id=run_id,
                    theme=theme,
                    rank=rank,
                    record=record,
                    _connection=connection,
                )
                for theme, rank, record in hits
            ]

    @staticmethod
    def _refresh_record_relevance(
        connection: Any,
        record_id: str,
    ) -> None:
        best = connection.execute(
            """
            SELECT theme, relevance_status, relevance_score, relevance_reason
            FROM bibliographic_harvest_hits
            WHERE record_id = ?
            ORDER BY COALESCE(relevance_score, -1) DESC,
                CASE relevance_status
                    WHEN 'accepted' THEN 0 WHEN 'review' THEN 1
                    WHEN 'rejected' THEN 2 ELSE 3 END
            LIMIT 1
            """,
            (record_id,),
        ).fetchone()
        if best is None:
            return
        current = connection.execute(
            """
            SELECT abstract, embedding_status, manual_decision
            FROM bibliographic_records WHERE id = ?
            """,
            (record_id,),
        ).fetchone()
        if current is None:
            return
        if current["manual_decision"] == "accepted":
            embedding_status = str(current["embedding_status"])
            if not current["abstract"]:
                embedding_status = "not_applicable"
            elif embedding_status == "not_applicable":
                embedding_status = "pending"
            connection.execute(
                """
                UPDATE bibliographic_records
                SET relevance_status = 'accepted', embedding_status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (embedding_status, record_id),
            )
            return
        accepted = str(best["relevance_status"]) == "accepted"
        has_abstract = bool(current["abstract"])
        embedding_status = str(current["embedding_status"])
        if not accepted or not has_abstract:
            embedding_status = "not_applicable"
        elif embedding_status == "not_applicable":
            embedding_status = "pending"
        connection.execute(
            """
            UPDATE bibliographic_records
            SET relevance_status = ?, relevance_score = ?, relevance_reason = ?,
                relevance_theme = ?, embedding_status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                best["relevance_status"],
                best["relevance_score"],
                best["relevance_reason"],
                best["theme"],
                embedding_status,
                record_id,
            ),
        )

    @staticmethod
    def _refresh_run_acceptance_counts(connection: Any) -> None:
        connection.execute(
            """
            UPDATE bibliographic_harvest_runs
            SET accepted_record_count = (
                    SELECT COUNT(DISTINCT h.record_id)
                    FROM bibliographic_harvest_hits AS h
                    WHERE h.run_id = bibliographic_harvest_runs.id
                      AND h.relevance_status = 'accepted'
                ),
                accepted_abstract_count = (
                    SELECT COUNT(DISTINCT h.record_id)
                    FROM bibliographic_harvest_hits AS h
                    JOIN bibliographic_records AS r ON r.id = h.record_id
                    WHERE h.run_id = bibliographic_harvest_runs.id
                      AND h.relevance_status = 'accepted'
                      AND r.abstract IS NOT NULL
                )
            """
        )

    @staticmethod
    def _new_values(record: BibliographicRecord) -> dict[str, Any]:
        values: dict[str, Any] = {
            "doi": record.doi,
            "title": record.title,
            "abstract": record.abstract,
            "authors": json.dumps(record.authors, ensure_ascii=False),
            "journal": record.journal,
            "work_type": record.work_type,
            "publisher": record.publisher,
            "publication_year": record.publication_year,
            "citation_count": record.citation_count,
            "url": record.url,
        }
        values["content_hash"] = _content_hash(values)
        values["embedding_status"] = "pending" if record.abstract else "not_applicable"
        return values

    @staticmethod
    def _merged_values(existing: dict[str, Any], record: BibliographicRecord) -> dict[str, Any]:
        try:
            existing_authors = json.loads(existing.get("authors") or "[]")
        except json.JSONDecodeError:
            existing_authors = []
        authors = list(
            dict.fromkeys(
                [
                    str(author)
                    for author in [*existing_authors, *record.authors]
                    if str(author).strip()
                ]
            )
        )
        old_abstract = existing.get("abstract")
        if isinstance(old_abstract, str):
            old_abstract = html.unescape(old_abstract)
        abstract = record.abstract
        if isinstance(old_abstract, str) and (not abstract or len(old_abstract) >= len(abstract)):
            abstract = old_abstract
        citation_counts = [
            value
            for value in (existing.get("citation_count"), record.citation_count)
            if isinstance(value, int)
        ]
        existing_title = str(existing.get("title") or "")
        normalized_existing_title = html.unescape(existing_title)
        values: dict[str, Any] = {
            "doi": existing.get("doi") or record.doi,
            "title": (
                record.title
                if existing_title == "Titre indisponible"
                else normalized_existing_title or record.title
            ),
            "abstract": abstract,
            "authors": json.dumps(authors, ensure_ascii=False),
            "journal": existing.get("journal") or record.journal,
            "work_type": existing.get("work_type") or record.work_type,
            "publisher": existing.get("publisher") or record.publisher,
            "publication_year": (existing.get("publication_year") or record.publication_year),
            "citation_count": max(citation_counts) if citation_counts else None,
            "url": existing.get("url") or record.url,
        }
        values["content_hash"] = _content_hash(values)
        return values

    def merge_doi_enrichment_duplicates(self) -> list[str]:
        """Merge legacy DOI-less notices into their single DOI-enriched twin.

        The maintenance rule is intentionally conservative: title after Unicode
        normalization and publication year must match, and the group must contain
        exactly one DOI-bearing record. Records carrying different DOI values are
        never merged.
        """

        with self.database.transaction() as connection:
            records = list(
                connection.execute(
                    """
                    SELECT * FROM bibliographic_records
                    ORDER BY created_at, id
                    """
                )
            )
            groups: dict[tuple[str, int | None], list[Any]] = {}
            for record in records:
                key = (
                    _canonical_title_key(str(record["title"])),
                    record["publication_year"],
                )
                groups.setdefault(key, []).append(record)

            merged_ids: list[str] = []
            survivors: set[str] = set()
            for group in groups.values():
                doi_records = [record for record in group if record["doi"]]
                provisional_records = [record for record in group if not record["doi"]]
                if len(doi_records) != 1 or not provisional_records:
                    continue
                survivor = doi_records[0]
                survivor_id = str(survivor["id"])
                for duplicate in provisional_records:
                    self._merge_record_into(
                        connection,
                        survivor_id=survivor_id,
                        duplicate=dict(duplicate),
                    )
                    merged_ids.append(str(duplicate["id"]))
                survivors.add(survivor_id)

            for survivor_id in survivors:
                self._refresh_record_relevance(connection, survivor_id)
            if merged_ids:
                self._refresh_run_acceptance_counts(connection)
        return merged_ids

    @classmethod
    def _merge_record_into(
        cls,
        connection: Any,
        *,
        survivor_id: str,
        duplicate: dict[str, Any],
    ) -> None:
        survivor = connection.execute(
            "SELECT * FROM bibliographic_records WHERE id = ?",
            (survivor_id,),
        ).fetchone()
        if survivor is None:
            raise RuntimeError("DOI-enriched bibliographic survivor is missing")
        try:
            authors = json.loads(duplicate.get("authors") or "[]")
        except json.JSONDecodeError:
            authors = []
        merged_values = cls._merged_values(
            dict(survivor),
            BibliographicRecord(
                source="local",
                source_id=str(duplicate["id"]),
                title=str(duplicate["title"]),
                abstract=(str(duplicate["abstract"]) if duplicate["abstract"] else None),
                authors=[str(author) for author in authors],
                journal=(str(duplicate["journal"]) if duplicate["journal"] else None),
                work_type=(str(duplicate["work_type"]) if duplicate["work_type"] else None),
                publisher=(str(duplicate["publisher"]) if duplicate["publisher"] else None),
                publication_year=duplicate["publication_year"],
                doi=None,
                citation_count=duplicate["citation_count"],
                url=(str(duplicate["url"]) if duplicate["url"] else None),
            ),
        )
        embedding_status = str(survivor["embedding_status"])
        if merged_values["content_hash"] != survivor["content_hash"]:
            embedding_status = "pending" if merged_values["abstract"] else "not_applicable"
        manual_decision = survivor["manual_decision"] or duplicate["manual_decision"]
        manual_reviewed_at = survivor["manual_reviewed_at"] or duplicate["manual_reviewed_at"]
        connection.execute(
            """
            UPDATE bibliographic_records
            SET title = ?, abstract = ?, authors = ?, journal = ?, work_type = ?, publisher = ?,
                publication_year = ?, citation_count = ?, url = ?,
                content_hash = ?, embedding_status = ?, manual_decision = ?,
                manual_reviewed_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                merged_values["title"],
                merged_values["abstract"],
                merged_values["authors"],
                merged_values["journal"],
                merged_values["work_type"],
                merged_values["publisher"],
                merged_values["publication_year"],
                merged_values["citation_count"],
                merged_values["url"],
                merged_values["content_hash"],
                embedding_status,
                manual_decision,
                manual_reviewed_at,
                survivor_id,
            ),
        )

        duplicate_id = str(duplicate["id"])
        sources = list(
            connection.execute(
                """
                SELECT source, source_id, first_seen_at, last_seen_at
                FROM bibliographic_record_sources WHERE record_id = ?
                """,
                (duplicate_id,),
            )
        )
        for source in sources:
            connection.execute(
                """
                INSERT INTO bibliographic_record_sources (
                    record_id, source, source_id, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(record_id, source, source_id) DO UPDATE SET
                    first_seen_at = MIN(first_seen_at, excluded.first_seen_at),
                    last_seen_at = MAX(last_seen_at, excluded.last_seen_at)
                """,
                (
                    survivor_id,
                    source["source"],
                    source["source_id"],
                    source["first_seen_at"],
                    source["last_seen_at"],
                ),
            )

        hits = list(
            connection.execute(
                "SELECT * FROM bibliographic_harvest_hits WHERE record_id = ?",
                (duplicate_id,),
            )
        )
        for hit in hits:
            existing_hit = connection.execute(
                """
                SELECT * FROM bibliographic_harvest_hits
                WHERE run_id = ? AND theme = ? AND record_id = ? AND source = ?
                """,
                (hit["run_id"], hit["theme"], survivor_id, hit["source"]),
            ).fetchone()
            if existing_hit is None:
                connection.execute(
                    """
                    INSERT INTO bibliographic_harvest_hits (
                        run_id, theme, record_id, source, rank,
                        relevance_status, relevance_score, relevance_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        hit["run_id"],
                        hit["theme"],
                        survivor_id,
                        hit["source"],
                        hit["rank"],
                        hit["relevance_status"],
                        hit["relevance_score"],
                        hit["relevance_reason"],
                    ),
                )
                continue
            duplicate_score = hit["relevance_score"]
            existing_score = existing_hit["relevance_score"]
            use_duplicate = duplicate_score is not None and (
                existing_score is None or duplicate_score > existing_score
            )
            connection.execute(
                """
                UPDATE bibliographic_harvest_hits
                SET rank = ?, relevance_status = ?, relevance_score = ?,
                    relevance_reason = ?
                WHERE run_id = ? AND theme = ? AND record_id = ? AND source = ?
                """,
                (
                    min(int(hit["rank"]), int(existing_hit["rank"])),
                    (
                        hit["relevance_status"]
                        if use_duplicate
                        else existing_hit["relevance_status"]
                    ),
                    duplicate_score if use_duplicate else existing_score,
                    (
                        hit["relevance_reason"]
                        if use_duplicate
                        else existing_hit["relevance_reason"]
                    ),
                    hit["run_id"],
                    hit["theme"],
                    survivor_id,
                    hit["source"],
                ),
            )

        connection.execute(
            "DELETE FROM bibliographic_records WHERE id = ?",
            (duplicate_id,),
        )

    def finish_run(
        self,
        *,
        run_id: str,
        state: Literal["completed", "partial", "failed"],
        raw_record_count: int,
        errors: list[dict[str, str]],
        completed_at: datetime,
    ) -> tuple[int, int, int, int]:
        with self.database.transaction() as connection:
            counts = connection.execute(
                """
                SELECT
                    COUNT(DISTINCT h.record_id) AS unique_count,
                    COUNT(DISTINCT CASE WHEN r.abstract IS NOT NULL
                        THEN h.record_id END) AS abstract_count,
                    COUNT(DISTINCT CASE WHEN h.relevance_status = 'accepted'
                        THEN h.record_id END) AS accepted_count,
                    COUNT(DISTINCT CASE WHEN h.relevance_status = 'accepted'
                        AND r.abstract IS NOT NULL THEN h.record_id END)
                        AS accepted_abstract_count
                FROM bibliographic_harvest_hits AS h
                JOIN bibliographic_records AS r ON r.id = h.record_id
                WHERE h.run_id = ?
                """,
                (run_id,),
            ).fetchone()
            unique_count = int(counts["unique_count"] or 0)
            abstract_count = int(counts["abstract_count"] or 0)
            accepted_count = int(counts["accepted_count"] or 0)
            accepted_abstract_count = int(counts["accepted_abstract_count"] or 0)
            connection.execute(
                """
                UPDATE bibliographic_harvest_runs
                SET state = ?, raw_record_count = ?, unique_record_count = ?,
                    abstract_record_count = ?, accepted_record_count = ?,
                    accepted_abstract_count = ?, errors = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    state,
                    raw_record_count,
                    unique_count,
                    abstract_count,
                    accepted_count,
                    accepted_abstract_count,
                    json.dumps(errors, ensure_ascii=False),
                    completed_at.isoformat(),
                    run_id,
                ),
            )
        return unique_count, abstract_count, accepted_count, accepted_abstract_count

    def recover_interrupted_run(
        self,
        *,
        run_id: str,
        reason: str,
        completed_at: datetime,
    ) -> dict[str, Any]:
        """Close one explicitly selected run interrupted before ``finish_run``.

        This operation is intentionally opt-in and only accepts a run that is still
        marked ``running``.  Its persisted hits remain authoritative: a run with at
        least one hit becomes ``partial``; an empty run becomes ``failed``.
        """

        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("interrupted harvest recovery reason is required")
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT state, raw_record_count, errors, started_at
                FROM bibliographic_harvest_runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"unknown bibliographic harvest run: {run_id}")
        if str(row["state"]) != "running":
            raise ValueError(f"bibliographic harvest run is not running: {run_id}")

        errors: list[dict[str, str]] = []
        try:
            existing_errors = json.loads(str(row["errors"] or "[]"))
        except json.JSONDecodeError:
            existing_errors = []
        if isinstance(existing_errors, list):
            errors.extend(error for error in existing_errors if isinstance(error, dict))
        errors.append(
            {
                "source": "campaign_recovery",
                "theme": "all",
                "error_type": "InterruptedHarvestRecovered",
                "message": normalized_reason,
            }
        )

        raw_record_count = int(row["raw_record_count"] or 0)
        with closing(self.database.connect()) as connection:
            hit_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM bibliographic_harvest_hits WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                or 0
            )
        recovered_state: Literal["partial", "failed"] = "partial" if hit_count else "failed"
        unique_count, abstract_count, accepted_count, accepted_abstract_count = self.finish_run(
            run_id=run_id,
            state=recovered_state,
            raw_record_count=raw_record_count,
            errors=errors,
            completed_at=completed_at,
        )
        return {
            "run_id": run_id,
            "state": recovered_state,
            "started_at": str(row["started_at"]),
            "completed_at": completed_at.isoformat(),
            "raw_record_count": raw_record_count,
            "unique_record_count": unique_count,
            "abstract_record_count": abstract_count,
            "accepted_record_count": accepted_count,
            "accepted_abstract_count": accepted_abstract_count,
            "reason": normalized_reason,
        }

    def statistics(self) -> dict[str, Any]:
        with closing(self.database.connect()) as connection:
            counts = connection.execute(
                """
                SELECT COUNT(*) AS stored_records,
                    COUNT(CASE WHEN abstract IS NOT NULL THEN 1 END) AS stored_abstracts,
                    COUNT(CASE WHEN relevance_status = 'accepted' THEN 1 END)
                        AS records,
                    COUNT(CASE WHEN relevance_status = 'accepted'
                        AND abstract IS NOT NULL THEN 1 END) AS abstracts,
                    COUNT(CASE WHEN relevance_status = 'accepted'
                        AND embedding_status = 'indexed' THEN 1 END) AS indexed,
                    COUNT(CASE WHEN relevance_status = 'review' THEN 1 END) AS review,
                    COUNT(CASE WHEN relevance_status IN ('rejected', 'unreviewed')
                        THEN 1 END) AS quarantined
                FROM bibliographic_records
                """
            ).fetchone()
            latest = connection.execute(
                """
                SELECT id, state, unique_record_count, abstract_record_count,
                       accepted_record_count, accepted_abstract_count,
                       started_at, completed_at
                FROM bibliographic_harvest_runs
                ORDER BY started_at DESC LIMIT 1
                """
            ).fetchone()
        return {
            "records": int(counts["records"] or 0),
            "abstracts": int(counts["abstracts"] or 0),
            "indexed": int(counts["indexed"] or 0),
            "stored_records": int(counts["stored_records"] or 0),
            "stored_abstracts": int(counts["stored_abstracts"] or 0),
            "review": int(counts["review"] or 0),
            "quarantined": int(counts["quarantined"] or 0),
            "latest_run": dict(latest) if latest is not None else None,
        }

    def archive_rejected_records(self) -> list[dict[str, object]]:
        """Persist an auditable DOI/title snapshot before any destructive cleanup."""

        with self.database.transaction() as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT r.id AS original_record_id, r.canonical_key, r.doi,
                        r.title, r.relevance_score, r.relevance_reason,
                        r.relevance_theme, r.created_at AS original_created_at,
                        r.updated_at AS original_updated_at,
                        COALESCE((
                            SELECT json_group_array(source)
                            FROM (
                                SELECT DISTINCT source
                                FROM bibliographic_record_sources
                                WHERE record_id = r.id
                                ORDER BY source
                            )
                        ), '[]') AS sources,
                        COALESCE((
                            SELECT json_group_array(run_id)
                            FROM (
                                SELECT DISTINCT run_id
                                FROM bibliographic_harvest_hits
                                WHERE record_id = r.id
                                ORDER BY run_id
                            )
                        ), '[]') AS harvest_run_ids
                    FROM bibliographic_records AS r
                    WHERE r.relevance_status = 'rejected'
                    ORDER BY r.id
                    """
                )
            )
            archived: list[dict[str, object]] = []
            for row in rows:
                record = dict(row)
                connection.execute(
                    """
                    INSERT INTO rejected_bibliographic_archive (
                        original_record_id, canonical_key, doi, title,
                        relevance_score, relevance_reason, relevance_theme,
                        sources, harvest_run_ids, original_created_at,
                        original_updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(original_record_id) DO UPDATE SET
                        canonical_key = excluded.canonical_key,
                        doi = COALESCE(excluded.doi, rejected_bibliographic_archive.doi),
                        title = excluded.title,
                        relevance_score = excluded.relevance_score,
                        relevance_reason = excluded.relevance_reason,
                        relevance_theme = excluded.relevance_theme,
                        sources = excluded.sources,
                        harvest_run_ids = excluded.harvest_run_ids,
                        original_updated_at = excluded.original_updated_at,
                        last_archived_at = CURRENT_TIMESTAMP,
                        rejection_count = rejection_count + 1
                    """,
                    (
                        record["original_record_id"],
                        record["canonical_key"],
                        record["doi"],
                        record["title"],
                        record["relevance_score"],
                        record["relevance_reason"],
                        record["relevance_theme"],
                        record["sources"],
                        record["harvest_run_ids"],
                        record["original_created_at"],
                        record["original_updated_at"],
                    ),
                )
                archived.append(record)
            missing = connection.execute(
                """
                SELECT COUNT(*)
                FROM bibliographic_records AS r
                WHERE r.relevance_status = 'rejected'
                  AND NOT EXISTS (
                      SELECT 1 FROM rejected_bibliographic_archive AS a
                      WHERE a.original_record_id = r.id
                        AND a.title = r.title
                        AND (r.doi IS NULL OR a.doi = r.doi COLLATE NOCASE)
                  )
                """
            ).fetchone()
            if int(missing[0] or 0):
                raise RuntimeError("rejected DOI/title archive verification failed")
        return archived

    def purge_archived_rejected_records(self, record_ids: list[str]) -> int:
        """Delete only explicitly archived rows that are still classified rejected."""

        unique_ids = list(dict.fromkeys(record_ids))
        if not unique_ids:
            return 0
        self.exclude_archived_rejected_dois(unique_ids)
        placeholders = ",".join("?" for _ in unique_ids)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"""
                DELETE FROM bibliographic_records
                WHERE id IN ({placeholders})
                  AND relevance_status = 'rejected'
                  AND EXISTS (
                      SELECT 1 FROM rejected_bibliographic_archive AS a
                      WHERE a.original_record_id = bibliographic_records.id
                        AND a.title = bibliographic_records.title
                        AND (
                            bibliographic_records.doi IS NULL
                            OR a.doi = bibliographic_records.doi COLLATE NOCASE
                        )
                  )
                """,
                unique_ids,
            )
            deleted = int(cursor.rowcount)
            remaining = connection.execute(
                f"""
                SELECT COUNT(*) FROM bibliographic_records
                WHERE id IN ({placeholders}) AND relevance_status = 'rejected'
                """,
                unique_ids,
            ).fetchone()
            if int(remaining[0] or 0):
                raise RuntimeError("some archived rejected records could not be deleted")
        return deleted

    def exclude_archived_rejected_dois(self, record_ids: list[str]) -> int:
        """Persist DOI exclusions before archived SQLite notices can be deleted."""

        unique_ids = list(dict.fromkeys(record_ids))
        if not unique_ids:
            return 0
        placeholders = ",".join("?" for _ in unique_ids)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT doi, title, relevance_reason AS reason,
                    last_archived_at AS excluded_at
                FROM rejected_bibliographic_archive
                WHERE original_record_id IN ({placeholders})
                """,
                unique_ids,
            )
            return self.doi_exclusions.exclude_many(
                {
                    **dict(row),
                    "origin": "automatic_relevance_rejection",
                }
                for row in rows
            )

    def archive_statistics(self) -> dict[str, int]:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM rejected_bibliographic_archive) AS archive_total,
                    (SELECT COUNT(*) FROM bibliographic_records
                     WHERE relevance_status = 'rejected') AS remaining_rejected_records
                """
            ).fetchone()
        return {
            "archive_total": int(row["archive_total"] or 0),
            "remaining_rejected_records": int(row["remaining_rejected_records"] or 0),
        }

    def browse_filter_options(self) -> dict[str, list[str]]:
        """Return only persisted values used by the documentary database filters."""

        with closing(self.database.connect()) as connection:
            themes = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT relevance_theme
                    FROM bibliographic_records
                    WHERE relevance_theme IS NOT NULL AND trim(relevance_theme) != ''
                    ORDER BY relevance_theme
                    """
                )
            ]
            sources = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT source
                    FROM bibliographic_record_sources
                    ORDER BY source
                    """
                )
            ]
        return {"themes": themes, "sources": sources}

    def browse_records(
        self,
        *,
        query: str = "",
        statuses: list[str] | None = None,
        theme: str | None = None,
        source: str | None = None,
        has_abstract: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Browse every harvested notice, including review and quarantined records."""

        if not 1 <= limit <= 200:
            raise ValueError("bibliographic browse limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("bibliographic browse offset cannot be negative")
        allowed_statuses = {"unreviewed", "accepted", "review", "rejected"}
        selected_statuses = list(dict.fromkeys(statuses or []))
        if not set(selected_statuses) <= allowed_statuses:
            raise ValueError("invalid bibliographic relevance status")

        clauses: list[str] = []
        parameters: list[Any] = []
        if query.strip():
            query_clause, query_parameters = _bibliographic_metadata_query(query)
            clauses.append(query_clause)
            parameters.extend(query_parameters)
        if selected_statuses:
            clauses.append(f"r.relevance_status IN ({','.join('?' for _ in selected_statuses)})")
            parameters.extend(selected_statuses)
        if theme:
            clauses.append("r.relevance_theme = ?")
            parameters.append(theme)
        if source:
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM bibliographic_record_sources AS selected_source
                    WHERE selected_source.record_id = r.id
                      AND selected_source.source = ?
                )
                """
            )
            parameters.append(source)
        if has_abstract is True:
            clauses.append("r.abstract IS NOT NULL AND trim(r.abstract) != ''")
        elif has_abstract is False:
            clauses.append("(r.abstract IS NULL OR trim(r.abstract) = '')")
        where_clause = " AND ".join(clauses) if clauses else "1 = 1"

        with closing(self.database.connect()) as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM bibliographic_records AS r WHERE {where_clause}",
                    tuple(parameters),
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT r.*,
                    (
                        SELECT GROUP_CONCAT(DISTINCT s.source)
                        FROM bibliographic_record_sources AS s
                        WHERE s.record_id = r.id
                    ) AS sources,
                    (
                        SELECT MIN(s.first_seen_at)
                        FROM bibliographic_record_sources AS s
                        WHERE s.record_id = r.id
                    ) AS first_seen_at,
                    (
                        SELECT MAX(s.last_seen_at)
                        FROM bibliographic_record_sources AS s
                        WHERE s.record_id = r.id
                    ) AS last_seen_at
                FROM bibliographic_records AS r
                WHERE {where_clause}
                ORDER BY
                    CASE r.relevance_status
                        WHEN 'accepted' THEN 0 WHEN 'review' THEN 1
                        WHEN 'unreviewed' THEN 2 ELSE 3 END,
                    r.relevance_score DESC,
                    r.publication_year DESC,
                    r.title COLLATE NOCASE,
                    r.id
                LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            )
            records = [dict(row) for row in rows]
        return {"total": total, "records": records, "limit": limit, "offset": offset}

    def review_record(self, record_id: str) -> dict[str, Any]:
        """Return one notice only when it is still waiting for a manual decision."""

        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT id, doi, title, abstract, relevance_status, embedding_status
                FROM bibliographic_records WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError("Notice bibliographique introuvable.")
        if row["relevance_status"] != "review":
            raise BibliographicReviewConflictError(
                "Cette notice n'est plus dans la file des articles à réviser."
            )
        return dict(row)

    def admit_review_record(self, record_id: str) -> dict[str, Any]:
        """Persist a manual admission so automated harvests cannot overwrite it."""

        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT id, title, abstract, relevance_status
                FROM bibliographic_records WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
            if row is None:
                raise FileNotFoundError("Notice bibliographique introuvable.")
            if row["relevance_status"] != "review":
                raise BibliographicReviewConflictError(
                    "Cette notice n'est plus dans la file des articles à réviser."
                )
            embedding_status = "pending" if row["abstract"] else "not_applicable"
            connection.execute(
                """
                UPDATE bibliographic_records
                SET relevance_status = 'accepted', embedding_status = ?,
                    manual_decision = 'accepted',
                    manual_reviewed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (embedding_status, record_id),
            )
        return {
            "record_id": record_id,
            "title": str(row["title"]),
            "decision": "accepted",
            "deleted": False,
            "vectors_deleted": 0,
        }

    def delete_review_record(self, record_id: str) -> dict[str, Any]:
        """Delete one explicitly targeted review notice and all SQLite dependants."""

        review_record = self.review_record(record_id)
        if not self.doi_exclusions.is_excluded(review_record.get("doi")):
            self.doi_exclusions.exclude(
                review_record.get("doi"),
                title=str(review_record["title"]),
                reason="Rejet manuel depuis la file de révision.",
                origin="manual_review",
            )
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT id, title, relevance_status
                FROM bibliographic_records WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
            if row is None:
                raise FileNotFoundError("Notice bibliographique introuvable.")
            if row["relevance_status"] != "review":
                raise BibliographicReviewConflictError(
                    "Cette notice n'est plus dans la file des articles à réviser."
                )
            cursor = connection.execute(
                "DELETE FROM bibliographic_records WHERE id = ? AND relevance_status = 'review'",
                (record_id,),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("La notice à réviser n'a pas pu être supprimée.")
            connection.execute(
                "DELETE FROM rejected_bibliographic_archive WHERE original_record_id = ?",
                (record_id,),
            )
        return {
            "record_id": record_id,
            "title": str(row["title"]),
            "decision": "rejected",
            "deleted": True,
        }

    def reclassify_existing(self) -> int:
        """Apply the current deterministic gate to every stored harvest hit."""

        changed = 0
        with self.database.transaction() as connection:
            hits = list(
                connection.execute(
                    """
                    SELECT h.rowid AS hit_rowid, h.theme, h.relevance_status,
                        h.relevance_score, h.relevance_reason, r.*, s.source
                    FROM bibliographic_harvest_hits AS h
                    JOIN bibliographic_records AS r ON r.id = h.record_id
                    LEFT JOIN bibliographic_record_sources AS s
                        ON s.record_id = r.id
                    GROUP BY h.rowid
                    """
                )
            )
            record_ids: set[str] = set()
            for row in hits:
                try:
                    authors = json.loads(row["authors"] or "[]")
                except json.JSONDecodeError:
                    authors = []
                assessment = assess_cider_relevance(
                    BibliographicRecord(
                        source=str(row["source"] or "local"),
                        source_id=str(row["id"]),
                        title=str(row["title"]),
                        abstract=(str(row["abstract"]) if row["abstract"] else None),
                        authors=[str(author) for author in authors],
                        journal=(str(row["journal"]) if row["journal"] else None),
                        publication_year=row["publication_year"],
                        doi=(str(row["doi"]) if row["doi"] else None),
                        citation_count=row["citation_count"],
                        url=(str(row["url"]) if row["url"] else None),
                    ),
                    str(row["theme"]),
                )
                if (
                    row["relevance_status"] != assessment.status
                    or row["relevance_score"] != assessment.score
                    or row["relevance_reason"] != assessment.reason
                ):
                    connection.execute(
                        """
                        UPDATE bibliographic_harvest_hits
                        SET relevance_status = ?, relevance_score = ?,
                            relevance_reason = ?
                        WHERE rowid = ?
                        """,
                        (
                            assessment.status,
                            assessment.score,
                            assessment.reason,
                            row["hit_rowid"],
                        ),
                    )
                    changed += 1
                record_ids.add(str(row["id"]))
            for record_id in record_ids:
                self._refresh_record_relevance(connection, record_id)
            self._refresh_run_acceptance_counts(connection)
        return changed

    def reject_abstractless_records(self) -> int:
        """Exclude records that remain unusable after abstract enrichment."""

        with self.database.transaction() as connection:
            record_ids = [
                str(row["id"])
                for row in connection.execute(
                    """
                    SELECT id
                    FROM bibliographic_records
                    WHERE (abstract IS NULL OR trim(abstract) = '')
                      AND manual_decision IS NULL
                    ORDER BY id
                    """
                )
            ]
            if not record_ids:
                return 0
            placeholders = ",".join("?" for _ in record_ids)
            connection.execute(
                f"""
                UPDATE bibliographic_harvest_hits
                SET relevance_status = 'rejected', relevance_score = 0.0,
                    relevance_reason = ?
                WHERE record_id IN ({placeholders})
                """,
                (MISSING_ABSTRACT_REASON, *record_ids),
            )
            connection.execute(
                f"""
                UPDATE bibliographic_records
                SET relevance_status = 'rejected', relevance_score = 0.0,
                    relevance_reason = ?, embedding_status = 'not_applicable',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                (MISSING_ABSTRACT_REASON, *record_ids),
            )
            self._refresh_run_acceptance_counts(connection)
        return len(record_ids)

    def reject_run_abstractless_records(self, run_id: str) -> int:
        """Reject only abstractless automatic hits from one collection run.

        This scoped variant lets a large resumable campaign clean up its own
        unusable notices without silently changing another run or a manual
        admission decision.
        """

        with self.database.transaction() as connection:
            record_ids = [
                str(row["id"])
                for row in connection.execute(
                    """
                    SELECT DISTINCT r.id
                    FROM bibliographic_records AS r
                    JOIN bibliographic_harvest_hits AS h ON h.record_id = r.id
                    WHERE h.run_id = ?
                      AND (r.abstract IS NULL OR trim(r.abstract) = '')
                      AND r.manual_decision IS NULL
                    ORDER BY r.id
                    """,
                    (run_id,),
                )
            ]
            if not record_ids:
                return 0
            placeholders = ",".join("?" for _ in record_ids)
            connection.execute(
                f"""
                UPDATE bibliographic_harvest_hits
                SET relevance_status = 'rejected', relevance_score = 0.0,
                    relevance_reason = ?
                WHERE run_id = ? AND record_id IN ({placeholders})
                """,
                (MISSING_ABSTRACT_REASON, run_id, *record_ids),
            )
            for record_id in record_ids:
                self._refresh_record_relevance(connection, record_id)
            self._refresh_run_acceptance_counts(connection)
        return len(record_ids)

    def review_run_doi_less_abstracts(self, run_id: str) -> int:
        """Keep DOI-less abstracts auditable but outside the accepted corpus."""

        with self.database.transaction() as connection:
            record_ids = [
                str(row["id"])
                for row in connection.execute(
                    """
                    SELECT DISTINCT r.id
                    FROM bibliographic_records AS r
                    JOIN bibliographic_harvest_hits AS h ON h.record_id = r.id
                    WHERE h.run_id = ?
                      AND h.relevance_status = 'accepted'
                      AND r.abstract IS NOT NULL AND trim(r.abstract) != ''
                      AND r.doi IS NULL
                      AND r.manual_decision IS NULL
                    ORDER BY r.id
                    """,
                    (run_id,),
                )
            ]
            if not record_ids:
                return 0
            placeholders = ",".join("?" for _ in record_ids)
            connection.execute(
                f"""
                UPDATE bibliographic_harvest_hits
                SET relevance_status = 'review',
                    relevance_reason = relevance_reason || '; ' || ?
                WHERE run_id = ? AND record_id IN ({placeholders})
                """,
                (MISSING_DOI_REASON, run_id, *record_ids),
            )
            for record_id in record_ids:
                self._refresh_record_relevance(connection, record_id)
            self._refresh_run_acceptance_counts(connection)
        return len(record_ids)

    def review_doi_less_abstracts(self) -> int:
        """Keep every automatic DOI-less abstract outside the accepted corpus."""

        with self.database.transaction() as connection:
            record_ids = [
                str(row["id"])
                for row in connection.execute(
                    """
                    SELECT DISTINCT r.id
                    FROM bibliographic_records AS r
                    JOIN bibliographic_harvest_hits AS h ON h.record_id = r.id
                    WHERE h.relevance_status = 'accepted'
                      AND r.abstract IS NOT NULL AND trim(r.abstract) != ''
                      AND r.doi IS NULL
                      AND r.manual_decision IS NULL
                    ORDER BY r.id
                    """
                )
            ]
            if not record_ids:
                return 0
            placeholders = ",".join("?" for _ in record_ids)
            connection.execute(
                f"""
                UPDATE bibliographic_harvest_hits
                SET relevance_status = 'review',
                    relevance_reason = relevance_reason || '; ' || ?
                WHERE relevance_status = 'accepted'
                  AND record_id IN ({placeholders})
                """,
                (MISSING_DOI_REASON, *record_ids),
            )
            for record_id in record_ids:
                self._refresh_record_relevance(connection, record_id)
            self._refresh_run_acceptance_counts(connection)
        return len(record_ids)

    def normalize_existing_text(self) -> int:
        """Decode harmless HTML entities and requeue changed abstract vectors."""

        changed = 0
        with self.database.transaction() as connection:
            rows = list(connection.execute("SELECT * FROM bibliographic_records"))
            for row in rows:
                current = dict(row)
                title = clean_text(current["title"]) or str(current["title"])
                abstract = (
                    clean_text(current["abstract"]) if current["abstract"] is not None else None
                )
                if title == current["title"] and abstract == current["abstract"]:
                    continue
                hash_values = {
                    "doi": current["doi"],
                    "title": title,
                    "abstract": abstract,
                    "authors": current["authors"],
                    "journal": current["journal"],
                    "publication_year": current["publication_year"],
                    "citation_count": current["citation_count"],
                    "url": current["url"],
                }
                connection.execute(
                    """
                    UPDATE bibliographic_records
                    SET title = ?, abstract = ?, content_hash = ?,
                        embedding_status = CASE
                            WHEN ? IS NULL OR relevance_status != 'accepted'
                            THEN 'not_applicable' ELSE 'pending' END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        title,
                        abstract,
                        _content_hash(hash_values),
                        abstract,
                        current["id"],
                    ),
                )
                changed += 1
        return changed

    def search(self, fts5_expression: str, *, limit: int = 20) -> list[Any]:
        if not fts5_expression.strip():
            return []
        if not 1 <= limit <= 200:
            raise ValueError("bibliographic search limit must be between 1 and 200")
        with closing(self.database.connect()) as connection:
            return list(
                connection.execute(
                    """
                    SELECT r.*, matched.lexical_score,
                        (
                            SELECT GROUP_CONCAT(DISTINCT s.source)
                            FROM bibliographic_record_sources AS s
                            WHERE s.record_id = r.id
                        ) AS sources
                    FROM (
                        SELECT record_id,
                            bm25(
                                bibliographic_records_fts,
                                0.0, 2.0, 1.0, 0.5, 0.5
                            ) AS lexical_score
                        FROM bibliographic_records_fts
                        WHERE bibliographic_records_fts MATCH ?
                    ) AS matched
                    JOIN bibliographic_records AS r ON r.id = matched.record_id
                    WHERE r.abstract IS NOT NULL
                      AND r.relevance_status = 'accepted'
                    ORDER BY matched.lexical_score, r.id
                    LIMIT ?
                    """,
                    (fts5_expression, limit),
                )
            )

    def search_metadata(self, query: str, *, limit: int = 20) -> list[Any]:
        """Find accepted abstracts through DOI, author, year, theme or provider metadata."""

        if not query.strip():
            return []
        if not 1 <= limit <= 200:
            raise ValueError("bibliographic metadata search limit must be between 1 and 200")
        predicate, parameters = _bibliographic_metadata_query(query)
        with closing(self.database.connect()) as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT r.*
                    FROM bibliographic_records AS r
                    WHERE r.abstract IS NOT NULL
                      AND r.relevance_status = 'accepted'
                      AND {predicate}
                    ORDER BY r.citation_count DESC, r.publication_year DESC,
                        r.title COLLATE NOCASE, r.id
                    LIMIT ?
                    """,
                    (*parameters, limit),
                )
            )

    def pending_abstracts(
        self,
        *,
        limit: int = 1000,
        retry_failed: bool = True,
    ) -> list[Any]:
        """Return only scientifically eligible abstracts awaiting vectorization."""

        statuses = {"pending"}
        if retry_failed:
            statuses.add("failed")
        rows = self._eligible_abstract_rows()
        return [row for row in rows if str(row["embedding_status"]) in statuses][:limit]

    def reset_abstract_embedding_statuses(self) -> int:
        """Requeue only eligible abstract-only records for an explicit rebuild."""

        eligible_ids = self.eligible_record_ids()
        return self._set_embedding_status(eligible_ids, "pending", exclude_current=True)

    def update_embedding_status(self, record_ids: list[str], status: str) -> None:
        if not record_ids:
            return
        if status not in {"not_applicable", "pending", "indexed", "failed"}:
            raise ValueError("unsupported bibliographic embedding status")
        self._set_embedding_status(record_ids, status)

    def records_by_ids(self, record_ids: list[str]) -> dict[str, Any]:
        unique_ids = list(dict.fromkeys(record_ids))
        if not unique_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_ids)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT r.*, GROUP_CONCAT(DISTINCT s.source) AS sources
                FROM bibliographic_records AS r
                LEFT JOIN bibliographic_record_sources AS s
                    ON s.record_id = r.id
                WHERE r.id IN ({placeholders})
                  AND r.relevance_status = 'accepted'
                GROUP BY r.id
                """,
                unique_ids,
            )
            return {str(row["id"]): row for row in rows}

    def ineligible_record_ids(self) -> list[str]:
        eligible = set(self.eligible_record_ids())
        with closing(self.database.connect()) as connection:
            return [
                str(row["id"])
                for row in connection.execute("SELECT id FROM bibliographic_records")
                if str(row["id"]) not in eligible
            ]

    def eligible_record_ids(self) -> list[str]:
        return [str(row["id"]) for row in self._eligible_abstract_rows()]

    def eligible_abstract_embedding_statuses(self) -> dict[str, str]:
        """Return the vector lifecycle state of each eligible abstract-only record."""

        return {
            str(row["id"]): str(row["embedding_status"]) for row in self._eligible_abstract_rows()
        }

    def synchronize_abstract_index_eligibility(self) -> tuple[int, int]:
        """Make vector statuses reflect DOI-valid abstract-only eligibility.

        A valid abstract is not eligible once a full article with the same
        normalized DOI exists.  Keeping that state in SQLite makes the
        Qdrant collection reconstructible and prevents stale points from
        being treated as usable evidence after a later full-text import.
        """

        eligible_ids = self.eligible_record_ids()
        with closing(self.database.connect()) as connection:
            all_ids = [
                str(row["id"]) for row in connection.execute("SELECT id FROM bibliographic_records")
            ]
        ineligible_ids = sorted(set(all_ids) - set(eligible_ids))
        marked_not_applicable = self._set_embedding_status(
            ineligible_ids,
            "not_applicable",
            exclude_current=True,
        )
        requeued = self._set_embedding_status(
            eligible_ids,
            "pending",
            only_current="not_applicable",
        )
        return marked_not_applicable, requeued

    def _eligible_abstract_rows(self) -> list[Any]:
        """Apply the DOI/full-text eligibility contract from authoritative SQLite."""

        with closing(self.database.connect()) as connection:
            records = list(
                connection.execute(
                    """
                    SELECT id, title, abstract, doi, content_hash, embedding_status, updated_at
                    FROM bibliographic_records
                    WHERE relevance_status = 'accepted'
                      AND abstract IS NOT NULL
                      AND trim(abstract) != ''
                    ORDER BY updated_at, id
                    """
                )
            )
            article_dois = {
                doi
                for row in connection.execute("SELECT doi FROM articles")
                if (doi := _verified_normalized_doi(row["doi"])) is not None
            }
        return [
            row
            for row in records
            if (doi := _verified_normalized_doi(row["doi"])) is not None and doi not in article_dois
        ]

    def _set_embedding_status(
        self,
        record_ids: list[str],
        status: str,
        *,
        exclude_current: bool = False,
        only_current: str | None = None,
    ) -> int:
        if not record_ids:
            return 0
        # SQLite builds with the common 999-variable limit cannot update a
        # mature bibliographic corpus in one IN clause.  This is also used by
        # the incremental indexer while a harvest is adding records.
        batch_size = 900
        updated = 0
        with closing(self.database.connect()) as connection, connection:
            for offset in range(0, len(record_ids), batch_size):
                record_id_batch = record_ids[offset : offset + batch_size]
                placeholders = ",".join("?" for _ in record_id_batch)
                predicate = ""
                parameters: list[object] = [status, *record_id_batch]
                if exclude_current:
                    predicate = " AND embedding_status != ?"
                    parameters.append(status)
                if only_current is not None:
                    predicate = " AND embedding_status = ?"
                    parameters.append(only_current)
                cursor = connection.execute(
                    "UPDATE bibliographic_records SET embedding_status = ?, "
                    "updated_at = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({placeholders}){predicate}",
                    parameters,
                )
                updated += int(cursor.rowcount)
        return updated

    def update_embedding_status_if_unchanged(
        self,
        record_content_hashes: dict[str, str],
        status: str,
    ) -> int:
        """Advance only records whose embedded content is still current.

        Harvesting and vectorization may run concurrently.  A vector produced
        from an older abstract must never mark a subsequently enriched record
        as indexed (or failed): the later content remains pending for the
        next incremental pass instead.
        """

        if not record_content_hashes:
            return 0
        if status not in {"indexed", "failed"}:
            raise ValueError("conditional bibliographic status must be indexed or failed")
        updated = 0
        with closing(self.database.connect()) as connection, connection:
            for record_id, content_hash in record_content_hashes.items():
                cursor = connection.execute(
                    """
                    UPDATE bibliographic_records
                    SET embedding_status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND content_hash = ?
                      AND relevance_status = 'accepted'
                      AND abstract IS NOT NULL AND trim(abstract) != ''
                    """,
                    (status, record_id, content_hash),
                )
                updated += int(cursor.rowcount)
        return updated


def _verified_normalized_doi(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized if normalize_doi(normalized) == normalized else None


class CiderPilotHarvester:
    """Collect a small weekly cider corpus with fail-closed OpenAlex spending."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        *,
        query_waves: tuple[dict[str, str], ...] = CIDER_QUERY_WAVES,
        start_page: int = 0,
    ) -> None:
        if not query_waves or any(not wave for wave in query_waves):
            raise ValueError("at least one non-empty query wave is required")
        if not 0 <= start_page <= 100:
            raise ValueError("harvest start page must be between 0 and 100")
        self.settings = settings
        self.database = database
        self.store = BibliographicHarvestStore(database)
        self.query_waves = query_waves
        self.start_page = start_page

    def run(self, *, force: bool = False) -> BibliographicHarvestReport:
        if not self.settings.harvest.enabled:
            raise RuntimeError("bibliographic harvesting is disabled")
        if not force and not self.store.is_due(self.settings):
            raise HarvestNotDue("the weekly cider harvest is not due yet")
        completed_runs = self.store.completed_run_count(self.settings.harvest.profile)
        query_wave = completed_runs % len(self.query_waves)
        result_page = self.start_page + completed_runs // len(self.query_waves)
        result_offset = result_page * self.settings.harvest.per_source_limit
        themes = self.query_waves[query_wave]
        sources = list(self.settings.bibliographic.sources)
        active = self.settings.model_copy(deep=True)
        active.bibliographic.request_delay_seconds = self.settings.harvest.request_delay_seconds
        run_id, started_at = self.store.start_run(self.settings, themes=themes, sources=sources)
        raw_count = 0
        errors: list[dict[str, str]] = []
        summaries: list[HarvestSourceSummary] = []
        remaining_before: float | None = None
        remaining_after: float | None = None
        state: Literal["completed", "partial", "failed"] = "completed"
        try:
            for source in sources:
                query_count = 0
                source_records = 0
                source_abstracts = 0
                client_type = CLIENTS[source]
                with client_type(active) as client:
                    if source == "openalex":
                        if not isinstance(client, OpenAlexClient):
                            raise TypeError("OpenAlex client registry is inconsistent")
                        budget = client.rate_limit_status()
                        remaining_before = _float_value(budget.get("daily_remaining_usd"))
                        projected = len(themes) * OPENALEX_SEARCH_COST_USD
                        if projected > self.settings.harvest.openalex_max_cost_usd_per_run:
                            raise RuntimeError(
                                "projected OpenAlex cost exceeds the configured run cap"
                            )
                        if remaining_before is None or remaining_before < projected:
                            raise RuntimeError(
                                "OpenAlex free daily budget is insufficient for this run"
                            )
                    for theme, query in themes.items():
                        if raw_count >= self.settings.harvest.max_records_per_run:
                            break
                        try:
                            if result_offset:
                                records = client.search(
                                    query,
                                    self.settings.harvest.per_source_limit,
                                    offset=result_offset,
                                )
                            else:
                                records = client.search(
                                    query,
                                    self.settings.harvest.per_source_limit,
                                )
                            query_count += 1
                            allowed = min(
                                len(records),
                                self.settings.harvest.max_records_per_run - raw_count,
                            )
                            for rank, record in enumerate(records[:allowed], start=1):
                                self.store.upsert_hit(
                                    run_id=run_id,
                                    theme=theme,
                                    rank=rank,
                                    record=record,
                                )
                            raw_count += allowed
                            source_records += allowed
                            source_abstracts += sum(
                                bool(record.abstract) for record in records[:allowed]
                            )
                        except Exception as exc:
                            errors.append(
                                {
                                    "source": source,
                                    "theme": theme,
                                    "error_type": type(exc).__name__,
                                    "message": str(exc)[:500],
                                }
                            )
                            if isinstance(exc, BibliographicApiDeferred):
                                break
                    if source == "openalex" and isinstance(client, OpenAlexClient):
                        budget = client.rate_limit_status()
                        remaining_after = _float_value(budget.get("daily_remaining_usd"))
                summaries.append(
                    HarvestSourceSummary(
                        source=source,
                        queries_completed=query_count,
                        records_received=source_records,
                        abstracts_received=source_abstracts,
                    )
                )
            state = "partial" if errors else "completed"
        except Exception as exc:
            state = "failed"
            errors.append(
                {
                    "source": "harvester",
                    "theme": "all",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                }
            )
        completed_at = datetime.now(UTC)
        (
            unique_count,
            abstract_count,
            accepted_count,
            accepted_abstract_count,
        ) = self.store.finish_run(
            run_id=run_id,
            state=state,
            raw_record_count=raw_count,
            errors=errors,
            completed_at=completed_at,
        )
        if self.store.review_run_doi_less_abstracts(run_id):
            (
                unique_count,
                abstract_count,
                accepted_count,
                accepted_abstract_count,
            ) = self.store.finish_run(
                run_id=run_id,
                state=state,
                raw_record_count=raw_count,
                errors=errors,
                completed_at=completed_at,
            )
        return BibliographicHarvestReport(
            run_id=run_id,
            state=state,
            profile=self.settings.harvest.profile,
            themes=list(themes),
            sources=sources,
            per_source_limit=self.settings.harvest.per_source_limit,
            raw_record_count=raw_count,
            unique_record_count=unique_count,
            abstract_record_count=abstract_count,
            accepted_record_count=accepted_count,
            accepted_abstract_count=accepted_abstract_count,
            source_summaries=summaries,
            errors=errors,
            query_wave=query_wave,
            result_offset=result_offset,
            openalex_daily_remaining_before_usd=remaining_before,
            openalex_daily_remaining_after_usd=remaining_after,
            started_at=started_at,
            completed_at=completed_at,
        )


class CiderAbstractBackfiller:
    """Enrich accepted DOI records with OpenAlex abstracts in one batch."""

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.store = BibliographicHarvestStore(database)

    def run(self, *, limit: int = 100) -> AbstractBackfillReport:
        profile = f"{self.settings.harvest.profile}_abstract_backfill"
        candidates = self.store.missing_abstract_candidates(
            profile=profile,
            limit=limit,
        )
        if not candidates:
            return AbstractBackfillReport(
                run_id=None,
                state="skipped",
                candidates=0,
                matched_records=0,
                abstracts_added=0,
                errors=[],
            )
        active = self.settings.model_copy(deep=True)
        active.bibliographic.request_delay_seconds = self.settings.harvest.request_delay_seconds
        active.harvest.profile = profile
        run_id, _ = self.store.start_run(
            active,
            themes={"doi_enrichment": "accepted records missing abstracts"},
            sources=["openalex"],
        )
        by_doi = {str(row["doi"]).strip().lower(): row for row in candidates}
        errors: list[dict[str, str]] = []
        matched = 0
        abstracts_added = 0
        remaining_before: float | None = None
        remaining_after: float | None = None
        state: Literal["completed", "failed"] = "completed"
        records: list[BibliographicRecord] = []
        matched_dois: set[str] = set()
        try:
            with OpenAlexClient(active) as client:
                budget = client.rate_limit_status()
                remaining_before = _float_value(budget.get("daily_remaining_usd"))
                endpoint_costs = budget.get("endpoint_costs_usd")
                projected = 0.0001
                if isinstance(endpoint_costs, dict):
                    projected = _float_value(endpoint_costs.get("list")) or projected
                if self.settings.harvest.openalex_free_only and (
                    remaining_before is None or remaining_before < projected
                ):
                    raise RuntimeError(
                        "OpenAlex free daily budget is insufficient for DOI backfill"
                    )
                records = client.lookup_dois(list(by_doi))
                for rank, record in enumerate(records, start=1):
                    if not record.doi or record.doi not in by_doi:
                        continue
                    candidate = by_doi[record.doi]
                    theme = str(candidate["relevance_theme"] or "aromes_procede")
                    self.store.upsert_hit(
                        run_id=run_id,
                        theme=theme,
                        rank=rank,
                        record=record,
                    )
                    matched_dois.add(record.doi)
                    matched += 1
                    abstracts_added += bool(record.abstract)
                for rank, (doi, candidate) in enumerate(by_doi.items(), start=1):
                    if doi in matched_dois:
                        continue
                    self.store.record_backfill_attempt(
                        run_id=run_id,
                        record_id=str(candidate["id"]),
                        theme=str(candidate["relevance_theme"] or "aromes_procede"),
                        rank=rank,
                        source="OpenAlex DOI lookup",
                    )
                remaining_after = _float_value(
                    client.rate_limit_status().get("daily_remaining_usd")
                )
        except Exception as exc:
            state = "failed"
            errors.append(
                {
                    "source": "openalex",
                    "theme": "doi_enrichment",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                }
            )
        completed_at = datetime.now(UTC)
        self.store.finish_run(
            run_id=run_id,
            state=state,
            raw_record_count=len(records),
            errors=errors,
            completed_at=completed_at,
        )
        return AbstractBackfillReport(
            run_id=run_id,
            state=state,
            candidates=len(candidates),
            matched_records=matched,
            abstracts_added=abstracts_added,
            errors=errors,
            openalex_daily_remaining_before_usd=remaining_before,
            openalex_daily_remaining_after_usd=remaining_after,
        )


class CiderBulkHarvester:
    """Run a resumable, bounded sequence of topical harvest pages."""

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.store = BibliographicHarvestStore(database)

    def run(
        self,
        *,
        target_new_accepted_abstracts: int = 1000,
        page_size: int = 50,
        max_runs: int = 20,
        profile: str = "cider_design_bulk",
        query_waves: tuple[dict[str, str], ...] = CIDER_BULK_QUERY_WAVES,
        sources: tuple[str, ...] | None = None,
        start_page: int = 0,
        progress: Callable[[str], None] | None = None,
    ) -> BulkHarvestReport:
        if not 1 <= target_new_accepted_abstracts <= 10000:
            raise ValueError("bulk target must be between 1 and 10000")
        if not 1 <= page_size <= 50:
            raise ValueError("bulk page size must be between 1 and 50")
        if not 1 <= max_runs <= 100:
            raise ValueError("bulk max runs must be between 1 and 100")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", profile):
            raise ValueError("bulk profile name is invalid")
        if not query_waves or any(set(wave) != set(CIDER_PILOT_THEMES) for wave in query_waves):
            raise ValueError("bulk query waves must cover every cider theme")
        if sources is not None and (not sources or set(sources) - set(CLIENTS)):
            raise ValueError("bulk sources must be known bibliographic providers")
        if not 0 <= start_page <= 100:
            raise ValueError("bulk start page must be between 0 and 100")

        active = self.settings.model_copy(deep=True)
        if sources is not None:
            active.bibliographic.sources = list(dict.fromkeys(sources))
        active.harvest.profile = profile
        active.harvest.per_source_limit = page_size
        active.harvest.max_records_per_run = min(
            5000,
            page_size * len(active.bibliographic.sources) * len(CIDER_PILOT_THEMES),
        )
        baseline = self.store.statistics()["abstracts"]
        harvest_runs: list[BibliographicHarvestReport] = []
        backfill_runs: list[AbstractBackfillReport] = []
        no_progress_runs = 0
        empty_error_runs = 0
        backfill_no_progress_runs = 0
        backfill_enabled = True
        stop_reason: Literal["target_reached", "max_runs", "no_progress"] = "max_runs"

        for run_number in range(1, max_runs + 1):
            before = self.store.statistics()["abstracts"]
            harvest = CiderPilotHarvester(
                active,
                self.database,
                query_waves=query_waves,
                start_page=start_page,
            ).run(force=True)
            harvest_runs.append(harvest)
            after_harvest = self.store.statistics()["abstracts"]
            if progress is not None:
                progress(
                    f"run={run_number}/{max_runs} wave={harvest.query_wave} "
                    f"offset={harvest.result_offset} raw={harvest.raw_record_count} "
                    f"accepted_abstracts={after_harvest} "
                    f"new={after_harvest - baseline} state={harvest.state}"
                )

            if harvest.raw_record_count == 0 and harvest.errors:
                empty_error_runs += 1
            else:
                empty_error_runs = 0
            if empty_error_runs >= 2:
                stop_reason = "no_progress"
                break

            if (
                backfill_enabled
                and harvest.raw_record_count > 0
                and after_harvest - baseline < target_new_accepted_abstracts
            ):
                backfill = CiderAbstractBackfiller(active, self.database).run(limit=100)
                backfill_runs.append(backfill)
                if backfill.abstracts_added:
                    backfill_no_progress_runs = 0
                elif backfill.state != "skipped":
                    backfill_no_progress_runs += 1
                    if backfill_no_progress_runs >= 2:
                        backfill_enabled = False
                if progress is not None:
                    progress(
                        f"backfill={backfill.state} candidates={backfill.candidates} "
                        f"abstracts_added={backfill.abstracts_added} "
                        f"enabled={backfill_enabled}"
                    )

            current = self.store.statistics()["abstracts"]
            if current - baseline >= target_new_accepted_abstracts:
                stop_reason = "target_reached"
                break
            if current <= before:
                no_progress_runs += 1
            else:
                no_progress_runs = 0
            if no_progress_runs >= len(query_waves) * 2:
                stop_reason = "no_progress"
                break

        final = self.store.statistics()["abstracts"]
        new_abstracts = max(0, final - baseline)
        return BulkHarvestReport(
            profile=profile,
            target_new_accepted_abstracts=target_new_accepted_abstracts,
            baseline_accepted_abstracts=baseline,
            final_accepted_abstracts=final,
            new_accepted_abstracts=new_abstracts,
            target_reached=new_abstracts >= target_new_accepted_abstracts,
            stop_reason=stop_reason,
            harvest_runs=harvest_runs,
            backfill_runs=backfill_runs,
        )


def _float_value(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
