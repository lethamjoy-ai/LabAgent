"""Deterministic public presentation: no internal identifiers or addresses."""
from collections import Counter
from datetime import datetime


def natural_date(value):
    return f"{value:%B} {value.day}, {value.year}"


def natural_time(value):
    return value.strftime("%I:%M %p").lstrip("0")


def appointment_text(row, timezone, now):
    local = row.start.astimezone(timezone)
    period = "Upcoming" if local > now else "Historical"
    assigned = f" with {row.staff_name}" if row.staff_name else "; staff assignment pending"
    return (f"{row.patient_name}'s {row.test_name} appointment is {row.status} for "
            f"{natural_date(local)} at {natural_time(local)} ({timezone.key}){assigned}. {period} appointment.")


def overview_summary(rows):
    counts = Counter(r.status for r in rows)
    statuses = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    return f"The laboratory schedule includes {len(rows)} appointment{'s' if len(rows) != 1 else ''}" + (f": {statuses}." if rows else ".")


def overview_table(rows, timezone):
    return [{"Patient Name":r.patient_name, "Date of Birth":str(r.date_of_birth) if r.date_of_birth else "Not available",
             "Laboratory Test":r.test_name,
             "Appointment Time":natural_time(r.start.astimezone(timezone)), "Status":r.status.capitalize()} for r in rows]


def appointment_table(rows, timezone):
    """Multi-date retrieval requires a visible local appointment date."""
    return [{"Patient Name":r.patient_name, "Date of Birth":str(r.date_of_birth) if r.date_of_birth else "Not available",
             "Laboratory Test":r.test_name, "Appointment Date":natural_date(r.start.astimezone(timezone)),
             "Appointment Time":natural_time(r.start.astimezone(timezone)), "Status":r.status.capitalize()} for r in rows]
