"""Small, offline parsers for demographic and common appointment expressions."""
import re
import unicodedata
from datetime import date, datetime, timedelta

DATE_PATTERN = r"\b(?:\d{4}-\d{2}-\d{2}|(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+\d{4})\b"


def parse_date(value):
    value = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", value, flags=re.I).replace(",", "")
    for pattern in ("%Y-%m-%d", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            pass
    raise ValueError("Please use a valid date, such as January 15, 1990 or 1990-01-15.")


BIRTH_LABEL = r"\b(?:date[ -]+of[ -]+birth|birth[ -]+date|dob|born)\b(?:\s*\((?:DOB|YYYY-MM-DD)\))?"
DOB_PATTERN = BIRTH_LABEL + r"\s*(?:is|on|[:=\-–—]+)?\s*(" + DATE_PATTERN + r")"
IDENTITY_BOUNDARIES = r"\b(?:first[ -]+name|last[ -]+name|date[ -]+of[ -]+birth|birth[ -]+date|dob|born|appointment(?: date| time)?|test|with|on|at)\b"
TIME_PATTERN = r"\b(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)\b|\b(\d{2}):(\d{2})\b"


def name_component_matches(text, label):
    return list(re.finditer(r"\b" + label + r"\s*(?::|=|is)?\s*(?!" + IDENTITY_BOUNDARIES + r")(.+?)(?=\s*[,;\n]|\s*" + IDENTITY_BOUNDARIES + r"|$)", text, re.I))


def identity_date_match(text, awaiting=False):
    explicit = list(re.finditer(DOB_PATTERN, text, re.I))
    if len({m.group(1).casefold() for m in explicit}) > 1:
        raise ValueError("Please provide one date of birth so I can match the patient's record safely.")
    if explicit:
        return explicit[0], True
    dates = [m for m in re.finditer(DATE_PATTERN, text, re.I)
             if not re.search(r"\b(?:appointment[ -]+date|date|on)\s*[:=]?\s*$", text[:m.start()], re.I)]
    if awaiting and len(dates) > 1:
        raise ValueError("Please provide one date of birth so I can match the patient's record safely.")
    return (dates[0] if awaiting and dates else None), False


def appointment_text_without_dob(text, awaiting=False):
    """Remove only identified birth dates before parsing appointment fields."""
    match, explicit = identity_date_match(text, awaiting)
    if explicit:
        return re.sub(DOB_PATTERN, " ", text, flags=re.I)
    if match:
        return text[:match.start()] + " " + text[match.end():]
    return text


def extract_identity(text, awaiting=False):
    """Return explicitly supplied name/DOB, without guessing first-name matches."""
    dob_match, explicit = identity_date_match(text, awaiting)
    dob = parse_date(dob_match.group(1) if explicit else dob_match.group()) if dob_match else None
    name = None
    parts = []
    labelled = False
    for label in (r"first[ -]+name", r"last[ -]+name"):
        matches = name_component_matches(text, label)
        values = {" ".join(unicodedata.normalize("NFKC", m.group(1)).strip(" .").split()) for m in matches}
        if len(values) > 1:
            raise ValueError("Please provide one first name and one last name for the patient.")
        labelled = labelled or bool(matches)
        parts.append(next(iter(values), ""))
    if labelled:
        if all(parts) and all(not re.search(r"\d", p) for p in parts):
            name = " ".join(parts)
        return name, dob
    if awaiting or explicit or re.search(r"\b(?:my name is|name:)\b", text, re.I):
        prefix = text[:dob_match.start()] if dob_match else text
        prefix = re.split(r"\b(?:for|patient name|patient)\s*:?\s*", prefix, flags=re.I)[-1]
        prefix = re.sub(r"\b(?:my name is|name:|i am|i'm)\s*", "", prefix, flags=re.I)
        prefix = re.sub(r"\b(?:date of birth|dob|born)\b.*$", "", prefix, flags=re.I).strip(" ,.;")
        # Do not misinterpret scheduling requests or identifiers as personal names.
        if not re.search(r"\b(?:book|schedule|appointment|specimen|CBC|GLU|A1C|SYN\d+)\b", prefix, re.I):
            if len(prefix.split()) >= 2 and not re.search(r"\d", prefix):
                name = " ".join(unicodedata.normalize("NFKC", prefix).replace(",", " ").split())
    return name, dob


def requested_day(text, today):
    matches = list(re.finditer(DATE_PATTERN, text, re.I))
    if len({m.group().lower() for m in matches}) > 1:
        raise ValueError("Which date should I use? Please provide one selection.")
    if matches:
        return parse_date(matches[0].group())
    if "tomorrow" in text.lower():
        return today + timedelta(days=1)
    if "today" in text.lower():
        return today
    return None


def requested_time(text):
    matches = list(re.finditer(TIME_PATTERN, text, re.I))
    values = []
    for m in matches:
        if m.group(1):
            hour = int(m.group(1))
            if not 1 <= hour <= 12:
                raise ValueError("Please use a valid appointment time.")
            if int(m.group(2) or 0) > 59:
                raise ValueError("Please use a valid appointment time.")
            hour = hour % 12 + (12 if m.group(3).lower().startswith("p") else 0)
            values.append(f"{hour:02}:{int(m.group(2) or 0):02}")
        else:
            if int(m.group(4)) > 23 or int(m.group(5)) > 59:
                raise ValueError("Please use a valid appointment time.")
            values.append(f"{m.group(4)}:{m.group(5)}")
    if len(set(values)) > 1:
        raise ValueError("Which time should I use? Please provide one selection.")
    return values[0] if values else None


def is_booking_field_reply(text, awaiting_fields):
    """Recognize only field-shaped answers to a stored booking question.

    Called after the LLM boundary. Commands and independent questions leave
    unconsumed words and therefore keep the model's normal route.
    """
    if not set(awaiting_fields or ()) & {"date", "time"}:
        return False
    remaining, dates = re.subn(DATE_PATTERN, " ", text, flags=re.I)
    remaining, relative = re.subn(r"\b(?:today|tomorrow)\b", " ", remaining, flags=re.I)
    remaining, times = re.subn(TIME_PATTERN, " ", remaining, flags=re.I)
    # Include malformed numeric time answers so validation can ask again without
    # losing the action (e.g. 25:00). Never accept arbitrary free text here.
    remaining, numeric = re.subn(r"\b\d{1,2}:\d{2}\b", " ", remaining)
    remaining = re.sub(r"\b(?:(?:appointment[ -]+)?(?:date|time)|on|at|and|or|please)\b", " ", remaining, flags=re.I)
    return bool(dates or relative or times or numeric) and not re.sub(r"[\s:;,=.?!\-–—]", "", remaining)
