"""
Sub-series scoping — one story out of a wiki that documents a whole franchise.

A wiki is organised around its franchise, not around whichever part of it you
want to roleplay. The Jujutsu Kaisen wiki holds the 2018 manga *and* its 2025
sequel *Jujutsu Kaisen Modulo*, and nothing separates them: `Category:Characters`
mixes both casts, `Story Arcs` covers only the original, and Modulo's own leads
(Yuka and Tsurugi Okkotsu) carry exactly the same categories as characters who
never appear in it. Filing is no help, so scope has to be *derived*.

What does separate them is the story's own instalments. A spin-off's chapters,
episodes and volumes are unambiguously its own — they are titled after it
("Modulo Chapter 1") and filed under it (`Category:Jujutsu Kaisen Modulo
Chapters`) — and every character, place, technique and faction that matters to
that story is linked from them, usually many times over. So:

    1. work out what the sub-series is called, including its short name
    2. find its instalment pages
    3. read what they link to, with navigation boxes stripped first
    4. rank by how much of the story each thing actually appears in

Step 3 is why this reads wikitext rather than asking the API for a page's
links: a navbox listing all 271 chapters of the parent series is transcluded
onto every one of the spin-off's chapter pages, and `prop=links` cannot tell
that apart from the cast. Stripping navigation templates first is the whole
difference between a clean 60-entry lorebook and a 400-entry franchise dump.

Step 4 is what keeps a walk-on out of the lorebook while keeping the
protagonist in — and it is measured, not guessed: something linked from one
chapter out of twenty-five is background, and something linked from twenty is
the story.
"""

import re

from scrapers import wikitext as W

# Instalment pages are the evidence, never the output — nobody wants 25
# chapter summaries as lorebook entries.
_INSTALMENT_RE = re.compile(
    r"^(chapter|volume|episode|issue|book|part|act|scene|season|arc)\b"
    r"|\b(chapter|volume|episode|issue)\s+\d+", re.I)

# Article subpages holding presentation rather than lore.
_SUBPAGE_RE = re.compile(
    r"/(image gallery|gallery|images?|synopsis|synopses|quotes?|trivia"
    r"|relationships?|abilities|history|appearances?)$", re.I)

# Index pages. They are how a wiki points at its content, not content.
# Only the plural forms: "Chapters" is the index, "Chapter 5" is an instalment
# and is caught by _INSTALMENT_RE instead.
_INDEX_RE = re.compile(
    r"^(list of |lists of )|^(volumes|chapters|episodes|story arcs)\b"
    r"|&\s*chapters?$|^.{0,60}\bwikia?$", re.I)

# A page filed under nothing but these is bookkeeping — the magazine a series
# runs in, the author, the franchise index. Real lore always earns at least
# one category describing what a thing *is*.
_META_ONLY_CATEGORIES = {
    "manga", "anime", "media", "lists", "list", "volumes", "chapters",
    "episodes", "series", "novels", "films", "movies", "games", "video games",
    "soundtracks", "music", "merchandise", "real world", "staff", "authors",
    "publishers", "magazines", "images", "galleries", "browse", "content",
}

# Infobox fields naming the people and companies who *made* the story. They
# are already dropped from entry text; here they are also excluded from its
# cast, because a series infobox links its author and publisher exactly the
# way a chapter links its characters.
_PRODUCTION_FIELD = re.compile(
    r"author|artist|illustrator|publisher|magazine|studio|director|producer"
    r"|writer|composer|creator|network|serial|imprint|licens|editor", re.I)

# Fields where a wiki states outright who is new to the story in this
# instalment — the strongest scope signal there is, when a wiki keeps it.
_DEBUT_FIELD = re.compile(r"new character|debut|introduc|cover|featured", re.I)

MAX_SEEDS = 80            # instalments read to establish scope
MAX_CANDIDATES = 700      # link targets ranked before validation
MAX_RESULTS = 500         # entries offered in the picker

# Share of a story's instalments something must appear in to count as part of
# its core cast rather than a passing mention.
CORE_SHARE = 0.15


# ══════════════════════════════════════════════════════════════════════════
# Identity
# ══════════════════════════════════════════════════════════════════════════

def parent_name(wiki_name):
    """'Jujutsu Kaisen Wiki' → 'Jujutsu Kaisen'."""
    return re.sub(r"\s*\b(wiki|wikia|fandom|encyclopedia|database)\b\s*$", "",
                  wiki_name or "", flags=re.I).strip()


# Words that name a format rather than a story. A title differing from the
# wiki's subject only by one of these is that subject, not a spin-off.
_FORMAT_WORD = re.compile(
    r"^(manga|anime|film|movie|novel|light novel|game|video game|comic|series"
    r"|tv series|tv|live[- ]action|animated|book|audio drama|musical|manhwa"
    r"|webtoon|ova|special|franchise|season \d+|\d{4} film|\d{4})$", re.I)


def _usable(name):
    """Reject names too short, too numeric or too generic to search on."""
    name = (name or "").strip(" :-–—")
    if len(name) < 3 or name.isdigit() or _FORMAT_WORD.match(name):
        return ""
    return name


def short_names(title, parent):
    """
    The names a wiki might actually file a sub-series' pages under, best first.

    Wikis title a spin-off in full on its own page and abbreviate it
    everywhere else, but which part they keep varies with how the title is
    built:

      "Jujutsu Kaisen Modulo"          → chapters are "Modulo Chapter 1"
      "My Hero Academia: Vigilantes"   → the tail names the story
      "Boruto: Naruto Next Generations"→ the *head* names it

    Guessing wrong costs one fruitless request, so all the plausible forms are
    tried and whichever finds instalments wins.
    """
    # "Blue Lock (Manga)" is not a spin-off called "(Manga)" — it is the wiki's
    # own subject, published in one of its formats. Left in, the qualifier
    # became the sub-series name and scoping searched the wiki for pages
    # beginning "(Manga)", which found fifteen of its two hundred characters.
    title = re.sub(r"\s*\([^)]*\)\s*$", "", (title or "")).strip()
    found = []

    if parent:
        m = re.match(rf"^{re.escape(parent)}\s*[:\-–—]?\s+(.+)$", title, re.I)
        if m:
            found.append(m.group(1))

    # A subtitled title names the work on one side of the colon and describes
    # it on the other; which side is which depends on the franchise.
    if ":" in title:
        head, tail = title.split(":", 1)
        found += [tail, head]

    seen, out = set(), []
    for name in found:
        name = _usable(name)
        low = name.lower()
        if not name or low in seen or low == (parent or "").lower():
            continue
        seen.add(low)
        out.append(name)
    return out


def short_name(title, parent):
    """The single best short name, or "" when the title yields none."""
    names = short_names(title, parent)
    return names[0] if names else ""


def identity(title, wiki_name, raw):
    """
    Everything needed to recognise one sub-series' own pages.

    Returns {title, parent, short, aliases, production}.
    """
    parent = parent_name(wiki_name)

    # A trailing qualifier disambiguates one telling of a story from another —
    # "My Hero Academia: Vigilantes (Anime)" beside the manga page of the same
    # name. It is not part of what the story is called, and leaving it in
    # searches the wiki for pages beginning "Vigilantes (Anime)", which is
    # nothing: pasting the anime page found fourteen characters where the
    # manga page found several hundred. Both are the same story, so both must
    # scope the same way.
    base = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip() or title

    shorts = short_names(base, parent)
    short = shorts[0] if shorts else ""

    aliases = {title, base} | set(shorts)

    _, fields = W.find_infobox(raw or "")
    production = set()
    for field, value in fields:
        key = re.sub(r"[\s_\-]+", " ", field).strip()
        if _PRODUCTION_FIELD.search(key):
            production.update(t for t, _ in W.link_targets(value))
        elif key.lower() in ("name", "romaji", "english", "title"):
            text = W.clean_inline(value).strip()
            if text and len(text) <= 80:
                aliases.add(text)

    return {"title": title, "parent": parent, "short": short,
            "shorts": shorts, "aliases": {a for a in aliases if a},
            "production": production}


# ══════════════════════════════════════════════════════════════════════════
# Finding the sub-series' own instalments
# ══════════════════════════════════════════════════════════════════════════

def _prefix_pages(call, prefix):
    """Namespace-0 pages whose title starts with `prefix` as a whole word."""
    try:
        data = call({"action": "query", "list": "allpages",
                     "apprefix": prefix, "apnamespace": 0, "aplimit": 500})
    except Exception:
        return []
    out = []
    for page in data.get("query", {}).get("allpages", []):
        title = page.get("title", "")
        rest = title[len(prefix):]
        # "Modulo Chapter 1" is the sub-series; "Moduloid" merely starts the
        # same way and belongs to somebody else's story.
        if rest and not rest[0].isspace() and rest[0] not in ":-–—":
            continue
        out.append(title)
    return out


def _scoped_categories(call, ident):
    """Categories whose *name* contains the sub-series name — its own filing."""
    names = []
    for alias in sorted(ident["aliases"], key=len, reverse=True):
        try:
            data = call({"action": "query", "list": "allcategories",
                         "acprefix": alias, "aclimit": 100, "acprop": "size"})
        except Exception:
            continue
        for cat in data.get("query", {}).get("allcategories", []):
            name = cat.get("category") or cat.get("*", "")
            if name and cat.get("pages", 0):
                names.append(name)
    return list(dict.fromkeys(names))


def discover(call, members, ident):
    """
    Everything on the wiki that belongs to this sub-series, split by what it
    is good for. Returns {"seeds": […], "direct": […]}.

    Wikis take one of two approaches, and both have to work:

      * **Filed** — the Attack on Titan wiki puts the *No Regrets* cast in
        `Category:No Regrets` and disambiguates their titles ("Levi (No
        Regrets)"). The pages are simply told to us; nothing needs inferring.
      * **Mixed in** — the Jujutsu Kaisen wiki files *Modulo*'s characters in
        the same `Category:Characters` as everyone else's, and only its
        chapters carry the sub-series' name. There the chapters are evidence
        rather than content, and the cast has to be read out of them.

    So the discovered pages are split: instalments become `seeds` to measure
    scope against, and everything else becomes `direct` — pages the wiki has
    already stated belong to this story.
    """
    found = []
    for alias in sorted(ident["aliases"], key=len, reverse=True):
        found += _prefix_pages(call, alias)

    for name in _scoped_categories(call, ident):
        try:
            found += members(name)
        except Exception:
            continue

    pages, seen = [], set()
    for title in found:
        if title in seen or title in ident["aliases"] or title == ident["title"]:
            continue
        if _SUBPAGE_RE.search(title):
            continue
        seen.add(title)
        pages.append(title)

    # A short name that is also a character's name matches far more than the
    # story's instalments — prefix-searching Narutopedia for "Boruto" returns
    # Boruto Uzumaki and every technique named after him. Splitting on shape
    # keeps those out of the evidence base: measuring a story's scope against
    # a character's own article would only re-derive that character.
    seeds = [t for t in pages if _INSTALMENT_RE.search(t)]
    direct = [t for t in pages if not _INSTALMENT_RE.search(t)]
    return {"seeds": sample_instalments(seeds, MAX_SEEDS),
            "direct": direct[:MAX_RESULTS]}


def _natural_key(title):
    """Sort 'Chapter 2' before 'Chapter 10' — publication order, not ASCII."""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", title)]


def sample_instalments(seeds, limit):
    """
    At most `limit` instalments, spread evenly across the whole story.

    Reading the first sixty of a hundred and twenty-eight chapters measures
    only the first half of the story: a character who arrives for the finale
    is linked from none of them and ranks as a walk-on, or is cut altogether.
    Taking every other chapter instead costs the same number of requests and
    covers the beginning, middle and end alike.

    Sorting matters as much as sampling — "Chapter 10" sorts before
    "Chapter 2" as text, so an unsorted head is not even the first half of
    anything in particular.
    """
    seeds = sorted(dict.fromkeys(seeds), key=_natural_key)
    if len(seeds) <= limit:
        return seeds
    step = len(seeds) / float(limit)
    picked = [seeds[int(i * step)] for i in range(limit)]
    # The finale is where a story introduces its last names and resolves its
    # factions, so it is worth keeping whatever the arithmetic lands on.
    if seeds[-1] not in picked:
        picked[-1] = seeds[-1]
    return list(dict.fromkeys(picked))


# ══════════════════════════════════════════════════════════════════════════
# Harvesting
# ══════════════════════════════════════════════════════════════════════════

def harvest(records, seeds, ident):
    """
    Count what the sub-series' instalments point at.

    Returns {target: {"seeds": n, "mentions": n, "debut": bool}} where `seeds`
    is how many instalments link to it at all — the number that actually
    measures presence in the story. `mentions` only breaks ties, because a
    single chapter naming someone twelve times says less about the whole story
    than twelve chapters naming them once.
    """
    tally = {}

    def bump(target, key, amount=1):
        row = tally.setdefault(target, {"seeds": 0, "mentions": 0,
                                        "debut": False})
        if key == "debut":
            row["debut"] = True
        else:
            row[key] += amount

    for title in seeds:
        record = records.get(title)
        if not record or record.get("missing"):
            continue
        raw = record.get("wikitext") or ""

        for target in W.infobox_links(raw, _DEBUT_FIELD.pattern):
            bump(target, "debut")

        seen = set()
        for target, _ in W.link_targets(W.strip_noise_templates(raw)):
            bump(target, "mentions")
            seen.add(target)
        for target in seen:
            bump(target, "seeds")

    return tally


def _plausible(title, ident, seedset):
    """Could this link target be a lorebook entry at all?"""
    if title in seedset or title in ident["aliases"] or title in ident["production"]:
        return False
    if ident["parent"] and title.lower() == ident["parent"].lower():
        return False
    if _SUBPAGE_RE.search(title) or _INDEX_RE.search(title):
        return False
    if _INSTALMENT_RE.search(title):
        return False
    if ":" in title.split(" ")[0]:          # File:, Category:, Wikipedia:
        return False
    return True


def _meta_category(name):
    """
    True for a category that files pages by *publication* rather than by what
    they are.

    Matching the last word as well as the whole name is what catches a wiki
    that qualifies the bucket with the story's name — "No Regrets Chapters"
    indexes chapters just as surely as "Chapters" does.
    """
    name = re.sub(r"[\s_\-]+", " ", name or "").strip().lower()
    if not name:
        return True
    if name in _META_ONLY_CATEGORIES:
        return True
    return name.rsplit(" ", 1)[-1] in _META_ONLY_CATEGORIES


# Buckets that identify a page as an instalment outright, whatever else it is
# filed under. Unlike the softer meta list, one of these is enough on its own.
# "arcs" is deliberately absent. A chapter is an instalment and nobody wants
# twenty-five chapter summaries as entries, but an arc page is a summary of a
# named stretch of the story — "Naruhata Lockdown Arc" — which is exactly the
# kind of event a lorebook is for. Wikis that file chapters under "<Story> Arc
# Chapters" are still caught, by the last word.
_INSTALMENT_CATEGORIES = {"chapters", "episodes", "volumes", "issues",
                          "seasons"}

# Buckets that identify a page as something made *about* the story rather than
# something in it. One is enough, because these pages also carry a category
# naming the story they belong to — the Vigilantes theme songs are filed under
# both "Music" and "My Hero Academia: Vigilantes (Anime)", so requiring every
# category to be meta let all four of them into the cast list.
_PRODUCTION_CATEGORIES = {
    "music", "songs", "soundtracks", "albums", "singles", "themes",
    "opening themes", "ending themes", "openings", "endings", "theme songs",
    "insert songs", "character songs", "discography",
    "merchandise", "merch", "toys", "figures", "products", "goods",
    "video games", "games", "apps", "art books", "guidebooks", "databooks",
    "actors", "actresses", "voice actors", "voice actresses", "seiyuu",
    "cast", "crew", "staff", "directors", "producers", "writers",
    "magazines", "publishers", "imprints",
    "images", "galleries", "videos", "screenshots", "artwork", "posters",
    "dvds", "blu-rays", "home video", "stage plays", "live action",
    "real world", "trivia", "polls", "quotes",
}


def _is_meta(categories):
    """
    True when a page is bookkeeping rather than lore.

    Either every category is franchise admin, or one of them files the page as
    an instalment — a chapter titled "Those Three" gives nothing away in its
    name, but `Category:No Regrets Chapters` settles it.
    """
    if not categories:
        return True
    for raw in categories:
        name = re.sub(r"[\s_\-]+", " ", raw or "").strip().lower()
        name = re.sub(r"\s*\([^)]*\)", "", name).strip()
        if name in _PRODUCTION_CATEGORIES:
            return True
        if name.rsplit(" ", 1)[-1] in _INSTALMENT_CATEGORIES:
            return True
    return all(_meta_category(c) for c in categories)


# ══════════════════════════════════════════════════════════════════════════
# Ranking
# ══════════════════════════════════════════════════════════════════════════

def rank(tally, ident, seeds, categories, valid, direct=()):
    """
    Turn raw counts into ranked, classified candidates.

    `categories` maps a canonical title to its wiki categories; `valid` is the
    set of titles that turned out to be real pages; `direct` are pages the
    wiki itself filed under the sub-series. Red links are dropped here — a
    wiki links names it has never written an article for, and those would
    import as empty entries.
    """
    from core.classifier import classify_fandom      # local: avoids a cycle

    total = max(1, len(seeds))
    core_at = max(2, round(total * CORE_SHARE))
    direct = set(direct)

    ranked = []
    for title, row in tally.items():
        if title not in valid:
            continue
        cats = categories.get(title, [])
        if _is_meta(cats):
            continue

        # Being filed under the story outranks any amount of counting: the
        # wiki is stating membership, not hinting at it.
        filed = title in direct
        score = (row["seeds"] * 3.0
                 + row["mentions"] * 0.4
                 + (12.0 if row["debut"] else 0.0)
                 + (30.0 if filed else 0.0))
        if filed or row["debut"] or row["seeds"] >= core_at:
            tier = "core"
        elif row["seeds"] >= 2:
            tier = "supporting"
        else:
            tier = "background"

        ranked.append({
            "title":    title,
            "group":    classify_fandom("", cats, title, ""),
            "tier":     tier,
            "seeds":    row["seeds"],
            "mentions": row["mentions"],
            "score":    round(score, 1),
        })

    ranked.sort(key=lambda c: (-c["score"], c["title"]))
    return ranked[:MAX_RESULTS]


# Groups shown first — a lorebook is mostly people and places.
_GROUP_ORDER = ["Character", "Species", "Organization", "Location", "Event",
                "Ability", "Item", "Lore", "Media"]
_GROUP_LABEL = {
    "Character": "Characters", "Location": "Locations", "Event": "Events",
    "Organization": "Organizations", "Ability": "Abilities & Techniques",
    "Item": "Items", "Species": "Species", "Lore": "Lore & Terminology",
    "Media": "Media",
}


def to_browse(ranked, ident, ref, info, seeds):
    """
    Shape ranked candidates like a browse result so the existing picker shows
    them — grouped into chips, with background detail hidden behind the same
    "include minor" toggle a film's manifest uses.
    """
    buckets = {}
    for item in ranked:
        buckets.setdefault(item["group"], []).append(item)

    order = ([g for g in _GROUP_ORDER if g in buckets]
             + sorted(g for g in buckets if g not in _GROUP_ORDER))

    groups = []
    for name in order:
        items = buckets[name]
        groups.append({
            "category":  _GROUP_LABEL.get(name, name),
            "total":     len(items),
            "pages":     [i["title"] for i in items],
            "minor":     [i["title"] for i in items
                          if i["tier"] == "background"],
            "notes":     {i["title"]: f"in {i['seeds']} of {len(seeds)}"
                          for i in items if i["tier"] != "core"},
            "tiers":     {i["title"]: i["tier"] for i in items},
            "subgroups": [],
        })

    return {
        "wiki_name":   info.get("name", ""),
        "site":        ref.get("site", ""),
        "api":         ref.get("api", ""),
        "main_page":   info.get("main_page"),
        "source_page": ident["title"],
        "scope": {
            "series":     ident["title"],
            "short":      ident["short"],
            "parent":     ident["parent"],
            "instalments": len(seeds),
            "found":      len(ranked),
        },
        "groups": groups,
    }


def merge_redirects(tally, canonical):
    """
    Fold alias counts onto the page they redirect to.

    Wikis link characters by whatever name fits the sentence — "Maru", "Cross",
    "Usami" — and each of those is a redirect to the full article. Left
    unmerged they look like a dozen minor walk-ons instead of one main
    character mentioned throughout, which is exactly backwards.
    """
    merged = {}
    for title, row in tally.items():
        target = canonical.get(title, title)
        into = merged.setdefault(target, {"seeds": 0, "mentions": 0,
                                          "debut": False})
        into["seeds"] += row["seeds"]
        into["mentions"] += row["mentions"]
        into["debut"] = into["debut"] or row["debut"]
    return merged
