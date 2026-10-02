from __future__ import annotations

import re

from frappe.utils import add_to_date, getdate, today


FREQUENCY_RULES = {
    "daily": {"label": "Daily", "interval_days": 1, "runtime_hours": None, "due_logic": "Calendar"},
    "weekly": {"label": "Weekly", "interval_days": 7, "runtime_hours": None, "due_logic": "Calendar"},
    "monthly": {"label": "Monthly", "interval_days": 30, "runtime_hours": None, "due_logic": "Calendar"},
    "quarterly": {"label": "Quarterly", "interval_days": 90, "runtime_hours": None, "due_logic": "Calendar"},
    "halfyearly": {"label": "Half Yearly", "interval_days": 182, "runtime_hours": None, "due_logic": "Calendar"},
    "yearly": {"label": "Yearly", "interval_days": 365, "runtime_hours": None, "due_logic": "Calendar"},
    "1500hrs": {"label": "1500 Hrs", "interval_days": None, "runtime_hours": 1500, "due_logic": "Runtime"},
    "300hrshalfyearly": {
        "label": "300 Hours / Half Yearly",
        "interval_days": 182,
        "runtime_hours": 300,
        "due_logic": "Hybrid",
    },
}


def normalize_lookup(value: str | None) -> str:
    value = cstr(value)
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def cstr(value) -> str:
    return str(value or "").strip()


def guess_equipment_group(
    equipment_code: str | None = None,
    equipment_name: str | None = None,
    equipment_type: str | None = None,
    source_reference: str | None = None,
) -> str:
    matches = get_reference_groups(
        source_reference=" ".join(
            [
                cstr(equipment_code),
                cstr(equipment_name),
                cstr(equipment_type),
                cstr(source_reference),
            ]
        )
    )
    return next(iter(matches)) if len(matches) == 1 else ""


def get_equipment_aliases(
    equipment_code: str | None = None,
    equipment_name: str | None = None,
    equipment_type: str | None = None,
    equipment_group: str | None = None,
) -> set[str]:
    aliases = {
        normalize_lookup(equipment_code),
        normalize_lookup(equipment_name),
        normalize_lookup(equipment_type),
        normalize_lookup(equipment_group),
    }

    group = cstr(equipment_group).upper()
    if group == "M32":
        aliases.update({normalize_lookup("M32"), normalize_lookup("Mega 32"), normalize_lookup("Mega32")})
    elif group == "M58":
        aliases.update({normalize_lookup("M58"), normalize_lookup("Mega 58"), normalize_lookup("Mega58")})
    elif group:
        aliases.add(normalize_lookup(group))

    return {alias for alias in aliases if alias}


def reference_matches_equipment(source_reference: str | None, aliases: set[str]) -> bool:
    reference_key = normalize_lookup(source_reference)
    if not reference_key:
        return False

    return any(alias and alias in reference_key for alias in aliases)


def get_reference_groups(source_reference: str | None, fallback_group: str | None = None) -> set[str]:
    raw_tokens = [cstr(source_reference).lower(), cstr(fallback_group).lower()]
    tokens = " ".join(raw_tokens)
    normalized_tokens = normalize_lookup(tokens)
    groups = set()

    if any(marker in tokens for marker in ("beta", "mega 32", "mega-32")) or any(
        marker in normalized_tokens for marker in ("mega32", "m32")
    ):
        groups.add("M32")

    if any(marker in tokens for marker in ("alpha", "mega 58", "mega-58")) or any(
        marker in normalized_tokens for marker in ("mega58", "m58")
    ):
        groups.add("M58")

    if any(marker in tokens for marker in ("tek 42", "tek-42")) or "tek42" in normalized_tokens:
        groups.add("TEK42")

    if any(marker in tokens for marker in ("tek 41", "tek-41")) or "tek41" in normalized_tokens:
        groups.add("TEK41")

    if any(marker in tokens for marker in ("tek 40", "tek-40")) or "tek40" in normalized_tokens:
        groups.add("TEK40")

    return groups


def get_frequency_details(value: str | None) -> dict[str, int | str | None]:
    raw_value = cstr(value)
    normalized = normalize_lookup(raw_value.replace("quaterly", "quarterly").replace("hours", "hrs"))

    details = FREQUENCY_RULES.get(normalized)
    if details:
        return details.copy()

    if normalized == "quaterly":
        return FREQUENCY_RULES["quarterly"].copy()

    return {
        "label": raw_value,
        "interval_days": None,
        "runtime_hours": None,
        "due_logic": "Manual",
    }


def compute_next_due_date(frequency_value: str | None, anchor_date=None):
    details = get_frequency_details(frequency_value)
    if not details.get("interval_days"):
        return None

    anchor_date = getdate(anchor_date or today())
    return getdate(add_to_date(anchor_date, days=details["interval_days"], as_string=True))
