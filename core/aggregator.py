"""
Merges Wikipedia summary, Wikidata structured fields, and social media stats
into a single normalised profile dict.
"""

import re
from datetime import datetime, date

# Ordered from most-specific to least so the first match wins.
_SKIN_PATTERNS = [
    r"\b(light[- ]skinned)\b",
    r"\b(dark[- ]skinned)\b",
    r"\b(fair[- ]skinned|fair skin)\b",
    r"\b(brown[- ]skinned|brown skin)\b",
    r"\b(olive[- ]skinned|olive skin|olive complexion)\b",
    r"\b(caramel[- ]skinned|caramel skin)\b",
    r"\b(pale[- ]skinned|pale skin|pale complexion)\b",
    r"\b(ebony skin|deep[- ]brown skin)\b",
    r"\b(medium[- ]brown skin|tan skin|tanned skin)\b",
    r"\b(dark complexion|light complexion|fair complexion)\b",
    r"\b(melanin[- ]rich)\b",
    r"\b(afro[- ]latina|afrolatina)\b",   # proxy for mixed brown-skin context
]


def _scan_skin_tone(text):
    """Best-effort skin-tone extraction from Wikipedia article text."""
    if not text:
        return None
    t = text.lower()
    for pat in _SKIN_PATTERNS:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            return m.group(1).replace("-", " ").strip()
    return None


def _age_from_dob(dob_str):
    if not dob_str:
        return None
    try:
        clean = str(dob_str).lstrip("+").split("T")[0][:10]
        dob   = datetime.strptime(clean, "%Y-%m-%d").date()
        today = date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    except Exception:
        return None


def _fmt_height(cm_str):
    try:
        cm = float(str(cm_str).lstrip("+"))
        feet   = int(cm // 30.48)
        inches = int((cm % 30.48) // 2.54)
        return f"{cm:.0f} cm  ({feet}'{inches}\")"
    except Exception:
        return str(cm_str)


def _as_list(val):
    if val is None:
        return []
    return val if isinstance(val, list) else [val]


def aggregate_entity_data(name, wiki_data, social_data, category, user_notes=""):
    """
    Combine all scraped data into one profile dict.
    Empty/None values are stripped so the formatter can render cleanly.
    """
    summary = wiki_data.get("summary", {})
    wd      = wiki_data.get("wikidata", {})

    profile = {
        "name":              name,
        "category":          category,
        "wikipedia_title":   wiki_data.get("wikipedia_title"),
        "wikidata_qid":      wiki_data.get("wikidata_qid"),
        "wikipedia_url":     summary.get("wikipedia_url"),
        "description":       summary.get("description", ""),
        "extract":           (summary.get("extract") or "")[:1200],
        "thumbnail":         summary.get("thumbnail"),
    }

    # ── personal / biographical ────────────────────────────────────────────────
    dob = wd.get("date_of_birth")
    if dob:
        profile["date_of_birth"] = str(dob)[:10]
        age = _age_from_dob(dob)
        if age:
            profile["age"] = age

    for field in ("birth_name", "place_of_birth", "gender", "citizenship",
                  "ethnicity", "official_website", "country",
                  "eye_color", "hair_color"):
        val = wd.get(field)
        if val:
            profile[field] = val

    # Skin tone — not in Wikidata; scan the full Wikipedia lead section
    skin = _scan_skin_tone(wiki_data.get("lead_text", "") or profile.get("extract", ""))
    if skin:
        profile["skin_tone"] = skin

    if wd.get("citizenship"):
        profile["nationality"] = wd["citizenship"]

    height_raw = wd.get("height_cm")
    if height_raw:
        profile["height"] = _fmt_height(height_raw)

    # ── list fields ────────────────────────────────────────────────────────────
    profile["occupations"]          = [o for o in _as_list(wd.get("occupation")) if o]
    profile["genres"]               = [g for g in _as_list(wd.get("genres")) if g]
    profile["founders"]             = [f for f in _as_list(wd.get("founders")) if f]
    profile["wikipedia_categories"] = wiki_data.get("categories", [])[:15]

    # ── brand / org ────────────────────────────────────────────────────────────
    for field in ("founded", "industry", "country_of_origin", "sport"):
        val = wd.get(field)
        if val:
            profile[field] = val

    # ── social ────────────────────────────────────────────────────────────────
    profile["social"] = social_data or {}

    if user_notes:
        profile["notes"] = user_notes

    # Strip empties
    profile = {
        k: v for k, v in profile.items()
        if v is not None and v != [] and v != ""
    }

    return profile
