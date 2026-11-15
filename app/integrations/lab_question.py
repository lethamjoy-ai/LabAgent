"""Preserve laboratory questions verbatim; never append patient/appointment state.

This boundary blocks obvious identity-bearing queries instead of rewriting test
names using a local allowlist. It is a demo privacy guard, not a production DLP.
"""
import re
from app.agents.privacy import PRIVATE_DATE_PATTERN


class LabQuestionPrivacyError(ValueError):
    pass


def lab_question(original, test_id=None, clarification="", sensitive_values=()):
    # test_id is retained only for call compatibility. It must not replace or
    # restrict the actual test name supplied in the question.
    parts = [original, clarification]
    for part in parts:
        if (re.search(r"\b(?:DOB|date of birth|MRN|patient ID)\b|"+PRIVATE_DATE_PATTERN+r"|\bSYN\d+\b",part,re.I)
                or re.search(r"\b[Pp]atient\s+(?:[Nn]ame\b|[A-Z][a-z]+\s+[A-Z][a-z]+)",part)
                or any(value and str(value).casefold() in part.casefold() for value in sensitive_values)):
            raise LabQuestionPrivacyError("Please restate the laboratory question without patient names, dates of birth, or patient identifiers. No query was sent to LabRAG.")
    if not clarification:
        return original
    return original + "\n\nLaboratory test clarification (preserve the exact test name and qualifiers): " + clarification
