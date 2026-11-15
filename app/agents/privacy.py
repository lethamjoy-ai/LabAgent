"""Local demographic minimization, before either external service is called."""
import re
from .parsing import DATE_PATTERN, BIRTH_LABEL, extract_identity, name_component_matches

PRIVATE_DATE_PATTERN = DATE_PATTERN + r"|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"


def routing_text(text, state):
    values = list(state.get("private_values", []))
    values += [state.get("patient_name"), state.get("date_of_birth")]
    values += [state.get("first_name"),state.get("last_name")]
    # Resolve supported inline identity syntax locally before removing its DOB
    # label. This also protects a name that is not yet present in SQLite.
    if re.search(BIRTH_LABEL,text,re.I):
        try:
            supplied_name, _ = extract_identity(text)
            if supplied_name:
                values.append(supplied_name)
        except ValueError:
            pass
    # Separate labels do not contain the combined stored name as a substring.
    # Mask their values locally before sending even the first booking turn.
    spans = [m.span(1) for label in (r"first[ -]+name", r"last[ -]+name")
             for m in name_component_matches(text, label)]
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + "[patient detail]" + text[end:]
    # Identity replies are processed locally. Send only a placeholder to classify
    # their role in the pending task, never the name/DOB themselves.
    if state.get("awaiting_identity"):
        if re.search(PRIVATE_DATE_PATTERN, text, re.I):
            return "[patient identity details supplied locally]"
        if (re.fullmatch(r"[\w'’,. -]{1,100}", text.strip()) and len(text.split()) <= 4
                and not re.search(r"\b(?:yes|no|confirm|cancel|tomorrow|today|how|what|show|book|schedule|with)\b", text, re.I)):
            return "[patient identity fragment supplied locally]"
        try:
            name, dob = extract_identity(text, awaiting=True)
            if dob or (name and not re.search(r"\b(?:how|what|show|tomorrow|today|with|confirm|cancel)\b", text, re.I)):
                return "[patient identity details supplied locally]"
        except ValueError:
            return "[patient identity details require local validation]"
    for value in sorted((str(v) for v in values if v), key=len, reverse=True):
        text = re.sub(re.escape(value), "[patient detail]", text, flags=re.I)
    text = re.sub(r"\bpatient\s+[A-Z][\w'-]+\s+[A-Z][\w'-]+", "patient [identity omitted]", text)
    # Suppress labelled identity spans and newly supplied name/date pairs too.
    text = re.sub(r"(?:\b(?:patient name|my name is|name:|MRN|patient ID)\b|" + BIRTH_LABEL + r").*", "[patient details omitted]", text, flags=re.I)
    if re.search(DATE_PATTERN, text, re.I):
        text = re.sub(r"\b[A-Z][\w'-]+\s+[A-Z][\w'-]+\s*,?\s*(?=" + DATE_PATTERN + ")", "[patient name] ", text)
    return re.sub(PRIVATE_DATE_PATTERN + r"|\bSYN\d+\b", "[date or identifier]", text, flags=re.I)
