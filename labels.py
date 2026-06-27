"""Reader-facing transparency labels.

Three variants, keyed off the verdict + confidence. The exact text lives here and
is reproduced verbatim in planning.md / README.md.
"""

from detection import HIGH_CONFIDENCE


def generate_label(attribution: str, confidence: float) -> str:
    """Map a verdict + confidence to the plain-language label shown to a reader."""
    pct = round(confidence * 100)
    strong = confidence >= HIGH_CONFIDENCE
    # Soften verdict wording when we have a verdict but not high confidence.
    indicates = "strongly indicates" if strong else "leans toward saying"
    conf_word = "high confidence" if strong else "moderate confidence"

    if attribution == "likely_ai":
        return (
            f"AI-generated ({conf_word}). Our analysis {indicates} this text was "
            "produced by an AI system. This verdict is based on two independent "
            "checks: a language-model assessment and writing-style statistics. "
            f"Confidence: {pct}%. AI detection is not perfect -- if you wrote this "
            "yourself, you can appeal this result."
        )

    if attribution == "likely_human":
        return (
            f"Human-written ({conf_word}). Our analysis {indicates} this text was "
            "written by a person. This verdict is based on two independent checks: "
            "a language-model assessment and writing-style statistics. "
            f"Confidence: {pct}%."
        )

    # uncertain
    return (
        "Attribution uncertain. We could not confidently tell whether this text is "
        "human-written or AI-generated, so we are not assigning a verdict. This is "
        "common for short, edited, or stylistically unusual writing. The creator's "
        f"authorship is not in question. (Internal signal strength: {pct}%.)"
    )
