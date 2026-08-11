"""Conservative, deterministic checks for numerical scientific claims.

This module is deliberately an audit primitive, not a scientific natural-language
inference engine.  It accepts only structured matches that can be tied to one
supplied evidence item without unit conversion or invented precision.  Anything
it cannot parse or disambiguate is reported as ambiguous rather than supported.

The public report intentionally contains only source identifiers, structured
quantity signatures, and reason codes.  It never includes source text, which
keeps it safe to use in validation errors and telemetry.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class NumericVerdict(StrEnum):
    """Outcome of a deterministic numerical comparison."""

    NOT_APPLICABLE = "not_applicable"
    SUPPORTED = "supported"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


class NumericIssueCode(StrEnum):
    """Stable, text-free diagnostic codes for an audit result."""

    VALUE_MISSING = "value_missing"
    UNIT_MISSING = "unit_missing"
    UNIT_MISMATCH = "unit_mismatch"
    UNSUPPORTED_CONVERSION = "unsupported_conversion"
    OPERATOR_MISMATCH = "operator_mismatch"
    RANGE_MISMATCH = "range_mismatch"
    UNCERTAINTY_MISMATCH = "uncertainty_mismatch"
    PRECISION_MISMATCH = "precision_mismatch"
    SIGN_MISMATCH = "sign_mismatch"
    DIRECTION_MISMATCH = "direction_mismatch"
    DIRECTION_MISSING = "direction_missing"
    CONTEXT_MISMATCH = "context_mismatch"
    CONTEXT_AMBIGUOUS = "context_ambiguous"
    AMBIGUOUS_CANDIDATE = "ambiguous_candidate"
    UNPARSED_NUMERIC = "unparsed_numeric"


class NumericOperator(StrEnum):
    """Canonical relation conveyed by a quantity."""

    EXACT = "exact"
    LESS_THAN = "less_than"
    LESS_OR_EQUAL = "less_or_equal"
    GREATER_THAN = "greater_than"
    GREATER_OR_EQUAL = "greater_or_equal"
    APPROXIMATE = "approximate"
    RANGE = "range"


class NumericDirection(StrEnum):
    """Explicit trend close to a quantitative expression."""

    NONE = "none"
    INCREASE = "increase"
    DECREASE = "decrease"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class NumericQuantity:
    """A parsed quantity without carrying the surrounding source text."""

    value: str
    precision: int
    unit: str | None
    operator: NumericOperator
    upper_value: str | None = None
    upper_precision: int | None = None
    uncertainty: str | None = None
    uncertainty_precision: int | None = None
    direction: NumericDirection = NumericDirection.NONE


@dataclass(frozen=True, slots=True)
class _ParsedQuantity:
    """Internal parsing state; never exposed through an audit report."""

    quantity: NumericQuantity
    context_terms: tuple[str, ...]
    language: str


@dataclass(frozen=True, slots=True)
class NumericQuantityAssessment:
    """One claim quantity and its deterministic source match, if any."""

    quantity: NumericQuantity
    verdict: NumericVerdict
    source_id: str | None
    issues: tuple[NumericIssueCode, ...] = ()


@dataclass(frozen=True, slots=True)
class NumericVerificationReport:
    """A content-safe report suitable for audit logs and policy decisions."""

    verdict: NumericVerdict
    assessments: tuple[NumericQuantityAssessment, ...]
    unparsed_numeric_count: int = 0
    issues: tuple[NumericIssueCode, ...] = ()


@dataclass(frozen=True, slots=True)
class NumericVerificationPolicy:
    """Policy switches that keep the default verifier intentionally strict."""

    require_context: bool = True
    allow_unique_contextless_match: bool = True


DEFAULT_NUMERIC_VERIFICATION_POLICY = NumericVerificationPolicy()


_NUMBER = (
    r"[+-]?(?:(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+)|(?:\d+(?:[.,]\d+)?|[.,]\d+))(?:[eE][+-]?\d+)?"
)
_NUMBER_RE = re.compile(_NUMBER)
_PH_RE = re.compile(
    rf"\bph\s*(?:(?:was|is|est|of|de)\s*)?=?\s*(?P<value>{_NUMBER})"
    rf"(?:\s*(?:±|\+/-)\s*(?P<uncertainty>{_NUMBER}))?",
    re.IGNORECASE,
)
# Kept as a literal pattern rather than a generic word matcher.  A generic matcher
# would eagerly consume analyte names or conditions after a bare number.
_UNIT_PATTERN = r"""(?:
    percentage\s+points?|points?\s+de\s+pourcentage|
    %|percent(?:age)?s?|pour\s*cent|
    °\s*[cf]|degrees?\s+celsius|degr[eé]s?\s+celsius|celsius|
    (?:[munpkµμ]?g)\s*(?:/|per\s+|\s+)l(?:\s*(?:[-−]\s*1|\^\s*[-−]?1))?|
    cfu\s*(?:/|per\s+|\s+)(?:ml|g)|
    (?:mmol|µmol|μmol|umol|mol|mm|mmol)\s*(?:/|per\s+|\s+)l(?:\s*(?:[-−]\s*1|\^\s*[-−]?1))?|
    (?:mg|µg|μg|ug|ng|g|kg|ml|l|mmol|µmol|μmol|umol|mol|mm|ppm|ppb|pa|kpa|bar|atm|rpm)|
    (?:milliseconds?|seconds?|minutes?|hours?|days?|weeks?|months?|years?|ms|min|h|d|s|secondes?|minutes?|heures?|jours?|semaines?|mois|ans)
)"""
_UNIT_RE = re.compile(_UNIT_PATTERN, re.IGNORECASE | re.VERBOSE)
_UNCERTAINTY_RE = re.compile(
    rf"(?P<value>{_NUMBER})\s*(?:±|\+/-)\s*(?P<uncertainty>{_NUMBER})"
    rf"\s*(?P<unit>{_UNIT_PATTERN})",
    re.IGNORECASE | re.VERBOSE,
)
_RANGE_RE = re.compile(
    rf"(?:(?:between|entre|from|de)\s+(?P<led_value>{_NUMBER})\s*(?:to|and|et|a|à)\s*"
    rf"(?P<led_upper>{_NUMBER})|(?P<dashed_value>{_NUMBER})\s*[-–—]\s*"
    rf"(?P<dashed_upper>{_NUMBER}))\s*(?P<unit>{_UNIT_PATTERN})",
    re.IGNORECASE | re.VERBOSE,
)
_BRACKET_RANGE_RE = re.compile(
    rf"[\[(]\s*(?P<value>{_NUMBER})\s*[,;]\s*(?P<upper>{_NUMBER})\s*[\])]"
    rf"\s*(?P<unit>{_UNIT_PATTERN})",
    re.IGNORECASE | re.VERBOSE,
)
_QUANTITY_RE = re.compile(
    rf"(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT_PATTERN})",
    re.IGNORECASE | re.VERBOSE,
)

_OPERATOR_PATTERNS: tuple[tuple[re.Pattern[str], NumericOperator], ...] = (
    (
        re.compile(r"(?:<=|≤|at\s+most|no\s+more\s+than|au\s+plus)\s*$", re.I),
        NumericOperator.LESS_OR_EQUAL,
    ),
    (
        re.compile(r"(?:>=|≥|at\s+least|no\s+less\s+than|au\s+moins)\s*$", re.I),
        NumericOperator.GREATER_OR_EQUAL,
    ),
    (
        re.compile(r"(?:<|less\s+than|below|under|moins\s+de|inf[eé]rieur(?:e)?\s+[àa])\s*$", re.I),
        NumericOperator.LESS_THAN,
    ),
    (
        re.compile(
            r"(?:>|greater\s+than|above|over|plus\s+de|sup[eé]rieur(?:e)?\s+[àa])\s*$", re.I
        ),
        NumericOperator.GREATER_THAN,
    ),
    (
        re.compile(r"(?:≈|~|about|around|approximately|environ|approximativement)\s*$", re.I),
        NumericOperator.APPROXIMATE,
    ),
)

_INCREASE_RE = re.compile(
    r"\b(?:increase(?:d|s)?|increas(?:e|ing)|higher|rise|rose|growth|"
    r"augment(?:e|ed|ation|er|ée|é)|hausse|accr(?:oit|u|ue|oissement)|plus\s+[ée]lev[ée])\b",
    re.IGNORECASE,
)
_DECREASE_RE = re.compile(
    r"\b(?:decrease(?:d|s)?|decreas(?:e|ing)|lower|declin(?:e|ed|ing)|reduc(?:e|ed|tion)|"
    r"dimin(?:ue|ution|ished)|baisse|diminu(?:e|é|ée|tion)|r[eé]duc(?:tion|t|te)|moins\s+[ée]lev[ée])\b",
    re.IGNORECASE,
)

_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_FRENCH_MARKERS = frozenset(
    {
        "avec",
        "dans",
        "de",
        "des",
        "du",
        "et",
        "etait",
        "est",
        "la",
        "le",
        "les",
        "pour",
        "apres",
        "une",
    }
)
_ENGLISH_MARKERS = frozenset({"after", "and", "the", "with", "was", "were", "from", "than", "for"})
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "after",
        "as",
        "at",
        "avec",
        "by",
        "de",
        "des",
        "du",
        "en",
        "et",
        "for",
        "from",
        "in",
        "la",
        "le",
        "les",
        "of",
        "on",
        "or",
        "par",
        "pendant",
        "pour",
        "the",
        "to",
        "un",
        "une",
        "was",
        "were",
        "with",
        "before",
        "during",
        "est",
        "etait",
        "etaient",
    }
)
_GENERIC_CONTEXT_TERMS = frozenset(
    {
        "amount",
        "concentration",
        "content",
        "concentrations",
        "experimental",
        "experiment",
        "experimentation",
        "day",
        "days",
        "level",
        "l",
        "mg",
        "ml",
        "niveau",
        "observed",
        "observation",
        "observe",
        "quantity",
        "quantite",
        "sample",
        "samples",
        "study",
        "teneur",
        "test",
        "trial",
        "ug",
        "value",
        "valeur",
    }
)
_LOCATOR_PREFIX_RE = re.compile(
    r"(?:figure|fig|table|tab|page|pages|p|pp|section|sec|chapter|chap|doi|pmid|chunk)\s*$",
    re.IGNORECASE,
)
_MASS_PER_VOLUME_UNITS = frozenset({"ng/l", "ug/l", "mg/l", "g/l", "kg/l"})


def _ascii_fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return normalized.encode("ascii", "ignore").decode("ascii")


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return (
        normalized.replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("μ", "µ")
        .replace("·", " ")
    )


def _canonical_number(value: str) -> tuple[str, int]:
    """Return a stable number key and decimal precision without float conversion."""

    compact = value.replace("\u00a0", " ").replace("\u202f", " ").strip()
    sign = ""
    if compact.startswith(("+", "-")):
        sign, compact = compact[0], compact[1:]
    compact = compact.replace(" ", "")
    if compact.lower().count("e") == 1:
        mantissa, exponent = re.split(r"[eE]", compact, maxsplit=1)
        key, precision = _canonical_number(mantissa)
        return f"{sign}{key}e{exponent}", precision
    separator_count = compact.count(".") + compact.count(",")
    if separator_count == 0:
        return f"{sign}{compact}", 0
    if compact.count(".") and compact.count(","):
        decimal_separator = "." if compact.rfind(".") > compact.rfind(",") else ","
        grouping_separator = "," if decimal_separator == "." else "."
        whole, fraction = compact.rsplit(decimal_separator, maxsplit=1)
        return f"{sign}{whole.replace(grouping_separator, '')}.{fraction}", len(fraction)
    separator = "." if "." in compact else ","
    whole, fraction = compact.rsplit(separator, maxsplit=1)
    # A punctuation-only group of three digits is locale ambiguous (1,000 vs 1.000).
    # Preserve its lexical form instead of guessing its mathematical value.
    if len(fraction) == 3 and whole and len(whole) <= 3:
        return f"{sign}{whole}{separator}{fraction}", len(fraction)
    return f"{sign}{whole}.{fraction}", len(fraction)


def _as_decimal(value: str) -> Decimal | None:
    """Best-effort Decimal used only for sign diagnostics, never for conversion."""

    key, _ = _canonical_number(value)
    if "e" in key:
        try:
            return Decimal(key)
        except InvalidOperation:
            return None
    if key.count(".") > 1:
        return None
    try:
        return Decimal(key)
    except InvalidOperation:
        return None


def _canonical_unit(value: str) -> str:
    folded = _ascii_fold(_normalize_text(value).replace("µ", "u"))
    compact = re.sub(r"\s+", "", folded).replace("per", "/").replace("-1", "^-1")
    compact = compact.replace("l^-1", "/l").replace("/l", "/l")
    if compact in {"%", "percent", "percents", "percentage", "percentages", "pourcent"}:
        return "%"
    if compact in {
        "percentagepoint",
        "percentagepoints",
        "pointdepourcentage",
        "pointsdepourcentage",
    }:
        return "percentage_point"
    if compact in {"°c", "degreescelsius", "degrecelsius", "celsius"}:
        return "°c"
    if compact in {"second", "seconds", "seconde", "secondes", "s"}:
        return "second"
    if compact in {"minute", "minutes", "min"}:
        return "minute"
    if compact in {"hour", "hours", "heure", "heures", "h"}:
        return "hour"
    if compact in {"day", "days", "jour", "jours", "d"}:
        return "day"
    if compact in {"week", "weeks", "semaine", "semaines"}:
        return "week"
    if compact in {"month", "months", "mois"}:
        return "month"
    if compact in {"year", "years", "an", "ans"}:
        return "year"
    compact = compact.replace("l^-1", "/l")
    return compact


def _unit_dimension(unit: str | None) -> str | None:
    if unit in _MASS_PER_VOLUME_UNITS:
        return "mass_per_volume"
    if unit in {"%", "percentage_point"}:
        return "percentage"
    if unit in {"second", "minute", "hour", "day", "week", "month", "year"}:
        return "time"
    return unit


def _operator_before(sentence: str, start: int) -> NumericOperator:
    prefix = sentence[max(0, start - 48) : start]
    for pattern, operator in _OPERATOR_PATTERNS:
        if pattern.search(prefix):
            return operator
    return NumericOperator.EXACT


def _direction_near(sentence: str, start: int, end: int) -> NumericDirection:
    window_start = max(0, start - 72)
    window_end = min(len(sentence), end + 72)
    window = sentence[window_start:window_end]
    distances: list[tuple[int, NumericDirection]] = []
    for pattern, direction in (
        (_INCREASE_RE, NumericDirection.INCREASE),
        (_DECREASE_RE, NumericDirection.DECREASE),
    ):
        for match in pattern.finditer(window):
            distance = min(
                abs(start - (window_start + match.start())), abs((window_start + match.end()) - end)
            )
            distances.append((distance, direction))
    if not distances:
        return NumericDirection.NONE
    nearest = min(distance for distance, _ in distances)
    directions = {direction for distance, direction in distances if distance == nearest}
    return directions.pop() if len(directions) == 1 else NumericDirection.UNKNOWN


def _context_terms(sentence: str, start: int, end: int) -> tuple[str, ...]:
    window = sentence[max(0, start - 88) : min(len(sentence), end + 88)]
    without_numbers = _NUMBER_RE.sub(" ", window)
    tokens = {_ascii_fold(item) for item in _WORD_RE.findall(without_numbers)}
    terms = sorted(
        token
        for token in tokens
        if token not in _STOPWORDS
        and token not in _GENERIC_CONTEXT_TERMS
        and token not in {"ph", "percent", "pourcent", "celsius"}
    )
    return tuple(terms)


def _language(sentence: str) -> str:
    words = {_ascii_fold(item) for item in _WORD_RE.findall(sentence)}
    french = len(words.intersection(_FRENCH_MARKERS))
    english = len(words.intersection(_ENGLISH_MARKERS))
    if french > english:
        return "fr"
    if english > french:
        return "en"
    return "unknown"


def _is_locator(sentence: str, start: int, end: int) -> bool:
    prefix = sentence[max(0, start - 24) : start]
    suffix = sentence[end : min(len(sentence), end + 32)]
    if _LOCATOR_PREFIX_RE.search(prefix):
        return True
    return bool(re.search(r"doi\s*[:=]?\s*$", prefix, re.IGNORECASE) or suffix.startswith("/"))


def _quantity(
    *,
    sentence: str,
    match: re.Match[str],
    value_group: str = "value",
    unit: str | None,
    operator: NumericOperator,
    upper_group: str | None = None,
    uncertainty_group: str | None = None,
) -> _ParsedQuantity:
    value, precision = _canonical_number(match.group(value_group))
    upper_value = upper_precision = None
    if upper_group is not None and match.group(upper_group) is not None:
        upper_value, upper_precision = _canonical_number(match.group(upper_group))
    uncertainty = uncertainty_precision = None
    if uncertainty_group is not None and match.group(uncertainty_group) is not None:
        uncertainty, uncertainty_precision = _canonical_number(match.group(uncertainty_group))
    return _ParsedQuantity(
        quantity=NumericQuantity(
            value=value,
            precision=precision,
            unit=unit,
            operator=operator,
            upper_value=upper_value,
            upper_precision=upper_precision,
            uncertainty=uncertainty,
            uncertainty_precision=uncertainty_precision,
            direction=_direction_near(sentence, match.start(), match.end()),
        ),
        context_terms=_context_terms(sentence, match.start(), match.end()),
        language=_language(sentence),
    )


def _sentences(value: str) -> tuple[str, ...]:
    normalized = _normalize_text(value)
    return tuple(part for part in re.split(r"(?<=[.!?;])\s+|\n+", normalized) if part.strip())


def _overlaps(match: re.Match[str], spans: list[tuple[int, int]]) -> bool:
    return any(match.start() < end and start < match.end() for start, end in spans)


def _extract_quantities(value: str) -> tuple[tuple[_ParsedQuantity, ...], int]:
    """Extract only high-confidence quantity forms and count residual numeric tokens."""

    quantities: list[_ParsedQuantity] = []
    unparsed_count = 0
    for sentence in _sentences(value):
        consumed: list[tuple[int, int]] = []

        for match in _PH_RE.finditer(sentence):
            if not _overlaps(match, consumed):
                consumed.append((match.start(), match.end()))
                quantities.append(
                    _quantity(
                        sentence=sentence,
                        match=match,
                        unit="ph",
                        operator=_operator_before(sentence, match.start("value")),
                        uncertainty_group="uncertainty",
                    )
                )
        for pattern in (_UNCERTAINTY_RE, _BRACKET_RANGE_RE, _RANGE_RE):
            for match in pattern.finditer(sentence):
                if _overlaps(match, consumed):
                    continue
                unit = _canonical_unit(match.group("unit"))
                if pattern is _UNCERTAINTY_RE:
                    quantity = _quantity(
                        sentence=sentence,
                        match=match,
                        unit=unit,
                        operator=_operator_before(sentence, match.start("value")),
                        uncertainty_group="uncertainty",
                    )
                else:
                    if pattern is _RANGE_RE:
                        value_group = (
                            "led_value" if match.group("led_value") is not None else "dashed_value"
                        )
                        upper_group = (
                            "led_upper" if match.group("led_upper") is not None else "dashed_upper"
                        )
                    else:
                        value_group = "value"
                        upper_group = "upper"
                    quantity = _quantity(
                        sentence=sentence,
                        match=match,
                        unit=unit,
                        operator=NumericOperator.RANGE,
                        value_group=value_group,
                        upper_group=upper_group,
                    )
                consumed.append((match.start(), match.end()))
                quantities.append(quantity)
        for match in _QUANTITY_RE.finditer(sentence):
            if _overlaps(match, consumed) or _is_locator(sentence, match.start(), match.end()):
                continue
            consumed.append((match.start(), match.end()))
            quantities.append(
                _quantity(
                    sentence=sentence,
                    match=match,
                    unit=_canonical_unit(match.group("unit")),
                    operator=_operator_before(sentence, match.start("value")),
                )
            )
        for match in _NUMBER_RE.finditer(sentence):
            if _overlaps(match, consumed) or _is_locator(sentence, match.start(), match.end()):
                continue
            # A bare number can be meaningful, but this conservative first version
            # does not guess its dimension or its association with surrounding words.
            unparsed_count += 1
    return tuple(quantities), unparsed_count


def _same_structure(
    left: NumericQuantity, right: NumericQuantity
) -> tuple[bool, tuple[NumericIssueCode, ...]]:
    issues: list[NumericIssueCode] = []
    if left.value != right.value:
        left_decimal = _as_decimal(left.value)
        right_decimal = _as_decimal(right.value)
        if (
            left_decimal is not None
            and right_decimal is not None
            and abs(left_decimal) == abs(right_decimal)
        ):
            issues.append(NumericIssueCode.SIGN_MISMATCH)
        else:
            issues.append(NumericIssueCode.VALUE_MISSING)
    if left.precision != right.precision:
        issues.append(NumericIssueCode.PRECISION_MISMATCH)
    if left.unit != right.unit:
        if left.unit is None or right.unit is None:
            issues.append(NumericIssueCode.UNIT_MISSING)
        elif _unit_dimension(left.unit) == _unit_dimension(right.unit):
            issues.append(NumericIssueCode.UNSUPPORTED_CONVERSION)
        else:
            issues.append(NumericIssueCode.UNIT_MISMATCH)
    if left.operator != right.operator:
        issues.append(
            NumericIssueCode.RANGE_MISMATCH
            if NumericOperator.RANGE in {left.operator, right.operator}
            else NumericIssueCode.OPERATOR_MISMATCH
        )
    if left.upper_value != right.upper_value or left.upper_precision != right.upper_precision:
        issues.append(NumericIssueCode.RANGE_MISMATCH)
    if (
        left.uncertainty != right.uncertainty
        or left.uncertainty_precision != right.uncertainty_precision
    ):
        issues.append(NumericIssueCode.UNCERTAINTY_MISMATCH)
    if left.direction != right.direction:
        if NumericDirection.NONE in {left.direction, right.direction}:
            issues.append(NumericIssueCode.DIRECTION_MISSING)
        else:
            issues.append(NumericIssueCode.DIRECTION_MISMATCH)
    return not issues, tuple(dict.fromkeys(issues))


def _context_assessment(
    claim: _ParsedQuantity,
    source: _ParsedQuantity,
    *,
    total_matches: int,
    policy: NumericVerificationPolicy,
) -> tuple[NumericVerdict, tuple[NumericIssueCode, ...]]:
    if not policy.require_context:
        return NumericVerdict.SUPPORTED, ()
    claim_terms = set(claim.context_terms)
    source_terms = set(source.context_terms)
    if claim_terms and source_terms and claim_terms.intersection(source_terms):
        return NumericVerdict.SUPPORTED, ()
    if claim_terms or source_terms:
        if (
            claim.language != "unknown"
            and source.language != "unknown"
            and claim.language != source.language
        ):
            return NumericVerdict.AMBIGUOUS, (NumericIssueCode.CONTEXT_AMBIGUOUS,)
        return NumericVerdict.UNSUPPORTED, (NumericIssueCode.CONTEXT_MISMATCH,)
    if total_matches == 1 and policy.allow_unique_contextless_match:
        return NumericVerdict.SUPPORTED, ()
    return NumericVerdict.AMBIGUOUS, (NumericIssueCode.AMBIGUOUS_CANDIDATE,)


def _assess_quantity(
    claim: _ParsedQuantity,
    evidence: Mapping[str, tuple[_ParsedQuantity, ...]],
    *,
    policy: NumericVerificationPolicy,
) -> NumericQuantityAssessment:
    candidates: list[tuple[str, _ParsedQuantity]] = [
        (source_id, quantity)
        for source_id, quantities in evidence.items()
        for quantity in quantities
    ]
    full_matches: list[tuple[str, _ParsedQuantity]] = []
    structure_issues: list[NumericIssueCode] = []
    for source_id, candidate in candidates:
        equal, issues = _same_structure(claim.quantity, candidate.quantity)
        if equal:
            full_matches.append((source_id, candidate))
        else:
            structure_issues.extend(issues)
    if not full_matches:
        issues = tuple(dict.fromkeys(structure_issues)) or (NumericIssueCode.VALUE_MISSING,)
        return NumericQuantityAssessment(
            quantity=claim.quantity,
            verdict=NumericVerdict.UNSUPPORTED,
            source_id=None,
            issues=issues,
        )
    contextual: list[tuple[str, NumericVerdict, tuple[NumericIssueCode, ...]]] = []
    for source_id, candidate in full_matches:
        verdict, issues = _context_assessment(
            claim,
            candidate,
            total_matches=len(full_matches),
            policy=policy,
        )
        contextual.append((source_id, verdict, issues))
    supported = [item for item in contextual if item[1] is NumericVerdict.SUPPORTED]
    if len(supported) == 1:
        source_id, verdict, issues = supported[0]
        return NumericQuantityAssessment(claim.quantity, verdict, source_id, issues)
    if len(supported) > 1:
        return NumericQuantityAssessment(
            quantity=claim.quantity,
            verdict=NumericVerdict.AMBIGUOUS,
            source_id=None,
            issues=(NumericIssueCode.AMBIGUOUS_CANDIDATE,),
        )
    unsupported = [item for item in contextual if item[1] is NumericVerdict.UNSUPPORTED]
    if unsupported:
        source_id, verdict, issues = unsupported[0]
        return NumericQuantityAssessment(claim.quantity, verdict, source_id, issues)
    issues = tuple(dict.fromkeys(issue for _, _, items in contextual for issue in items))
    return NumericQuantityAssessment(
        quantity=claim.quantity,
        verdict=NumericVerdict.AMBIGUOUS,
        source_id=None,
        issues=issues or (NumericIssueCode.AMBIGUOUS_CANDIDATE,),
    )


def verify_numeric_claim(
    claim_text: str,
    evidence_by_id: Mapping[str, str],
    *,
    policy: NumericVerificationPolicy = DEFAULT_NUMERIC_VERIFICATION_POLICY,
) -> NumericVerificationReport:
    """Compare structured numerical claims to individually cited evidence texts.

    The function is side-effect free.  It neither raises a source-bearing error
    nor mutates supplied mappings.  A caller may choose to reject ``unsupported``
    immediately, retain ``ambiguous`` for expert/LLM review, or use the report in
    a benchmark-only audit mode.
    """

    claim_quantities, unparsed_numeric_count = _extract_quantities(claim_text)
    evidence = {
        source_id: _extract_quantities(text)[0] for source_id, text in evidence_by_id.items()
    }
    if not claim_quantities:
        if unparsed_numeric_count:
            return NumericVerificationReport(
                verdict=NumericVerdict.AMBIGUOUS,
                assessments=(),
                unparsed_numeric_count=unparsed_numeric_count,
                issues=(NumericIssueCode.UNPARSED_NUMERIC,),
            )
        return NumericVerificationReport(NumericVerdict.NOT_APPLICABLE, ())
    assessments = tuple(
        _assess_quantity(quantity, evidence, policy=policy) for quantity in claim_quantities
    )
    verdicts = {assessment.verdict for assessment in assessments}
    if NumericVerdict.UNSUPPORTED in verdicts:
        verdict = NumericVerdict.UNSUPPORTED
    elif NumericVerdict.AMBIGUOUS in verdicts or unparsed_numeric_count:
        verdict = NumericVerdict.AMBIGUOUS
    else:
        verdict = NumericVerdict.SUPPORTED
    issues = tuple(
        dict.fromkeys(
            [issue for assessment in assessments for issue in assessment.issues]
            + ([NumericIssueCode.UNPARSED_NUMERIC] if unparsed_numeric_count else [])
        )
    )
    return NumericVerificationReport(
        verdict=verdict,
        assessments=assessments,
        unparsed_numeric_count=unparsed_numeric_count,
        issues=issues,
    )
