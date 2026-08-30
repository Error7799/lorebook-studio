"""
Convert normalised profile dicts into SillyTavern World Info (lorebook) JSON.

Entries are emitted with the full modern SillyTavern World Info schema —
the same field set the Editor writes — so a generated lorebook round-trips
cleanly through the Editor and imports into SillyTavern without warnings.
"""

import re
from datetime import datetime

# Detect raw IDs: YouTube channel IDs (UC…) or Spotify/generic base62 IDs
_RAW_ID_RE = re.compile(r'^(UC[A-Za-z0-9_-]{18,}|[A-Za-z0-9]{20,})$')


def _is_raw_id(handle):
    """True if handle is an opaque platform ID rather than a human username."""
    return bool(_RAW_ID_RE.match(handle))


# ── keyword builder ───────────────────────────────────────────────────────────

# Wikis list these among a character's aliases, but as a lorebook trigger a
# bare "Brat" or "Master" fires on half of an ordinary roleplay message.
# Multi-word epithets built on them ("The Dangerous Fireball Kid") are fine.
_GENERIC_ALIASES = {
    "brat", "brother", "sister", "kid", "kiddo", "boy", "girl", "man",
    "woman", "boss", "master", "mistress", "teacher", "student", "sensei",
    "monster", "hero", "villain", "king", "queen", "prince", "princess",
    "doctor", "friend", "child", "son", "daughter", "father", "mother",
    "dad", "mom", "uncle", "aunt", "captain", "chief", "sir", "lady",
    "lord", "old man", "the boy", "the girl", "the kid", "the man",
    "baby", "baby girl", "baby boy", "sis", "bro", "cousin", "grandmother",
    "grandfather", "granny", "grandma", "grandpa", "husband", "wife",
}


# Words that carry no identity on their own — "Iron" should not pull in the
# Iron Man entry, and "Black" should not pull in Black Widow.
_WEAK_NAME_PARTS = {
    "the", "of", "and", "lord", "lady", "iron", "spider", "super", "captain",
    "doctor", "black", "white", "grey", "gray", "blue", "green", "star",
    "dark", "light", "king", "queen", "prince", "lady", "great", "young",
    "elder", "master", "little", "baron", "agent", "man", "woman", "girl",
    "boy", "clan", "team", "squad", "high", "school", "city", "town",
}


# Entries that describe one individual. Splitting their name into parts gives
# a useful extra trigger ("Lamar", "Okkotsu"); doing the same to "Tokyo
# Jujutsu High" would make the school fire on the word "Tokyo".
# Ordinary words that turn up as stage names and nicknames. As a lorebook
# trigger a bare "Classic" or "Money" fires on half the messages in a chat,
# which is worse than the entry never firing at all.
_COMMON_WORD_ALIASES = {
    "classic", "legend", "legendary", "money", "cash", "ice", "snow", "fire",
    "angel", "devil", "god", "goddess", "saint", "star", "sun", "moon",
    "red", "blue", "green", "black", "white", "gold", "silver", "pink",
    "young", "big", "little", "real", "true", "future", "past", "present",
    "genius", "prodigy", "champion", "chief", "general", "major", "minor",
    "the kid", "kid", "problem", "trouble", "nature", "science", "magic",
    "diamond", "pearl", "ruby", "rose", "storm", "thunder", "shadow",
    # Redirects widen the net a long way, and a wiki will happily point a bare
    # common noun at a character — "Rock" redirects to Number 6. As a trigger
    # that fires on any mention of a rock, which is worse than not firing.
    "rock", "pop", "stone", "steel", "wind", "rain", "cloud", "sky", "sea", "ocean",
    "river", "mountain", "forest", "island", "hero", "heroes", "villain",
    "villains", "boss", "leader", "mask", "ghost", "beast", "dragon", "wolf",
    "tiger", "bear", "eagle", "hawk", "crow", "spark", "flash", "blade",
    "sword", "shield", "hammer", "arrow", "bullet", "bomb", "engine", "quirk",
    "power", "powers", "school", "student", "teacher", "police", "doctor",
    "nurse", "number", "one", "two", "three", "zero", "six",
}

_PERSON_CATEGORIES = {
    "character", "person", "musician", "actor", "athlete", "influencer",
    "politician", "scientist", "writer", "comedian", "model", "gamer",
}


def _build_fandom_keys(profile):
    """
    Trigger words for a wikitext-sourced entry: the subject's names, nothing
    generic. Used for both Fandom pages and scraped Wikipedia articles, which
    carry the same aliases-and-infobox shape.

    In-universe pages already sit in a narrow world, so broad triggers like an
    occupation ("student") would fire on almost every message. Proper nouns
    and aliases are what should pull the entry into context.
    """
    name = (profile.get("name") or "").strip()
    keys = {name} if name else set()

    for alias in profile.get("aliases", []):
        # "Kin-chan (by Kirara Hoshi)" → "Kin-chan"
        alias = re.sub(r"\s*\([^)]*\)", "", alias).strip(" ,;")
        if not alias or alias.lower() == name.lower():
            continue
        low = alias.lower()
        if low in _GENERIC_ALIASES:
            continue
        # One ordinary word is not an identity, however the wiki lists it.
        if " " not in alias and (low in _COMMON_WORD_ALIASES
                                 or low in _WEAK_NAME_PARTS):
            continue
        keys.add(alias)

    page_title = (profile.get("page_title") or "").strip()
    if page_title and page_title.lower() != name.lower():
        keys.add(page_title)

    # Redirects are the names a wiki's own readers search for, which makes
    # them the names that turn up in chat. "Knuckleduster" and "O'Clock" both
    # point at Iwao Oguro and neither is in his infobox, so without these the
    # entry never fires on the name the story actually uses. They are already
    # proper nouns pointing at this page, so they need no alias filtering —
    # only the same "one ordinary word is not an identity" guard.
    for other in profile.get("redirects", []):
        other = re.sub(r"\s*\([^)]*\)", "", other).strip(" ,;")
        low = other.lower()
        if not other or low == name.lower() or low in _GENERIC_ALIASES:
            continue
        if " " not in other and (low in _COMMON_WORD_ALIASES
                                 or low in _WEAK_NAME_PARTS):
            continue
        keys.add(other)

    # Full/native names live in the infobox rather than the page title —
    # "Kiri" is the article, "Kiri te Suli Kìreysì'ite" is what a character
    # actually gets called in dialogue.
    name_labels = {"rōmaji", "kanji", "japanese name", "name", "full name",
                   "real name", "birth name", "native name", "viz name",
                   "true name", "alias name", "other name"}
    for fact in profile.get("facts", []):
        if fact.get("label", "").lower() in name_labels:
            value = fact.get("value", "").strip()
            if (value and value.lower() != name.lower()
                    and "," not in value and len(value) <= 60):
                keys.add(value)

    # A character's surname or given name on its own is a useful trigger.
    # Only for characters, though: splitting "Tokyo Jujutsu High" would make
    # the school fire on the word "Tokyo". Trailing punctuation has to go too,
    # so "Spider-Man: Homecoming" cannot contribute the key "Spider-Man:".
    # Only for names that are actually two names. "50 Cent" and "6ix9ine" are
    # single stage names that happen to contain a space; splitting them yields
    # "Cent", which fires on any mention of money.
    parts = name.split()
    looks_personal = (len(parts) >= 2
                      and all(p[:1].isupper() and not any(c.isdigit() for c in p)
                              for p in parts))
    if looks_personal and             (profile.get("category") or "").lower() in _PERSON_CATEGORIES:
        for part in parts:
            part = part.strip(".,:;!?\"'()[]")
            if (len(part) >= 4 and part.lower() not in _WEAK_NAME_PARTS
                    and part.lower() not in _COMMON_WORD_ALIASES):
                keys.add(part)

    # An arc or event page is titled with a decoration the chat will not use:
    # people say "the Sky Egg" or "Naruhata Lockdown", not "Sky Egg Arc". The
    # stripped form is only a trigger when what is left is still distinctive.
    bare = re.sub(r"\s*\b(story\s+)?(arc|saga|incident\s+arc)\b\s*$", "",
                  name, flags=re.I).strip()
    if bare and bare.lower() != name.lower() and len(bare) >= 6:
        keys.add(bare)

    return sorted(k for k in keys if k and len(k) > 1)


def _build_keys(profile):
    """
    Deduplicated, sorted keyword list.
    Only human-readable handles get added (raw YouTube/Spotify IDs are excluded).
    """
    # A profile that already knows its own triggers says so. A Relationships
    # entry is the case: its subject is a character but its title is a subpage,
    # and the usual builder would take that apart and offer "Relationships".
    stated = profile.get("keys")
    if stated:
        return sorted({k.strip() for k in stated if k and len(k.strip()) > 1})
    # A scraped article — Fandom or Wikipedia — has aliases and infobox name
    # rows the compact real-person path knows nothing about.
    if profile.get("source") == "fandom" or profile.get("sections")             or profile.get("aliases"):
        return _build_fandom_keys(profile)

    name = profile.get("name", "")
    keys = {name} if name else set()

    birth_name = profile.get("birth_name", "")
    if birth_name and birth_name != name:
        keys.add(birth_name)

    wiki_title = profile.get("wikipedia_title", "")
    if wiki_title and wiki_title != name:
        keys.add(wiki_title)

    # First name / mononym as an extra trigger when it's distinctive enough
    if name and " " in name:
        first = name.split()[0]
        if len(first) >= 4:
            keys.add(first)

    for platform, data in profile.get("social", {}).items():
        handle = data.get("handle", "")
        if not handle or _is_raw_id(handle):
            continue
        keys.add(f"@{handle}")

    for occ in profile.get("occupations", [])[:2]:
        if occ and not re.match(r'^Q\d+$', str(occ)):
            keys.add(occ.lower())

    return sorted(k for k in keys if k and len(k) > 1)


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt_count(n):
    if isinstance(n, int):
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n // 1_000}K"
        return str(n)
    return str(n)


def _clean(vals):
    """Filter out unresolved Wikidata Q-IDs from a list."""
    return [v for v in (vals or []) if v and not re.match(r'^Q\d+$', str(v))]


# ── content builder ───────────────────────────────────────────────────────────

_PACK_WIDTH = 96          # soft line width when merging short facts


def _pack_facts(facts):
    """
    Render infobox rows, merging short ones onto shared lines so a character
    sheet reads as a few dense lines instead of fifteen two-word ones.
    """
    lines, buf = [], []

    def flush():
        if buf:
            lines.append("  |  ".join(buf))
            buf.clear()

    for fact in facts:
        piece = f"{fact['label']}: {fact['value']}"
        if len(piece) > 46:
            flush()
            lines.append(piece)
            continue
        if sum(len(p) + 5 for p in buf) + len(piece) > _PACK_WIDTH:
            flush()
        buf.append(piece)
    flush()
    return lines


def _render_social(profile):
    """'Instagram: @50cent (30.2M followers) | X: @50cent' — or nothing."""
    rendered = []
    for platform, data in (profile.get("social") or {}).items():
        handle = data.get("handle", "")
        if not handle:
            continue
        line = f"{platform.capitalize()}: @{handle}"
        if data.get("scraped"):
            followers = data.get("followers")
            subscribers = data.get("subscribers")
            if followers:
                line += f" ({_fmt_count(followers)} followers)"
            elif subscribers:
                line += f" ({subscribers} subscribers)"
        rendered.append(line)
    return " | ".join(rendered)


def _build_wikipedia_content(profile):
    """
    Render a scraped Wikipedia article as a lorebook entry.

    Same shape as the Fandom renderer — header, facts, lead, sections — since
    both come from wikitext and the structure is what models read reliably.
    The one addition is the live social line, which only a real person has and
    which no encyclopedia article carries.
    """
    lines = []

    name = profile.get("name", "Unknown")
    desc = (profile.get("description") or "").strip()
    header = f"[{name}]"
    if profile.get("category"):
        header += f" — {profile['category']}"
    header += " (Wikipedia)"
    lines.append(header)

    aliases = [re.sub(r"\s*\([^)]*\)", "", a).strip()
               for a in profile.get("aliases", [])]
    aliases = [a for a in aliases if a and a.lower() != name.lower()]
    if aliases:
        lines.append(f"Also known as: {', '.join(dict.fromkeys(aliases))}")

    facts = list(profile.get("facts", []))
    # Wikidata fills the gaps the infobox left rather than repeating it, so a
    # date of birth appears once whichever source supplied it.
    have = {f["label"].lower() for f in facts}
    for label, field in (("Born", "date_of_birth"), ("Height", "height"),
                         ("Nationality", "nationality")):
        value = profile.get(field)
        if value and label.lower() not in have:
            facts.append({"label": label, "value": str(value)[:60]})
    lines += _pack_facts(facts)

    social = _render_social(profile)
    if social:
        lines.append(f"Social: {social}")

    extract = (profile.get("extract") or "").strip()
    if extract:
        lines.append("")
        lines.append(extract)
    elif desc:
        lines.append("")
        lines.append(desc)

    for section in profile.get("sections", []):
        text = (section.get("text") or "").strip()
        heading = "#" * min(section.get("level", 2), 4)
        lines.append("")
        lines.append(f"{heading} {section['title']}")
        if text:
            lines.append(text)

    if profile.get("notes"):
        lines.append("")
        lines.append(f"Notes: {profile['notes']}")

    source = profile.get("page_url") or profile.get("wikipedia_url") or ""
    lines.append("")
    lines.append(f"[Source: {source} · Retrieved {datetime.now():%Y-%m-%d}]")
    return "\n".join(lines).strip()


def _build_fandom_content(profile):
    """
    Render a Fandom page as a lorebook entry.

    Layout is deliberately plain — a header line, the infobox facts, the lead
    paragraph, then the surviving sections as markdown headings. Models read
    that structure reliably, and every part of it is world lore: images,
    references, trivia and production credits were dropped upstream.
    """
    lines = []

    name = profile.get("name", "Unknown")
    category = profile.get("category", "Lore")
    wiki = profile.get("wiki_name", "")

    header = f"[{name}] — {category}"
    if wiki:
        header += f" ({wiki})"
    lines.append(header)

    aliases = [re.sub(r"\s*\([^)]*\)", "", a).strip()
               for a in profile.get("aliases", [])]
    aliases = [a for a in aliases if a and a.lower() != name.lower()]
    if aliases:
        lines.append(f"Also known as: {', '.join(dict.fromkeys(aliases))}")

    facts = [f for f in profile.get("facts", [])
             if f.get("label") not in ("Also known as",)]
    lines += _pack_facts(facts)

    extract = (profile.get("extract") or "").strip()
    if extract:
        lines.append("")
        lines.append(extract)

    for section in profile.get("sections", []):
        text = (section.get("text") or "").strip()
        heading = "#" * min(section.get("level", 2), 4)
        lines.append("")
        lines.append(f"{heading} {section['title']}")
        if text:
            lines.append(text)

    notes = profile.get("notes", "")
    if notes:
        lines.append("")
        lines.append(f"Notes: {notes}")

    source = profile.get("page_url", "")
    stamp = f"[Source: {source} · Retrieved {datetime.now():%Y-%m-%d}]"
    lines.append("")
    lines.append(stamp)

    return "\n".join(lines).strip()


def _build_content(profile):
    """Render a concise, roleplay-useful lore block."""
    lines = []

    name     = profile.get("name", "Unknown")
    desc     = profile.get("description", "")
    category = profile.get("category", "Person")

    header = f"[{name}]"
    if desc:
        header += f" — {desc}"
    lines.append(header)

    occs        = _clean(profile.get("occupations", []))
    genres      = _clean(profile.get("genres", []))
    nationality = profile.get("nationality", "")

    if occs or nationality:
        nat_str = f"{nationality} " if nationality else ""
        occ_str = ", ".join(occs[:3]) if occs else category
        lines.append(f"Category: {nat_str}{occ_str}")

    if genres:
        lines.append(f"Genre(s): {', '.join(genres[:5])}")

    birth_name = profile.get("birth_name", "")
    if birth_name and birth_name != name:
        lines.append(f"Birth name: {birth_name}")

    dob = profile.get("date_of_birth")
    age = profile.get("age")
    pob = profile.get("place_of_birth")

    if dob:
        dob_s = str(dob)[:10]
        if age:
            dob_s += f" (age {age})"
        born_line = f"Born: {dob_s}"
        if pob:
            born_line += f", {pob}"
        lines.append(born_line)

    phys_parts = []
    for label, field in (("Gender", "gender"), ("Height", "height"),
                         ("Skin", "skin_tone"), ("Hair", "hair_color"),
                         ("Eyes", "eye_color")):
        val = profile.get(field, "")
        if val:
            phys_parts.append(f"{label}: {val}")
    if phys_parts:
        lines.append("  |  ".join(phys_parts))

    ethnicity_raw = profile.get("ethnicity")
    if ethnicity_raw:
        eth_list = ethnicity_raw if isinstance(ethnicity_raw, list) else [ethnicity_raw]
        eth_clean = [e for e in eth_list if e and not re.match(r'^Q\d+$', str(e))]
        if eth_clean:
            lines.append(f"Ethnicity: {', '.join(eth_clean)}")

    founded  = profile.get("founded")
    founders = _clean(profile.get("founders", []))
    industry = profile.get("industry", "")

    if founded:
        lines.append(f"Founded: {str(founded)[:10]}")
    if founders:
        lines.append(f"Founder(s): {', '.join(founders[:3])}")
    if industry:
        lines.append(f"Industry: {industry}")

    website = profile.get("official_website", "")
    if website and "wikipedia.org" not in website:
        lines.append(f"Website: {website}")

    social = profile.get("social", {})
    socials_rendered = []
    for platform, data in social.items():
        handle = data.get("handle", "")
        if not handle:
            continue
        s = f"{platform.capitalize()}: @{handle}"
        if data.get("scraped"):
            followers   = data.get("followers")
            subscribers = data.get("subscribers")
            if followers:
                s += f" ({_fmt_count(followers)} followers)"
            elif subscribers:
                s += f" ({subscribers} subscribers)"
        socials_rendered.append(s)

    if socials_rendered:
        lines.append("Social: " + " | ".join(socials_rendered))

    extract = (profile.get("extract") or "").strip()
    if extract:
        sentences = re.split(r"(?<=[.!?])\s+", extract)
        bio = " ".join(sentences[:3])
        if len(bio) > 30:
            lines.append(f"Bio: {bio}")

    notes = profile.get("notes", "")
    if notes:
        lines.append(f"Notes: {notes}")

    lines.append(f"[Retrieved: {datetime.now().strftime('%Y-%m-%d')}]")

    return "\n".join(lines)


# ── entry builder ─────────────────────────────────────────────────────────────

def profile_to_entry(profile, uid=0):
    """Convert one profile dict to a SillyTavern World Info entry."""
    is_fandom = profile.get("source") == "fandom"
    # A Wikipedia profile with sections came from the article scraper; one
    # without came from the search-and-enrich path, which has no article body
    # to render and its own compact layout.
    is_article = (profile.get("source") == "wikipedia"
                  and (profile.get("sections") or profile.get("facts")))

    keys    = _build_keys(profile)
    if is_fandom:
        content = _build_fandom_content(profile)
    elif is_article:
        content = _build_wikipedia_content(profile)
    else:
        content = _build_content(profile)

    name_val = profile.get("name", "")
    category = profile.get("category", "")
    comment  = (f"{category.upper()}: {name_val}" if category else name_val)[:150]

    return {
        "uid":                 uid,
        "key":                 keys,
        "keysecondary":        [],
        "comment":             comment,
        "content":             content,
        "constant":            False,
        "vectorized":          False,
        "selective":           True,
        "selectiveLogic":      0,
        "addMemo":             True,
        "order":               100,
        "position":            0,
        "disable":             False,
        "excludeRecursion":    False,
        "preventRecursion":    False,
        "delayUntilRecursion": False,
        "probability":         100,
        "useProbability":      True,
        "depth":               4,
        "group":               profile.get("group") or category,
        "groupOverride":       False,
        "groupWeight":         100,
        "scanDepth":           None,
        "caseSensitive":       None,
        "matchWholeWords":     None,
        "useGroupScoring":     None,
        "automationId":        "",
        "role":                None,
        "sticky":              0,
        "cooldown":            0,
        "delay":               0,
        "displayIndex":        uid,
        "extensions": {
            "lorebook_meta": {
                "category":        category,
                "source":          "fandom" if is_fandom else "wikipedia",
                "wikidata_qid":    profile.get("wikidata_qid"),
                "wikipedia_title": profile.get("wikipedia_title"),
                "wiki_name":       profile.get("wiki_name"),
                "page_url":        profile.get("page_url"),
                "image":           profile.get("image") or "",
                "scraped_at":      datetime.now().isoformat(),
            },
        },
    }


# ── lorebook builder ──────────────────────────────────────────────────────────

def profiles_to_lorebook(profiles, name="Lorebook", description=""):
    """Convert a list of profiles to a complete SillyTavern World Info JSON object."""
    entries = {
        str(i): profile_to_entry(p, i)
        for i, p in enumerate(profiles)
    }
    return {
        "name":               name,
        "description":        description or f"Auto-generated lorebook ({len(profiles)} entries).",
        "scan_depth":         100,
        "token_budget":       500,
        "recursive_scanning": False,
        "extensions": {
            "generator":  "Lorebook Studio",
            "created_at": datetime.now().isoformat(),
        },
        "entries": entries,
    }
