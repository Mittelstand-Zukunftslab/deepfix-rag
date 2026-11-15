"""Whether an answer is grounded in the corpus, declined, or had no sources.

The phrase list lives here rather than in the evaluation runner because two
callers need it: the offline eval counts a deflection as a failure mode, and the
live ``POST /ask`` route must not badge a refusal as "Grounded". One list keeps
the two from drifting apart.

Matching is substring-based on the lowercased answer, which is what the eval
runner has always done, plus a small set of regular expressions for the
"No explanation … is available" shape where the noun in the middle varies.
"""

from enum import StrEnum
import re


class Grounding(StrEnum):
    """How much the corpus supports the answer that was returned."""

    GROUNDED = "grounded"
    DECLINED = "declined"
    NO_RELEVANT_SOURCES = "no_relevant_sources"


DEFLECTION_PHRASES = (
    "i do not have enough information",
    "i don't have enough information",
    "not enough information",
    "i cannot answer",
    "the context does not provide",
    "the provided text does not",
    "not mentioned in the context",
    "i am unable to answer",
    "does not contain the answer to this question",
    # The refusal a small model writes when it will not guess at a term it does
    # not know, as in 'No explanation of "rerank off" is available.'
    "no explanation",
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


def is_deflection(answer: str) -> bool:
    """True when the answer text is a refusal rather than a corpus-grounded answer."""
    lowered = (answer or "").lower()
    if not lowered:
        return False
    if any(phrase in lowered for phrase in DEFLECTION_PHRASES):
        return True
    return any(pattern.search(lowered) for pattern in DEFLECTION_PATTERNS)


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
