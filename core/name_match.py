"""Company-name normalisation and fuzzy matching for bank verification.

The same company is routinely written several ways. For example, "M/s ACME
PRECISION TOOLS PVT. LTD." and "Acme Precision Tools Private Limited" are the
same entity. Banks also truncate and abbreviate names. We normalise both sides
before scoring, so that only *real* differences lower the score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

#: Abbreviation -> canonical word. Applied per token after punctuation removal.
ABBREVIATIONS: dict[str, str] = {
    "pvt": "private",
    "pte": "private",
    "ltd": "limited",
    "co": "company",
    "corp": "corporation",
    "inc": "incorporated",
    "intl": "international",
    "mfg": "manufacturing",
    "ind": "industries",
    "inds": "industries",
    "bros": "brothers",
    "svcs": "services",
    "engg": "engineering",
}

_DOTTED_ACRONYM = re.compile(r"\b(?:[a-z]\.){2,}")
_MS_PREFIX = re.compile(r"^\s*m\s*/\s*s\.?\s+|^\s*messrs\.?\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def normalise_name(name: str) -> str:
    """Canonicalise a business name so cosmetic differences don't count.

    Steps: lowercase, '&' -> 'and', drop a leading 'M/s', join dotted acronyms
    (A.B.C. -> abc), replace punctuation with spaces, expand common
    abbreviations (pvt -> private, ltd -> limited, co -> company, ...), and
    collapse whitespace.
    """
    text = (name or "").lower().replace("&", " and ")
    text = _MS_PREFIX.sub("", text)
    text = _DOTTED_ACRONYM.sub(lambda m: m.group(0).replace(".", "") + " ", text)
    text = _NON_ALNUM.sub(" ", text)
    tokens = [ABBREVIATIONS.get(tok, tok) for tok in text.split()]
    return _SPACES.sub(" ", " ".join(tokens)).strip()


@dataclass(frozen=True)
class NameMatch:
    """Result of comparing a declared name against the bank account holder."""

    candidate_label: str
    candidate_raw: str
    candidate_normalised: str
    holder_raw: str
    holder_normalised: str
    raw_score: float
    score: float


def match_names(candidate: str, holder: str, label: str = "legal_name") -> NameMatch:
    """Score two names with token_sort_ratio on their normalised forms.

    token_sort_ratio ignores word order ("Sharma Rajesh" == "Rajesh Sharma"),
    which banks often reorder, but still penalises missing or extra words.
    """
    cand_norm = normalise_name(candidate)
    holder_norm = normalise_name(holder)
    return NameMatch(
        candidate_label=label,
        candidate_raw=candidate,
        candidate_normalised=cand_norm,
        holder_raw=holder,
        holder_normalised=holder_norm,
        raw_score=round(fuzz.token_sort_ratio(candidate or "", holder or ""), 1),
        score=round(fuzz.token_sort_ratio(cand_norm, holder_norm), 1),
    )


def best_match(candidates: dict[str, str], holder: str) -> NameMatch:
    """Return the highest-scoring match among several declared names."""
    matches = [match_names(value, holder, label) for label, value in candidates.items() if value]
    return max(matches, key=lambda m: m.score)
