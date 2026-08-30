"""
Keyword-based entity classifier.
Returns a category string like "Musician", "Brand", etc.

v2 improvements over the original:
* Weighted signals — a keyword hit in the Wikidata *occupation* list counts
  far more than one buried in a Wikipedia category name.
* More categories (Scientist, Writer, Comedian, Model, Gamer, Location, Media).
* Word-boundary matching so "dj" doesn't fire on "Djibouti".
"""

import re

CATEGORY_KEYWORDS = {
    "Musician": [
        "rapper", "singer", "musician", "songwriter", "hip-hop", "hip hop",
        "r&b", "pop artist", "recording artist", "vocalist", "band",
        "record producer", "dj", "beatmaker", "music artist", "discography",
        "album", "single", "record label", "composer", "guitarist", "drummer",
        "pianist",
    ],
    "Actor": [
        "actor", "actress", "film", "television", "movie", "tv series", "cast",
        "hollywood", "filmmaker", "film director", "performer", "stage actor",
        "voice actor", "screenwriter",
    ],
    "Influencer": [
        "youtuber", "content creator", "blogger", "vlogger", "twitch streamer",
        "social media personality", "influencer", "tiktok star",
        "instagram model", "streamer", "podcaster", "internet personality",
    ],
    "Athlete": [
        "athlete", "basketball", "football", "soccer", "tennis", "nba", "nfl",
        "mlb", "sports", "swimmer", "gymnast", "boxer", "mma", "olympic",
        "footballer", "baseball", "hockey", "rugby", "cricket", "golfer",
        "sprinter", "wrestler", "racing driver",
    ],
    "Brand": [
        "brand", "company", "corporation", "inc.", "ltd", "enterprise",
        "conglomerate", "headquarters", "ceo", "products", "services",
        "retail", "manufacturer", "startup", "organization", "business",
        "multinational", "fashion house",
    ],
    "Politician": [
        "politician", "president", "senator", "congress", "parliament",
        "minister", "governor", "mayor", "political", "party leader",
        "diplomat", "ambassador", "statesperson", "head of state",
    ],
    "Character": [
        "fictional", "character", "animated", "manga", "anime",
        "game character", "protagonist", "villain", "comic book",
        "novel character", "superhero",
    ],
    "Scientist": [
        "scientist", "physicist", "chemist", "biologist", "mathematician",
        "researcher", "inventor", "engineer", "astronomer", "professor",
        "computer scientist", "nobel",
    ],
    "Writer": [
        "writer", "author", "novelist", "poet", "journalist", "essayist",
        "playwright", "columnist", "editor",
    ],
    "Comedian": [
        "comedian", "comic", "stand-up", "standup", "sketch comedy",
        "satirist", "humorist",
    ],
    "Model": [
        "model", "supermodel", "fashion model", "runway", "vogue cover",
    ],
    "Gamer": [
        "esports", "professional gamer", "pro gamer", "gaming", "speedrunner",
        "let's play",
    ],
    "Location": [
        "city", "country", "capital", "region", "municipality", "landmark",
        "mountain", "river", "island", "district", "state", "province",
        "neighborhood", "borough",
    ],
    "Media": [
        "tv show", "television series", "video game", "film series",
        "franchise", "anime series", "manga series", "novel", "album",
        "streaming series", "sitcom",
    ],
}

# Compile each keyword to a word-boundary regex once, at import time.
_COMPILED = {
    cat: [re.compile(r"\b" + re.escape(kw) + r"\b") for kw in kws]
    for cat, kws in CATEGORY_KEYWORDS.items()
}

# How much each text source is worth per keyword hit.
_W_OCCUPATION = 4   # Wikidata occupation — the strongest signal
_W_DESC = 2         # Wikipedia short description
_W_OTHER = 1        # name + Wikipedia categories


def classify_entity(name, description="", categories=None, occupations=None):
    """
    Score entity text against keyword lists and return the best-match category.
    Falls back to "Person" if no keywords match.
    """
    if isinstance(occupations, str):
        occupations = [occupations]

    sources = [
        (" ".join(occupations or []).lower(), _W_OCCUPATION),
        ((description or "").lower(), _W_DESC),
        (((name or "") + " " + " ".join(categories or [])).lower(), _W_OTHER),
    ]

    scores = {}
    for cat, patterns in _COMPILED.items():
        total = 0
        for text, weight in sources:
            if not text:
                continue
            total += weight * sum(1 for p in patterns if p.search(text))
        scores[cat] = total

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Person"


# ══════════════════════════════════════════════════════════════════════════
# Fandom / fictional-world classification
# ══════════════════════════════════════════════════════════════════════════
#
# Wikipedia categories describe the real world; a Fandom wiki describes a
# fictional one, so it needs its own vocabulary. The infobox template name is
# the strongest signal by far — a page using {{Character Infobox}} is a
# character no matter what its prose says — with wiki categories as backup.

FANDOM_CATEGORIES = {
    "Character": [
        "character", "protagonist", "antagonist", "villain", "hero",
        "person", "people", "individual", "biography", "sorcerer",
        "student", "ninja", "pirate", "wizard", "witch", "knight",
        "demon", "human", "cast member", "npc", "crew", "companion",
    ],
    "Location": [
        "location", "place", "region", "city", "town", "village", "country",
        "planet", "world", "realm", "building", "school", "landmark",
        "territory", "map", "area", "dungeon", "kingdom", "continent",
        "settlement", "castle", "fortress", "stronghold", "celestial",
        "geography", "terrain", "island", "forest", "mountain", "temple",
        "ruins", "station", "district", "province",
    ],
    "Organization": [
        "organization", "organisation", "group", "faction", "clan", "family",
        "team", "guild", "squad", "army", "council", "order", "society",
        "corporation", "house", "division", "unit",
    ],
    "Event": [
        "event", "arc", "saga", "war", "battle", "tournament", "incident",
        "story arc", "conflict", "invasion", "raid", "timeline", "era",
    ],
    "Ability": [
        "technique", "ability", "power", "magic", "spell", "jutsu", "skill",
        "quirk", "curse", "attack", "move", "style", "form", "transformation",
        "domain expansion", "devil fruit", "semblance",
    ],
    "Species": [
        "species", "race", "creature", "monster", "beast", "animal", "deity",
        "god", "spirit", "demon race", "tribe", "bloodline",
    ],
    "Item": [
        "item", "weapon", "artifact", "artefact", "object", "equipment",
        "armor", "armour", "tool", "relic", "treasure", "vehicle", "food",
        "potion", "material",
    ],
    "Lore": [
        "terminology", "term", "concept", "lore", "history", "culture",
        "religion", "language", "law", "rule", "system", "glossary",
        "mythology", "legend", "prophecy",
    ],
    "Media": [
        "series", "manga", "anime", "novel", "film", "movie", "show",
        "franchise", "season", "book", "comic",
    ],
}

# Wiki categories and infobox names are usually plural ("Characters",
# "Story Arcs Infobox"), so every keyword matches its -s form too.
_FANDOM_COMPILED = {
    cat: [re.compile(r"\b" + re.escape(kw) + r"s?\b") for kw in kws]
    for cat, kws in FANDOM_CATEGORIES.items()
}

def _best_fandom_match(text):
    """Highest-scoring category for one piece of text, or None."""
    if not text:
        return None
    scores = {cat: sum(1 for p in patterns if p.search(text))
              for cat, patterns in _FANDOM_COMPILED.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


# A category is a stronger claim about a page when it names the kind of thing
# outright than when it merely contains the word.
_EXACT_CATEGORY_WEIGHT = 3
_LOOSE_CATEGORY_WEIGHT = 1

# "Lore" is the fallback bucket, so a category that names a concrete kind of
# thing outranks it whenever both fit equally well.
_GENERIC_FANDOM = "Lore"


def _match_category_list(categories):
    """
    Classify a page from its wiki categories, weighing each one separately.

    Joining the categories into one string loses the distinction that decides
    these cases. "Jujutsu Sorcerer" is filed under *Jujutsu Sorcerers* and
    *Terminology*: the first only contains the word "sorcerer" while the second
    *is* the word "terminology", and the page is indeed a glossary entry about
    the role rather than somebody who holds it. Scoring an exact category name
    above a substring hit gets that right without needing to know the wiki.
    """
    scores = {}
    for raw in categories or []:
        # A parenthetical in a category name disambiguates it; it never says
        # what the page is. The Attack on Titan wiki files a spin-off's cast
        # under "Characters (No Regrets Manga)", and left in place that
        # trailing "Manga" votes for Media on every category the page has —
        # enough to outvote the word "Characters" itself.
        name = re.sub(r"\s*\([^)]*\)", " ", raw)
        name = re.sub(r"[\s_\-]+", " ", name).strip().lower()
        if not name:
            continue
        singular = name[:-1] if name.endswith("s") else name
        for cat, patterns in _FANDOM_COMPILED.items():
            hits = sum(1 for p in patterns if p.search(name))
            if not hits:
                continue
            exact = any(kw in (name, singular) for kw in FANDOM_CATEGORIES[cat])
            scores[cat] = scores.get(cat, 0) + (
                _EXACT_CATEGORY_WEIGHT if exact else _LOOSE_CATEGORY_WEIGHT)

    if not scores:
        return None
    top = max(scores.values())
    tied = [c for c, s in scores.items() if s == top]
    if len(tied) > 1:
        tied = [c for c in tied if c != _GENERIC_FANDOM] or tied
    return tied[0]


def classify_fandom(infobox_type="", wiki_categories=None, name="", lead=""):
    """
    Pick a lorebook category for a Fandom page.

    The signals are tried in order of trust rather than added together: a page
    built on {{Character Infobox}} is a character full stop, and only if the
    infobox says nothing useful do the wiki's own categories, then the opening
    prose, get a vote. Prose alone is unreliable here — a wizard's biography
    is thick with the words "magic", "curse" and "power" without being a page
    about any of them.

    Falls back to "Lore", a neutral bucket that still reads sensibly in an
    entry header.
    """
    # Infobox templates are often run-together words — {{SkyrimLocations}},
    # {{CelestialBody}} — so split them apart before matching.
    box = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", infobox_type or "").lower()

    return (_best_fandom_match(box)
            or _match_category_list(wiki_categories)
            or _best_fandom_match(f"{name or ''} {(lead or '')[:400]}".lower())
            or "Lore")
