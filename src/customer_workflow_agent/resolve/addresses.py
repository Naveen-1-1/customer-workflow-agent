"""Turn extracted address fields into a complete, normalized address."""

import re

from customer_workflow_agent.store.models import UserAddress

US_STATES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
}
_US = {"us", "usa", "u.s.", "u.s.a.", "united states", "united states of america", "america"}
REQUIRED = ("address1", "city", "state", "zip")
FIELD_LABELS = {"address1": "street address", "city": "city", "state": "state", "zip": "zip code"}


def merge_fields(old: dict | None, new: dict | None) -> dict:
    out = dict(old or {})
    for k, v in (new or {}).items():
        if v is not None and str(v).strip():
            out[k] = str(v).strip()
    return out


def complete_address(fields: dict) -> tuple[UserAddress | None, list[str]]:
    """Returns (address, missing field labels). Country defaults to USA (all customers are US)."""
    f = {k: (fields.get(k) or "").strip() for k in (*REQUIRED, "address2", "country")}
    missing = [FIELD_LABELS[k] for k in REQUIRED if not f[k]]
    if f["zip"] and not re.fullmatch(r"\d{5}", f["zip"]):
        digits = re.sub(r"\D", "", f["zip"])
        if len(digits) >= 5:
            f["zip"] = digits[:5]
        else:
            missing.append("5-digit zip code")
    if missing:
        return None, missing
    state = US_STATES.get(f["state"].lower(), f["state"])
    country = "USA" if not f["country"] or f["country"].lower() in _US else f["country"]
    return (
        UserAddress(
            address1=f["address1"],
            address2=f["address2"],
            city=f["city"],
            state=state.upper() if len(state) == 2 else state,
            zip=f["zip"],
            country=country,
        ),
        [],
    )


def same_address(a: UserAddress, b: UserAddress) -> bool:
    def key(x: UserAddress) -> tuple:
        return tuple(str(v).strip().lower() for v in x.model_dump().values())

    return key(a) == key(b)
