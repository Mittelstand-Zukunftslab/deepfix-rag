"""Whether an answer is grounded in the corpus, declined, or had no sources.

The phrase list lives here rather than in the evaluation runner because two
callers need it: the offline eval counts a deflection as a failure mode, and the
live ``POST /ask`` route must not badge a refusal as "Grounded". One list keeps
the two from drifting apart.

Matching is substring-based on a normalized answer: NFKC-folded, lowercased,
with typographic apostrophes and quotes folded to ASCII and runs of whitespace
collapsed. That is what lets "I don’t have enough information" (U+2019) and a
refusal wrapped across lines match a phrase written with ASCII "'". A small set
of regular expressions covers the "No explanation … is available" shape where
the noun in the middle varies. No model call decides this.
"""

from enum import StrEnum
import re
import unicodedata


class Grounding(StrEnum):
    """How much the corpus supports the answer that was returned."""

    GROUNDED = "grounded"
    DECLINED = "declined"
    NO_RELEVANT_SOURCES = "no_relevant_sources"
    # The turn answered without searching at all — a thanks, a small talk reply,
    # or a rework of the assistant's own previous answer from content already in
    # the thread. It is not "not found": nothing was looked for, so there is
    # nothing to have failed to find. Never rendered as grounded.
    NOT_SEARCHED = "not_searched"


DEFLECTION_PHRASES = (
    "i do not have enough information",
    "i don't have enough information",
    "i do not have that information",
    "i don't have that information",
    "not enough information",
    "insufficient information",
    "not enough context",
    "i cannot answer",
    "i cannot provide an answer",
    "i cannot determine",
    "i cannot find",
    "i could not find",
    "i couldn't find",
    "i am unable to answer",
    "i'm unable to answer",
    "i am not able to answer",
    "i'm not able to answer",
    "i do not have access to",
    "i don't have access to",
    "cannot be answered",
    "the answer cannot be found",
    "cannot be determined from",
    "the context does not provide",
    "the context does not contain",
    "the context does not include",
    "the context is insufficient",
    "the provided context does not",
    "the provided text does not",
    "the provided passages do not",
    "the provided documents do not",
    "the provided information does not",
    "the given context does not",
    "not mentioned in the context",
    "not present in the context",
    "there is no mention of",
    "there are no mentions of",
    "is not available in the provided",
    "there is no information",
    "no information is available",
    "no information available",
    "no relevant information",
    "not found in the provided",
    "does not contain the answer to this question",
    "does not contain any information",
    "does not include any information",
    "do not contain any information",
    "do not provide any information",
    # The refusal a small model writes when it will not guess at a term it does
    # not know, as in 'No explanation of "rerank off" is available.' Kept to the
    # ways that sentence starts, so an answer that merely mentions an
    # explanation is not read as a refusal.
    "no explanation is",
    "no explanation of",
    "no explanation for",
    "no explanation available",
    "no explanation was given",
    "no explanation was found",
    "no explanation was provided",
    # German phrasings, for a corpus and a prompt that are German.
    "ich habe nicht genügend informationen",
    "nicht genügend informationen",
    "ich kann diese frage nicht beantworten",
    "kann diese frage nicht beantworten",
    "die bereitgestellten informationen enthalten",
    "im kontext nicht erwähnt",
    "steht nicht im kontext",
    "keine erklärung",
    "keine informationen dazu",
)

DEFLECTION_PATTERNS = (
    re.compile(r"\bno\s+explanation\b[^.]{0,120}\bis\s+available\b"),
    re.compile(r"\bkeine\s+erkl(?:ä|ae)rung\b[^.]{0,120}\b(?:verf(?:ü|ue)gbar|vorhanden)\b"),
    re.compile(r"\bnicht\s+in\s+den\s+(?:dokumenten|bereitgestellten)\b"),
)

# U+2018..U+201F, the prime marks, U+02BC and the guillemets are the quotation
# marks a served model writes instead of ASCII ' and ".
_QUOTE_FOLDING = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u02bc": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2033": '"',
        "\u00ab": '"',
        "\u00bb": '"',
    }
)


def normalize_answer(answer: str) -> str:
    """The form the phrase list and patterns are matched against."""
    text = unicodedata.normalize("NFKC", answer or "").translate(_QUOTE_FOLDING)
    return " ".join(text.lower().split())


def is_deflection(answer: str) -> bool:
    """True when the answer text is a refusal rather than a corpus-grounded answer."""
    normalized = normalize_answer(answer)
    if not normalized:
        return False
    if any(phrase in normalized for phrase in DEFLECTION_PHRASES):
        return True
    return any(pattern.search(normalized) for pattern in DEFLECTION_PATTERNS)


def grounding_for(answer: str | None) -> Grounding:
    """Classify one answer: grounded, declined, or nothing to ground.

    ``None`` and blank mean no answer was produced from passages at all, which
    is also how a stored ``retrieval`` interaction (which never has an answer)
    reports itself.
    """
    if answer is None or not answer.strip():
        return Grounding.NO_RELEVANT_SOURCES
    if is_deflection(answer):
        return Grounding.DECLINED
    return Grounding.GROUNDED
