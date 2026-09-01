"""
Fandom (fandom.com / wikia.org) scraper.

Fandom runs MediaWiki, so every wiki exposes `/api.php` — no HTML scraping, no
API key, no AI. We pull raw wikitext and filter it structurally in
`scrapers.wikitext`, which is what keeps images, references, "Trivia",
"Voice Actors" and merchandise out of the finished lorebook.

Two entry points:

  scrape_page(url)   one article  → a lorebook-ready profile
  browse_wiki(url)   a wiki root  → its lore categories and their pages,
                                    so a whole world can be imported at once
"""

import html
import re
import time
from urllib.parse import quote, unquote, urlsplit, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scrapers import wikitext as W
from scrapers import scope

HEADERS = {"User-Agent": "LorebookStudio/1.0 (educational/personal use)"}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
# Fandom rate-limits a busy client with 429s. Backing off further and obeying
# the Retry-After header it sends is the difference between a slow import and a
# failed one.
_retry = Retry(total=5, backoff_factor=1.2,
               status_forcelist=(429, 500, 502, 503, 504),
               respect_retry_after_header=True,
               allowed_methods=("GET",))
SESSION.mount("https://", HTTPAdapter(max_retries=_retry, pool_maxsize=8))

TIMEOUT = 20
MAX_TITLES_PER_CALL = 40      # MediaWiki allows 50 for anonymous clients
MAX_BULK_PAGES = 100          # guard rail for one "add everything" click
MAX_SUBGROUPS = 12            # sub-filters offered per category
MAX_CATEGORY_CALLS = 130      # ceiling on requests spent indexing one wiki
MAX_EXTRA_CATEGORIES = 26     # big categories to surface beyond the known names
MIN_EXTRA_CATEGORY = 5        # …as long as they hold at least this many pages

# Roughly one lorebook entry's worth of prose. SillyTavern budgets tokens per
# entry, so a 90 KB arc page has to be cut down to something usable.
DEFAULT_BUDGET = 4200
LEAD_BUDGET = 900

# Many wikis split a long article across tabbed subpages rather than letting
# one page grow to 100 KB. Satoru Gojo's own page covers what he is like and
# what he can do; everything that *happens* to him lives on
# "Satoru Gojo/Synopsis", which is twice the size and the better half for
# roleplay. These are tried for every page, in the same batched request — a
# subpage that does not exist simply comes back missing, and costs nothing.
SUBPAGE_SUFFIXES = ("Synopsis", "History", "Relationships", "Abilities",
                    "Plot", "Story", "Biography", "Personality")

# Sweeping a wiki's article titles is how subpages are found *exactly* rather
# than guessed at: one page of 500 titles per request, and a mid-sized wiki is
# a handful of requests. Past this ceiling the wiki is too big to index and the
# guess-and-batch fallback is used instead.
MAX_INDEX_CALLS = 40

# …and the article count past which the sweep is not even started. Asking the
# wiki its size first costs one request; discovering the same thing by running
# into MAX_INDEX_CALLS costs forty.
MAX_INDEX_ARTICLES = 20000

# Tabs that hold presentation rather than story. "Profile" — or "Info", or
# "Overview", depending on the wiki — is the main page itself, and a gallery is
# exactly what the wikitext filters already drop.
_SUBPAGE_NOISE = re.compile(
    r"^(profile|main|info|infobox|overview|about|home|image gallery|gallery"
    r"|images?|videos?|screenshots?"
    r"|artwork|trivia|quotes?|merchandise|references?|navigation|stats?"
    r"|gallery of .*|manga gallery|anime gallery)$", re.I)

# Share of an entry's budget given to its subpages. They are additional to the
# main page rather than competing with it, so an entry with a synopsis is
# bigger than one without rather than being the same size with less detail.
SUBPAGE_BUDGET_SHARE = 0.8

# Of that, the share reserved for the story tab when a page also has others.
STORY_TAB_SHARE = 0.7

# Room each arc of a story is guaranteed, however many arcs there are. Enough
# for what happened to this character in that arc, rather than a clause of it.
MIN_ARC_BUDGET = 420

# Below this, a heading that appears on only one page is that page's own
# layout rather than a part of the story worth filtering by.
MIN_ARC_CHARS = 700

# Tabs holding one section per person the subject knows. On a well-kept wiki
# this is as long as the synopsis, and it is the single most useful thing in a
# roleplay lorebook — who someone is to everybody else.
_RELATION_TAB = re.compile(r"\brelationships?\b", re.I)


class _NotRelationTab:
    """Matches every tab that is *not* the relationships one."""


    @staticmethod
    def search(name):
        return None if _RELATION_TAB.search(name or "") else True


_NOT_RELATION_TAB = _NotRelationTab()


# Tabs whose sections are chapters of the story, and so are what the arc
# chooser filters. Wikis name this tab differently — Jujutsu Kaisen calls it
# "Synopsis", Kaiju No. 8 calls it "Plot" — which is why the tab bar is read
# rather than a fixed name looked for. Other tabs (a Relationships page, one
# section per person) are kept in full: they are lore, not story, and hiding
# them behind an arc filter would be wrong.
# Matched anywhere in the tab name, not just as the whole of it, because the
# name is the wiki's own choice — "Story Arcs", "Character History",
# "Plot Summary" and "Biography & History" all hold the same thing.
_STORY_TAB = re.compile(
    r"\b(plot|synopsis|synopses|story|storyline|histor\w*|biograph\w*|background"
    r"|chronolog\w*|timeline|past|narrative|journey|events?|appearances?|arcs?)\b",
    re.I)

_HOST_RE = re.compile(r"^(?:[\w-]+\.)*(?:fandom\.com|wikia\.org|wikia\.com)$", re.I)
_LANG_RE = re.compile(r"^/([a-z]{2,3}(?:-[a-z]{2,4})?)(/.*)?$", re.I)
_RESERVED_PREFIX = re.compile(
    r"^(category|special|user|talk|user talk|file|template|help|forum|board|"
    r"blog|message wall|thread|project|mediawiki|module)\s*:", re.I)

# Categories that are housekeeping rather than lore. Wikis carry a lot of
# these — maintenance buckets, parser-function tracking categories, redirect
# indexes — and none of them describe the world.
_NOISE_CATEGORY = re.compile(
    r"(images?|gallery|galleries|screenshots?|artwork|blog posts?|stubs?"
    r"|candidates?|browse|contents?|site maintenance|maintenance|templates?"
    r"|wikia?|policy|policies|disambiguation|needs? |unreleased|videos?"
    r"|files?|to ?do|cleanup|pages using|pages with|parser (function|tag)"
    r"|dynamicpagelist|redirects?|articles? |documentation|modules?"
    r"|navigation|sysop|users?|protected|spoilers?|expansion|lists?"
    r"|cast|actors?|actresses|crew|staff|voice|portrayals?|merchandise"
    # The people who made the story and the formats it was published in. A
    # lorebook is about the world, so its author is as out of place in it as
    # its Blu-ray release — the My Hero Academia wiki offered "Authors" as a
    # five-page category of real people beside its cast.
    r"|authors?|mangaka|illustrators?|writers?|editors?|animators?|studios?"
    # Shelves of merchandise and side publications. They are works in their own
    # right — the step-1 chooser lists them — but they are not lore, and the
    # Demon Slayer wiki offered eight paint books beside its cast.
    r"|art ?books?|paint books?|setting books?|character books?|fan ?books?"
    r"|colou?ring books?|novelizations?|novelisations?|calendars?"
    r"|spin[- ]?offs?|chronicles series|guide ?books?|data ?books?"
    # Format names have to match the whole category, not a word inside it:
    # "Anime Original Quirks" are in-universe powers and belong in a lorebook,
    # while a category called simply "Anime" is a list of episodes.
    r"|^(?:anime|manga|light novels?|stage plays?|drama cds?|radio dramas?"
    r"|novels?|films?|movies?|media|albums?|games?|anthology|specials?"
    r"|omake|spin[- ]?offs?|crossovers?)$"
    r"|citations? needed|needed|needs "
    r"|soundtracks?|music|songs?|posters?|promotional|behind the scenes"
    r"|real[- ]world|media subpage|subpages?|trivia|quotes?|theor(y|ies)"
    r"|polls?|fan ?fiction|fanon|community|forum|discussions?"
    # Per-instalment indexes: hundreds of pages, and the story they cover is
    # already in the arc and season categories.
    r"|chapters?|episodes?|volumes?|synops[ei]s"
    r"|bd ?& ?dvd|blu[- ]?ray|dvds?|home video"
    r"|theme songs?|opening themes?|ending themes?|openings|endings"
    # Attribute buckets that just re-slice the character list
    r"|\b(male|female|deceased|alive|living)\b"
    # "Maki Zenin Battles", "Mahito Techniques" — one index per character,
    # duplicating pages already reachable from the character itself.
    r"|\w\s(battles|techniques|abilities|relationships|appearances)$)", re.I)

# Categories worth offering when importing a whole wiki, best first.
LORE_CATEGORIES = [
    "Characters", "Character", "Protagonists", "Antagonists", "Villains",
    "Locations", "Location", "Places", "Countries", "Cities", "Towns",
    "Villages", "Planets", "Worlds", "Realms", "Regions",
    "Organizations", "Organisations", "Groups", "Factions", "Clans",
    "Families", "Teams", "Guilds", "Schools", "Houses",
    "Species", "Races", "Creatures", "Monsters", "Beasts", "Deities", "Gods",
    "Events", "Arcs", "Story Arcs", "Sagas", "Wars", "Battles",
    "Techniques", "Abilities", "Powers", "Magic", "Spells", "Skills",
    "Items", "Weapons", "Artifacts", "Objects", "Equipment",
    "Terminology", "Concepts", "Lore", "History", "Culture", "Religions",
    "Languages", "Timeline",
    # Kinds a wiki organised around a game-like world keeps, which no amount of
    # looking at the biggest categories will reach: Solo Leveling's "Quests"
    # holds seven pages and lost its place to twenty-six larger ones.
    "Quests", "Missions", "Dungeons", "Gates", "Titles", "Ranks", "Classes",
    "Guilds", "Shadows", "Tribes", "Bloodlines", "Currencies", "Systems",
]


# ══════════════════════════════════════════════════════════════════════════
# URL handling
# ══════════════════════════════════════════════════════════════════════════

class FandomError(Exception):
    """Raised for URLs or pages Lorebook Studio cannot turn into an entry."""


def parse_fandom_url(raw):
    """
    Split a Fandom URL into the pieces the API needs.

    Returns {api, site, host, lang, title, kind} where kind is
    "article", "category" or "wiki" (a wiki root / main page).
    """
    raw = (raw or "").strip().strip("<>\"'")
    if not raw:
        raise FandomError("No URL given.")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw

    parts = urlsplit(raw)
    host = parts.netloc.split("@")[-1].split(":")[0].lower()
    if not _HOST_RE.match(host):
        raise FandomError(
            f"“{host}” is not a Fandom wiki. Use a fandom.com or wikia.org link.")

    path = unquote(parts.path or "/")
    lang = ""
    m = _LANG_RE.match(path)
    if m and m.group(1).lower() not in ("wiki", "api"):
        lang = m.group(1).lower()
        path = m.group(2) or "/"

    site = f"{parts.scheme}://{host}" + (f"/{lang}" if lang else "")
    api = f"{site}/api.php"

    title = None
    if path.startswith("/wiki/"):
        title = path[len("/wiki/"):]
    elif "title=" in (parts.query or ""):
        m = re.search(r"(?:^|&)title=([^&]+)", parts.query)
        if m:
            title = unquote(m.group(1))
    elif path.strip("/") not in ("", "wiki"):
        head = path.strip("/").split("/")[0]
        if head in ("f", "d", "Special", "Board", "Thread"):
            raise FandomError(
                "That is a forum or special page — link to a wiki article instead.")
        raise FandomError("Could not find an article name in that URL.")

    title = (title or "").split("#")[0].strip().replace("_", " ").strip("/")

    kind = "wiki"
    if title:
        if _RESERVED_PREFIX.match(title):
            if title.lower().startswith("category:"):
                kind = "category"
            else:
                raise FandomError(
                    f"“{title}” is not an article — link to a wiki article instead.")
        else:
            kind = "article"

    return {"api": api, "site": site, "host": host, "lang": lang,
            "title": title or None, "kind": kind}


def is_fandom_api(url):
    """
    True when `url` is an api.php on a Fandom host.

    The browser hands the API endpoint back to us when importing a batch, so
    it has to be re-validated before we make requests with it.
    """
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return False
    host = parts.netloc.split("@")[-1].split(":")[0].lower()
    return (parts.scheme in ("http", "https")
            and bool(_HOST_RE.match(host))
            and parts.path.endswith("/api.php"))


def _call(api, params):
    """One MediaWiki API request returning parsed JSON."""
    query = dict(params)
    query.setdefault("format", "json")
    query.setdefault("formatversion", "2")
    try:
        r = SESSION.get(api, params=query, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise FandomError(f"Could not reach the wiki: {exc}") from exc
    except ValueError as exc:
        raise FandomError("The wiki returned a response we could not read.") from exc


# ══════════════════════════════════════════════════════════════════════════
# Wiki-level queries
# ══════════════════════════════════════════════════════════════════════════

def wiki_info(api):
    """Name and main page of a wiki."""
    data = _call(api, {"action": "query", "meta": "siteinfo", "siprop": "general"})
    general = data.get("query", {}).get("general", {})
    return {
        "name":      general.get("sitename") or "Fandom Wiki",
        "main_page": general.get("mainpage"),
        "base":      general.get("base"),
        "lang":      general.get("lang", "en"),
    }


def resolve(url):
    """
    Work out whether a link means one article or a whole wiki, and return
    (ref, wiki_info).

    A wiki's front page is served from `/wiki/<Title>` exactly like any
    article — https://jujutsu-kaisen.fandom.com/wiki/Jujutsu_Kaisen_Wiki is
    the main page, not a character — so the title is compared against what
    the wiki reports as its main page.
    """
    ref = parse_fandom_url(url)
    info = wiki_info(ref["api"])
    if ref["kind"] == "article":
        main = (info.get("main_page") or "").replace("_", " ").strip().lower()
        if main and ref["title"].strip().lower() == main:
            ref["kind"] = "wiki"
    return ref, info


# Article subpages that hold production or presentation detail, not lore,
# plus the wiki's own front page, which matches almost any search.
_META_SUBPAGE = re.compile(
    r"/(production|clothing|cast|credits|gallery|images?|trivia|quotes?"
    r"|soundtrack|music|merchandise|videos?|references?|deaths)$"
    r"|^.{0,60}\bwikia?$", re.I)

# Categories that mark a page as being about the real world — actors and
# crew have articles on most show wikis and are never part of the fiction.
_REAL_WORLD_CATEGORY = re.compile(
    r"^(cast|actors?|actresses|crew|directors?|writers?|producers?"
    r"|composers?|guest stars?|production|real[- ]world|staff)\b", re.I)


def search_wiki(api, query, limit=15):
    """
    Full-text search within one wiki, with real-world pages filtered out.

    A search for a season name otherwise returns the actors who appeared in
    it alongside the characters, because their articles mention it just as
    often. One extra request tells us which hits are cast pages.
    """
    data = _call(api, {
        "action": "query", "list": "search", "srsearch": query,
        "srnamespace": 0, "srlimit": max(limit * 2, 20), "srprop": "snippet",
    })
    hits = data.get("query", {}).get("search", [])
    titles = [h["title"] for h in hits if not _META_SUBPAGE.search(h["title"])]
    if not titles:
        return []

    real_world = set()
    try:
        info = _call(api, {"action": "query", "prop": "categories",
                           "titles": "|".join(titles[:MAX_TITLES_PER_CALL]),
                           "cllimit": "max", "clshow": "!hidden"})
        for page in info.get("query", {}).get("pages", []):
            cats = [c["title"].split(":", 1)[-1]
                    for c in page.get("categories", [])]
            if cats and any(_REAL_WORLD_CATEGORY.match(c) for c in cats):
                real_world.add(page.get("title"))
    except FandomError:
        pass

    results = []
    for hit in hits:
        title = hit["title"]
        if title in real_world or _META_SUBPAGE.search(title):
            continue
        snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", ""))
        results.append({"title": title, "snippet": snippet.strip()})
        if len(results) >= limit:
            break
    return results


def category_members(api, category, limit=200):
    """Article titles directly inside a category, alphabetically."""
    return _members(api, category, limit)[0]


def _members(api, category, limit=500):
    """
    Return (article_titles, subcategory_names) for one category.

    Both namespaces come back in a single request, which matters because
    walking a category tree is otherwise a request per level per branch.
    """
    if not category.lower().startswith("category:"):
        category = f"Category:{category}"
    pages, subs, cont = [], [], None
    while len(pages) < limit:
        params = {"action": "query", "list": "categorymembers",
                  "cmtitle": category, "cmnamespace": "0|14",
                  "cmlimit": 500, "cmsort": "sortkey"}
        if cont:
            params["cmcontinue"] = cont
        data = _call(api, params)
        for member in data.get("query", {}).get("categorymembers", []):
            title = member.get("title", "")
            if member.get("ns") == 14:
                subs.append(title.split(":", 1)[-1])
            elif title:
                pages.append(title)
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
    return pages[:limit], subs


def _category_sizes(api, names):
    """Page counts for many categories in as few requests as possible."""
    sizes = {}
    for i in range(0, len(names), MAX_TITLES_PER_CALL):
        chunk = names[i:i + MAX_TITLES_PER_CALL]
        data = _call(api, {
            "action": "query", "prop": "categoryinfo",
            "titles": "|".join(f"Category:{n}" for n in chunk),
        })
        for page in data.get("query", {}).get("pages", []):
            info = page.get("categoryinfo") or {}
            sizes[page["title"].split(":", 1)[-1]] = {
                "pages": info.get("pages", 0),
                "subcats": info.get("subcats", 0),
            }
    return sizes


# A category with fewer direct pages than this is treated as a container
# whose real contents live one level down, and is filled in immediately.
SPARSE_CATEGORY = 25


def resolve_category(api, name, budget=None, depth=1):
    """Pages of one category, following subcategories when it is a container."""
    budget = budget if budget is not None else {"calls": MAX_CATEGORY_CALLS}
    pages, subs = _members(api, name)
    budget["calls"] -= 1

    if pages or depth <= 0:
        return pages
    for sub in subs[:6]:
        if budget["calls"] <= 0:
            break
        if _NOISE_CATEGORY.search(sub):
            continue
        pages += resolve_category(api, sub, budget, depth - 1)
    return list(dict.fromkeys(pages))


def expand_category(api, name, budget=None):
    """
    Collect what is filed under a category, including its subcategories.

    Wikis organise the same idea in opposite ways and both have to work:

      * On the Avatar wiki, Category:Characters holds one page and nineteen
        subcategories — the 287 Na'vi and 178 Humans are all one level down,
        so reading only direct members finds almost nothing.
      * On the American Horror Story wiki, Category:Characters holds all 349
        pages *and* subcategories slicing them by season. There the
        subcategories are the useful part: a Coven lorebook wants
        "Characters of Coven", not the whole franchise.

    Returns (pages, subgroups). Subgroup *counts* come free with the size
    lookup, but their page lists are only fetched here when the parent is too
    sparse to stand on its own — otherwise the picker loads one on demand,
    which keeps indexing a large wiki down to two requests per category.
    """
    budget = budget if budget is not None else {"calls": MAX_CATEGORY_CALLS}

    pages, subs = _members(api, name)
    budget["calls"] -= 1

    subs = [s for s in subs if not _NOISE_CATEGORY.search(s)]
    subgroups = []
    if not subs:
        return pages, subgroups

    sizes = _category_sizes(api, subs[:MAX_TITLES_PER_CALL * 2])
    budget["calls"] -= 1
    subs.sort(key=lambda s: -(sizes.get(s, {}).get("pages", 0)))

    container = len(pages) < SPARSE_CATEGORY
    for sub in subs[:MAX_SUBGROUPS]:
        info = sizes.get(sub, {})
        count = info.get("pages", 0)
        if not count and not info.get("subcats"):
            continue

        sub_pages = []
        if container and budget["calls"] > 0:
            sub_pages = resolve_category(api, sub, budget)
            count = len(sub_pages) or count
        if count:
            subgroups.append({"category": sub, "count": count,
                              "pages": sub_pages})

    merged = list(pages)
    for group in subgroups:
        merged += group["pages"]
    return list(dict.fromkeys(merged)), subgroups


def _existing_categories(api, names):
    """Filter a candidate category list down to the ones the wiki actually has."""
    found = {}
    for i in range(0, len(names), MAX_TITLES_PER_CALL):
        chunk = names[i:i + MAX_TITLES_PER_CALL]
        data = _call(api, {
            "action": "query", "prop": "categoryinfo",
            "titles": "|".join(f"Category:{n}" for n in chunk),
        })
        for page in data.get("query", {}).get("pages", []):
            info = page.get("categoryinfo") or {}
            count = info.get("pages", 0)
            if count:
                found[page["title"].split(":", 1)[1]] = count
    return found


# Categories that re-file pages somebody has already seen, rather than adding
# any. "Characters by Occupation" is a navigation index; every page in it is a
# character, and offering it beside "Characters" is the same list twice.
_INDEX_CATEGORY = re.compile(r"\b(by|per)\s+\w+", re.I)

# The order pages are claimed in. A page is filed under whichever of these
# comes first, so Yuta Okkotsu is a Character rather than being repeated as a
# Jujutsu Sorcerer, a Culling Game Player and a member of the Gojo Family.
_CLAIM_ORDER = [
    "character", "protagonist", "antagonist", "villain", "hero",
    "species", "race", "creature", "monster", "deity", "god",
    "organization", "organisation", "group", "faction", "clan", "family",
    "team", "guild", "school", "house",
    "location", "place", "country", "city", "town", "village", "planet",
    "world", "realm", "region",
    "event", "battle", "war", "arc", "saga",
    "ability", "power", "technique", "magic", "spell", "skill", "quirk",
    "item", "weapon", "artifact", "object", "equipment", "tool",
    "terminology", "concept", "lore", "culture", "religion", "language",
    "media", "anime", "manga", "game", "novel", "film", "movie",
]

# Past this share of a category being pages another category already claimed,
# it is a facet of that category rather than a category of its own.
FACET_SHARE = 0.7


def _claim_rank(name):
    """How early a category gets to claim its pages. Lower wins."""
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    best = len(_CLAIM_ORDER)
    for word in words:
        singular = word[:-1] if word.endswith("s") and len(word) > 3 else word
        for candidate in (word, singular):
            if candidate in _CLAIM_ORDER:
                best = min(best, _CLAIM_ORDER.index(candidate))
    return best


# Titles that are never a lorebook entry, whichever category surfaced them.
# Browsing a whole wiki took category members verbatim, so the Demon Slayer
# wiki offered "Chapter 67", "Blu-ray & DVD: Entertainment District Arc -
# Volume 3", "Calendar/2016" and eight paint books as things to import.
_NOT_AN_ENTRY = re.compile(
    r"^(chapters?|episodes?|volumes?|issues?|parts?|acts?|seasons?|books?)\s*[#.]?\s*\d"
    r"|^(blu[- ]?ray|dvd|cd|ost|soundtrack|single|album|omnibus)\b"
    r"|\b(paint book|art ?book|setting book|fan ?book|character book"
    r"|colou?ring book|novelization|novelisation|guide ?book|data ?book"
    r"|sticker book|activity book|picture book|calendar|soundtrack)\b"
    r"|^(calendar|timeline|gallery|galleries)\b"
    # An index of the things is not one of the things.
    r"|^(lists?|indexe?s?) of\b", re.I)


def is_lorebook_page(title):
    """
    True when a page could be a lorebook entry at all.

    Subpages are parts of another page ("Calendar/2016"); the rest are
    instalments and merchandise, which are about the story rather than in it.
    """
    title = (title or "").strip()
    if not title or "/" in title or _RESERVED_PREFIX.match(title):
        return False
    return not _NOT_AN_ENTRY.search(title)


def dedupe_groups(groups):
    """
    Give every page one home, and turn the leftovers into filters.

    Wikis file the same character half a dozen ways. The Jujutsu Kaisen wiki
    lists Yuta Okkotsu under Characters, Jujutsu Sorcerers, Culling Game
    Players, Characters by Occupation, Characters by Affiliation and Gojo
    Family — 349 real pages presented as 663 across eighteen chips, so ticking
    two of them imports the same person twice and no chip means what it says.

    So each page is claimed by exactly one group, in lore order (a person is a
    Character before they are a Culling Game Player). A category left with
    almost nothing of its own was never a separate body of content — it is a
    way of slicing one — so it is offered as a sub-filter of the group that
    took its pages, where it still does its job without pretending to be new.
    """
    ordered = sorted(groups,
                     key=lambda g: (_claim_rank(g["category"]),
                                    -len(g["pages"]), g["category"]))
    claimed = {}          # page -> the group that owns it
    kept, facets = [], []
    for group in ordered:
        pages = group["pages"]
        if not pages:
            continue
        fresh = [p for p in pages if p not in claimed]
        # A pure re-slice, and an index category whatever its overlap.
        overlap = 1.0 - (len(fresh) / float(len(pages)))
        if _INDEX_CATEGORY.search(group["category"]) or overlap >= FACET_SHARE:
            owner = None
            for page in pages:
                if page in claimed:
                    owner = claimed[page]
                    break
            if owner is not None:
                facets.append((owner, group))
                continue
        if not fresh:
            continue
        group = dict(group, pages=fresh, total=len(fresh))
        for page in fresh:
            claimed[page] = group["category"]
        kept.append(group)

    by_name = {g["category"]: g for g in kept}
    for owner, group in facets:
        host = by_name.get(owner)
        if host is None:
            continue
        # A facet is mostly its host's pages, but rarely all of them. The few
        # it holds alone are still real entries and have to end up somewhere,
        # or demoting the chip would quietly lose them.
        for page in group["pages"]:
            if page not in claimed:
                claimed[page] = host["category"]
                host["pages"].append(page)
                host["total"] += 1
        names = {sub["category"] for sub in host["subgroups"]}
        if group["category"] in names:
            continue
        host["subgroups"] = host["subgroups"] + [{
            "category": group["category"],
            "count":    len(group["pages"]),
            "pages":    group["pages"],
        }]

    # Back into the order the caller offered them, so the wiki's own priorities
    # (LORE_CATEGORIES first, discovered ones after) still show through.
    position = {g["category"]: i for i, g in enumerate(groups)}
    kept.sort(key=lambda g: position.get(g["category"], len(position)))
    return kept


# ═════════════════════════════════════════════════════════════════════════
# What the wiki is about — the first question, not the last
# ═════════════════════════════════════════════════════════════════════════
#
# "Characters" is the wrong first question on a wiki that documents a whole
# franchise. The DC Universe wiki has ten films and eleven series in it, and a
# lorebook is almost never about all of them at once — asking for its
# characters returns everybody from Superman to Peacemaker in one list. The
# wiki already knows its own works and files them plainly, so the picker asks
# which one first and scopes everything after that to the answer.

# What a category has to end in to be listing works, and what kind of work that
# makes them. Longest first, so "Short Films" is not read as "Films".
#
# A fixed list of category *names* was not enough. The Marvel Cinematic
# Universe wiki has no category called "Movies" at all — its films are split
# across "Released Movies", "Upcoming Movies", "Phase One Movies" and six more
# — so a wiki with 47 films offered none of them. Matching the ending finds
# whatever a wiki calls its shelves.
WORK_SUFFIXES = [
    ("short films", "Short Films"),
    ("tv specials", "Specials"),
    ("tv series", "TV Series"),
    ("television series", "TV Series"),
    ("light novels", "Novels"),
    ("reference books", "Books"),
    ("video games", "Video Games"),
    ("web series", "TV Series"),
    ("animated series", "TV Series"),
    ("documentaries", "Documentaries"),
    ("spin-offs", "Spin-offs"),
    ("spin offs", "Spin-offs"),
    ("artbooks", "Artbooks"),
    ("specials", "Specials"),
    ("seasons", "Seasons"),
    # American Horror Story tells a self-contained story per season and files
    # them under "Stories" — no "Seasons" category anywhere. Only the plural:
    # "American Horror Story" is the wiki's own subject, not a shelf.
    ("stories", "Stories"),
    ("movies", "Movies"),
    ("films", "Movies"),
    ("series", "TV Series"),
    ("shows", "TV Series"),
    ("comics", "Comics"),
    ("manga", "Manga"),
    ("anime", "Anime"),
    ("games", "Video Games"),
    ("novels", "Novels"),
    ("books", "Books"),
    ("ovas", "OVA"),
    ("ova", "OVA"),
]

# The order the tabs are offered in, so the thing most people mean comes first.
KIND_ORDER = ["Movies", "TV Series", "Seasons", "Stories", "Short Films",
              "Specials",
              "Anime", "Manga", "Comics", "Spin-offs", "OVA",
              "Video Games", "Novels", "Books", "Artbooks", "Documentaries"]

# Categories that list a wiki's own works, best first. Kept as a hint for
# wikis whose category names do not end in a word we know.
WORK_CATEGORIES = [
    "Movies", "Films", "Feature Films",
    "TV Series", "Television Series", "TV Shows", "Shows", "Series",
    "Seasons", "Anime", "OVA", "OVAs", "Specials",
    "Manga", "Comics", "Graphic Novels",
    "Spin-offs", "Spinoffs", "Spin Offs",
    "Video Games", "Games", "Novels", "Light Novels", "Books",
    "Documentaries", "Artbooks",
]

# A page filed under "Movies" is not necessarily a film. Attack on Titan files
# every song from its films there too, and the Manga category carries the
# author. What separates them is a second category naming what they really
# are — Music, Lyrics, Albums — so one of these disqualifies a candidate.
_NOT_A_WORK = {
    "music", "musics", "albums", "album", "lyrics", "songs", "song",
    "soundtracks", "soundtrack", "singles", "single", "theme songs",
    "opening themes", "ending themes", "insert songs", "character songs",
    "authors", "author", "writers", "writer", "artists", "artist",
    "illustrators", "mangaka", "staff", "cast", "actors", "actresses",
    "voice actors", "directors", "producers", "composers", "publishers",
    "characters", "locations", "organizations", "organisations", "events",
    "episodes", "chapters", "volumes", "arcs", "story arcs", "quotes",
    "images", "galleries", "gallery", "videos", "screenshots", "artwork",
    "merchandise", "trivia", "real world articles", "disambiguations",
    "disambiguation pages", "terminology", "techniques", "abilities",
    "lists", "list", "indexes", "index", "polls", "popularity polls",
}

MAX_WORKS_PER_KIND = 40
MAX_WORK_CHECKS = 400         # cheap now that only template names are read
# Shelves of something other than works, which happen to end in a work word:
# "Unreleased Movies" is fine, "Movies by Studio" is a filing scheme.
_NOT_A_SHELF = re.compile(
    r"\b(galler(y|ies)|screenshots?|quotes?|images?|index|indexes|lists?"
    r"|soundtracks?|credits?|crew|cast|artists?|writers?|editors?|producers?"
    r"|by [a-z]+)\b"
    # …and shelves of something *about* works rather than of works. The Disney
    # wiki's biggest "video games" category is "Characters in video games",
    # 1,976 of them, and reading it first spent the whole Video Games budget
    # on people and left the kind with nothing to show.
    r"|^(characters?|songs?|people|users?|actors?|voices?|episodes?)\b"
    r"|\b(introduced in|based on|who are|fans of|appearing in)\b",
    re.I)

MAX_CATEGORY_SWEEP = 30       # pages of category names read to find the shelves
MAX_LISTS_PER_KIND = 4        # shelves opened per kind, largest first
MAX_LIST_CALLS = 26           # …and in total, across every kind

# api → the finished step-1 answer, so going back to it costs nothing.
_WORKS_CACHE = {}

# api → [(category, size, kind)], so a second visit to a wiki costs nothing.
_WORK_CATEGORY_INDEX = {}


def work_categories(api):
    """
    Every category on the wiki that looks like a shelf of works.

    Read by the ending of the name rather than the whole of it: the Marvel
    wiki shelves its films under "Released Movies", "Upcoming Movies" and one
    category per phase, and none of them is called "Movies".
    """
    if api in _WORK_CATEGORY_INDEX:
        return _WORK_CATEGORY_INDEX[api]

    found, params, calls = [], {"action": "query", "list": "allcategories",
                                "aclimit": "max", "acprop": "size"}, 0
    while calls < MAX_CATEGORY_SWEEP:
        try:
            data = _call(api, params)
        except FandomError:
            break
        calls += 1
        for row in data.get("query", {}).get("allcategories", []):
            name = row.get("category") or ""
            size = row.get("pages") or 0
            # _NOISE_CATEGORY is deliberately not used here. It exists to keep
            # media indexes out of a whole-wiki *page* browse, where "Anime"
            # is a list of episodes — but on this step "Anime" is a shelf of
            # works and exactly what is being looked for. Applying it lost the
            # DC wiki its Movies shelf.
            if not name or not size or _NOT_A_SHELF.search(name):
                continue
            plain = re.sub(r"[\s_\-]+", " ", name).strip().lower()
            for suffix, kind in WORK_SUFFIXES:
                if plain == suffix or plain.endswith(" " + suffix):
                    found.append((name, size, kind))
                    break
        cont = data.get("continue")
        if not cont:
            break
        params = dict(params, **cont)

    _WORK_CATEGORY_INDEX[api] = found
    return found


def _is_work_candidate(title):
    """
    Cheap rejections before anything is fetched.

    The colon test has to be for a real namespace prefix, not for any colon in
    the first word: "Avengers: Endgame", "Thor: Ragnarok" and "Spider-Man: No
    Way Home" are all titled that way, and fifteen Marvel films were being
    thrown out as though they were "Category:" pages.
    """
    if not title:
        return False
    if _RESERVED_PREFIX.match(title):
        return False
    # A work can be a subpage of the franchise it belongs to: every American
    # Horror Story season is filed as "American Horror Story/Murder House".
    # What rules a subpage out is the *kind* of subpage it is.
    if "/" in title:
        tail = title.rsplit("/", 1)[1].strip()
        # …and the tabs of an article are its parts, never works of their own.
        if _SUBPAGE_NOISE.match(tail) or tail in SUBPAGE_SUFFIXES:
            return False
    return not _META_SUBPAGE.search(title)


# Templates that mention a medium without being an infobox for one. A wiki
# citing its sources with {{Cite book}} was enough to make Maui and Te Fiti
# look like published works.
_NOT_AN_INFOBOX = re.compile(
    r"^(cite|citation|ref|refs|reference|note|notes|quote|quotes|sfn|harv"
    r"|nav|navbox|footer|stub|about|main|see also|for)\b", re.I)


# Navigation furniture named after the medium it navigates. {{Anime
# Navigation}} sits at the foot of every anime-related page, so it made
# "Mitsuri's Uniform" and an episode index look like published works.
_NAV_TEMPLATE = re.compile(
    r"\b(nav|navi|navigation|navbox|navbar|footer|sidebar|banner|header"
    r"|template|box|bar|list|index)$", re.I)


def is_work_template(name):
    """True when a template name marks its page as a film, series or book."""
    name = (name or "").strip()
    if not name or _NOT_AN_INFOBOX.match(name) or _NAV_TEMPLATE.search(name):
        return False
    return bool(_WORK_INFOBOX.search(name))


# What a confirming template says the thing actually is, where that is clearer
# than the shelf it was found on: Demon Slayer files its Hinokami Chronicles
# games under "TV Series", and their {{Game}} infobox knows better.
_TEMPLATE_KIND = [
    ("short film", "Short Films"), ("documentary", "Documentaries"),
    ("movie", "Movies"), ("film", "Movies"),
    ("video game", "Video Games"), ("game", "Video Games"),
    ("light novel", "Novels"), ("novel", "Novels"),
    ("artbook", "Artbooks"), ("book", "Books"),
    ("manga", "Manga"), ("anime", "Anime"), ("comic", "Comics"),
    ("season", "Seasons"), ("special", "Specials"),
    ("tv", "TV Series"), ("television", "TV Series"),
]


# Kinds where a page's own infobox is a better guide than the shelf: a wiki
# with no separate games shelf files them wherever, and {{Game}} is decisive.
_TRUST_TEMPLATE_KIND = {"Video Games", "Novels", "Books", "Artbooks", "Comics"}


def template_kind(names):
    """The kind a page's own templates claim, or "" when they do not say."""
    for name in names or ():
        if not is_work_template(name):
            continue
        plain = re.sub(r"[\s_\-]+", " ", name).strip().lower()
        for word, kind in _TEMPLATE_KIND:
            if re.search(r"\b" + re.escape(word) + r"\b", plain):
                return kind
    return ""


def fetch_templates(api, titles):
    """
    Which templates each page transcludes. Returns {title: [name, …]}.

    Used to tell a work from its author without downloading either. The
    wikitext says the same thing, but a Marvel film article is 100 KB of it
    and the template list is a few hundred bytes — fifteen pages came back in
    ten kilobytes rather than several megabytes.
    """
    out = {}
    titles = [t for t in titles if t]
    for i in range(0, len(titles), MAX_TITLES_PER_CALL):
        chunk = titles[i:i + MAX_TITLES_PER_CALL]
        params = {
            "action": "query", "prop": "templates", "tlnamespace": 10,
            "tllimit": "max", "titles": "|".join(chunk), "redirects": 1,
        }
        hops, landed = {}, {}
        # `tllimit` is a budget for the whole batch, not per page. Forty
        # articles listing seventeen templates each blow through it, and the
        # rest of the batch arrives on a continuation — unfollowed, the tail of
        # every batch looked as though it transcluded nothing, which is how
        # eight Marvel films failed a test their pages plainly pass.
        for _ in range(8):
            try:
                data = _call(api, params)
            except FandomError:
                break
            query = data.get("query", {})
            for norm in query.get("normalized", []):
                hops[norm["from"]] = norm["to"]
            for hop in query.get("redirects", []):
                hops[hop["from"]] = hop["to"]

            for page in query.get("pages", []):
                if page.get("missing"):
                    continue
                landed.setdefault(page.get("title", ""), []).extend(
                    (t.get("title") or "").split(":", 1)[-1]
                    for t in page.get("templates", []))

            cont = data.get("continue")
            if not cont:
                break
            params = dict(params, **cont)

        for title in chunk:
            end = title
            for _ in range(3):
                if end in hops:
                    end = hops[end]
                else:
                    break
            if end in landed:
                out[title] = landed[end]
        if len(titles) > MAX_TITLES_PER_CALL:
            time.sleep(0.1)
    return out


def discover_works(api, info=None):
    """
    The films, series, seasons and spin-offs a wiki documents.

    Returns [{"kind": "Movies", "works": [{"title", "image", "group"}, …]}, …]
    — the first step of the picker, so "characters" can be answered with
    "characters from *what*". Empty when a wiki documents a single story and
    there is no choice to make, in which case the picker skips the step.

    A kind is assembled from every shelf the wiki keeps it on, largest first.
    Marvel splits its 47 films across "Released Movies", "Upcoming Movies" and
    one category per phase; each is a slice of the same shelf, and the phase a
    work came from is kept as its `group` so the step can show it.
    """
    if api in _WORKS_CACHE:
        return _WORKS_CACHE[api]

    shelves = {}
    for name, size, kind in work_categories(api):
        shelves.setdefault(kind, []).append((size, name))
    if not shelves:
        _WORKS_CACHE[api] = []
        return []

    order = ([k for k in KIND_ORDER if k in shelves]
             + sorted(k for k in shelves if k not in KIND_ORDER))

    # Every kind gets its biggest shelf before any kind gets its second, so a
    # wiki with nine of them does not spend the whole budget on Movies and
    # leave Books and Video Games out of the step altogether.
    ranked = {kind: [name for _, name in sorted(shelves[kind], reverse=True)]
              for kind in order}
    rounds = []
    for depth in range(MAX_LISTS_PER_KIND):
        for kind in order:
            if depth < len(ranked[kind]):
                rounds.append((kind, ranked[kind][depth]))

    claimed, picked = {}, {}
    for calls, (kind, name) in enumerate(rounds):
        if calls >= MAX_LIST_CALLS:
            break
        try:
            members = category_members(api, name, limit=200)
        except FandomError:
            continue
        label = _shelf_label(name, kind)
        for title in members:
            if title in claimed or not _is_work_candidate(title):
                continue
            claimed[title] = kind
            picked.setdefault(kind, []).append({"title": title, "group": label})

    by_kind = [(kind, picked[kind][:MAX_WORKS_PER_KIND])
               for kind in order if picked.get(kind)]
    # The biggest shelf is a fair estimate of how many a kind really holds, so
    # the step can admit it is showing forty of two and a half thousand — but
    # only where the shelf is mostly works. Solo Leveling's "Anime" category is
    # forty-six pages of which one is the anime and forty-five are episodes and
    # songs; "showing 1 of 46" would be a promise of forty-five more that do
    # not exist. `held` is settled once the verification below says which.
    shelf_size = {kind: max(size for size, _ in shelves[kind]) for kind in shelves}
    candidates = {kind: len(picked.get(kind, ())) for kind in shelves}

    everything = [i["title"] for _, items in by_kind for i in items]
    if not everything:
        _WORKS_CACHE[api] = []
        return []
    canonical, categories, valid = _resolve_targets(api, everything)

    def is_work(title):
        # `valid` and `categories` are keyed by where a title actually lands
        # after normalisation and redirects, not by what was asked for. Testing
        # the raw spelling threw out eight Marvel films whose titles the API
        # normalises on the way through.
        landed = canonical.get(title, title)
        if landed not in valid:
            return False
        for raw in categories.get(landed, []):
            name = re.sub(r"[\s_\-]+", " ", raw or "").strip().lower()
            if name in _NOT_A_WORK:
                return False
        return True

    kept = [(kind, [i for i in items if is_work(i["title"])])
            for kind, items in by_kind]
    kept = [(kind, items) for kind, items in kept if items]
    if not kept:
        _WORKS_CACHE[api] = []
        return []

    # Categories cannot tell a story from the person who wrote it: on the
    # Jujutsu Kaisen wiki both Gege Akutami and Jujutsu Kaisen Modulo are filed
    # under nothing but "Manga". The infobox can — a work has one naming it a
    # series, film or game, and an author has none.
    survivors = [i["title"] for _, items in kept for i in items][:MAX_WORK_CHECKS]
    templates = fetch_templates(api, survivors)
    if templates:
        confirmed, says = set(), {}
        for title in survivors:
            for name in templates.get(title, ()):
                if is_work_template(name):
                    confirmed.add(title)
                    break
            claimed = template_kind(templates.get(title, ()))
            if claimed:
                says[title] = claimed
        # A wiki whose work pages carry no infobox we recognise would lose
        # every one of them, so this only narrows a list that still has
        # something left in it.
        checked = [(kind, [i for i in items if i["title"] in confirmed])
                   for kind, items in kept]
        if any(items for _, items in checked):
            kept = [(kind, items) for kind, items in checked if items]

        # A page's own infobox can correct the shelf it was found on — Demon
        # Slayer files its Hinokami Chronicles games under "TV Series" and they
        # say {{Game}} — but only for media a wiki has no narrative word for.
        # Between Movies, Seasons and Anime the shelf is the better judge:
        # these wikis stamp {{Anime}} on films and seasons alike, and trusting
        # it folded every one of them into a single Anime tab.
        moved, order = {}, [k for k, _ in kept]
        for kind, items in kept:
            for item in items:
                claimed = says.get(item["title"], "")
                target = claimed if claimed in _TRUST_TEMPLATE_KIND else kind
                moved.setdefault(target, []).append(item)
        order += [k for k in moved if k not in order]
        order.sort(key=lambda k: KIND_ORDER.index(k) if k in KIND_ORDER
                   else len(KIND_ORDER))
        kept = [(kind, moved[kind]) for kind in order if moved.get(kind)]

    posters = fetch_images(api, [i["title"] for _, items in kept for i in items])
    def held_for(kind, shown):
        # Only the ones that were actually checked can say what proportion of
        # the shelf is works. Comparing against every candidate instead counts
        # truncation as failure, and Disney's Movies shelf — forty verified out
        # of forty checked — stopped admitting it had 2,607 in it.
        checked = min(candidates.get(kind) or shown, MAX_WORKS_PER_KIND)
        if checked and shown < checked * 0.5:
            return shown
        return max(shelf_size.get(kind, shown), shown)

    answer = [{"kind": kind,
               "held": held_for(kind, len(items)),
               "works": [{"title": i["title"], "group": i["group"],
                          # A season filed as a subpage of its franchise reads
                          # as "Murder House", not "American Horror
                          # Story/Murder House" — the franchise is the wiki.
                          "label": i["title"].rsplit("/", 1)[-1].strip(),
                          "image": posters.get(i["title"], "")} for i in items]}
              for kind, items in kept]
    _WORKS_CACHE[api] = answer
    return answer


def search_works(api, query, limit=24):
    """
    Find one work by name, for wikis too big to list.

    The Disney wiki documents 2,607 films. No grid shows that, and the forty
    it can show start at "10 Things I Hate About You" — so on a wiki that
    size you do not browse for Frozen, you ask for it.
    """
    query = (query or "").strip()
    if not query:
        return []
    try:
        hits = search_wiki(api, query, limit=limit * 2)
    except FandomError:
        return []

    titles = [h["title"] for h in hits if _is_work_candidate(h.get("title", ""))]
    if not titles:
        return []

    templates = fetch_templates(api, titles)
    works = [t for t in titles
             if any(is_work_template(n) for n in templates.get(t, ()))]
    # A wiki that keeps a cast in categories named after the work says just as
    # clearly that it is one, and its film pages sometimes carry no infobox at
    # all — "Moana" is a disambiguation page with thirty-nine characters
    # filed under it.
    if len(works) < limit:
        for title in titles:
            if title in works or len(works) >= limit:
                continue
            if work_content_categories(api, title):
                works.append(title)
    works = works[:limit]
    if not works:
        return []

    posters = fetch_images(api, works)
    return [{"title": t, "group": "", "image": posters.get(t, "")}
            for t in works]


def _shelf_label(name, kind):
    """
    "Phase One Movies" → "Phase One". What is left once the kind word is taken
    off the end, so a step can say which part of a franchise a work belongs to.
    """
    plain = re.sub(r"[\s_\-]+", " ", name or "").strip()
    low = plain.lower()
    for suffix, matched in WORK_SUFFIXES:
        if matched != kind:
            continue
        if low == suffix:
            return ""
        if low.endswith(" " + suffix):
            return plain[:-(len(suffix) + 1)].strip()
    return ""


def browse_wiki(url, per_category=250, ref=None, info=None, works=True):
    """
    Inspect a whole wiki and return the lore categories worth importing.

    This is what a wiki-root link resolves to: instead of one entry, the user
    gets a pick-list of characters, locations, arcs and so on.
    """
    if ref is None:
        ref, info = resolve(url)
    api = ref["api"]
    info = info or wiki_info(api)

    if ref["kind"] == "category":
        wanted = [ref["title"].split(":", 1)[1]]
    else:
        known = _existing_categories(api, LORE_CATEGORIES)
        wanted = sorted(known, key=LORE_CATEGORIES.index)
        wanted = _drop_plural_duplicates(wanted, known)
        # Every wiki also has its own organising axis that no fixed list can
        # predict — American Horror Story files each season under its own
        # category ("Coven", "Murder House"), and that is exactly how someone
        # building a lorebook for one season wants to pick pages.
        wanted += _discover_categories(api, skip=set(wanted))

    budget = {"calls": MAX_CATEGORY_CALLS}
    groups = []
    seen_subgroups = set()
    for name in wanted:
        if budget["calls"] <= 0:
            break
        # Already offered as a sub-filter of an earlier group — listing it
        # again at the top level is the same pages under a second chip.
        if name in seen_subgroups:
            continue

        pages, subgroups = expand_category(api, name, budget)
        pages = [p for p in pages if is_lorebook_page(p)]
        subgroups = [dict(g, pages=[p for p in g["pages"] if is_lorebook_page(p)])
                     for g in subgroups]
        subgroups = [g for g in subgroups if g["pages"]]
        if not pages:
            continue
        seen_subgroups.update(g["category"] for g in subgroups)
        groups.append({
            "category":  name,
            "total":     len(pages),
            "pages":     pages[:per_category],
            "subgroups": [
                {"category": g["category"], "count": g["count"],
                 "pages": g["pages"][:per_category]}
                for g in subgroups
            ],
        })

    # Offered before the categories: "characters from what?" is the question a
    # franchise wiki leaves you holding, and the wiki can answer it itself.
    found_works = []
    if works and ref["kind"] != "category":
        try:
            found_works = discover_works(api, info)
        except FandomError:
            found_works = []

    return {
        "wiki_name": info["name"],
        "site":      ref["site"],
        "api":       api,
        "main_page": info["main_page"],
        "groups":    dedupe_groups(groups),
        "works":     found_works,
    }


def _drop_plural_duplicates(names, sizes):
    """
    Collapse "Character"/"Characters" style pairs, keeping the fuller one —
    both exist on some wikis and two near-identical chips only confuse.
    """
    best = {}
    for name in names:
        stem = name.lower().rstrip("s")
        if stem not in best or sizes.get(name, 0) > sizes.get(best[stem], 0):
            best[stem] = name
    return [n for n in names if best.get(n.lower().rstrip("s")) == n]


def _discover_categories(api, skip=(), limit=MAX_EXTRA_CATEGORIES):
    """
    The biggest non-housekeeping categories a wiki has, for the organising
    axes the known-names list cannot cover.

    The cap has to be generous: American Horror Story has twelve season
    categories and the smallest of them ("NYC", 26 pages) is as legitimate a
    lorebook as the biggest.
    """
    try:
        data = _call(api, {"action": "query", "list": "allcategories",
                           "acmin": MIN_EXTRA_CATEGORY, "aclimit": 500,
                           "acprop": "size"})
    except FandomError:
        return []

    found = []
    for cat in data.get("query", {}).get("allcategories", []):
        name = cat.get("category") or cat.get("*", "")
        if not name or name in skip or _NOISE_CATEGORY.search(name):
            continue
        # "Characters of Apocalypse/Supporting" is a slice of a slice; it
        # belongs under its parent as a sub-filter, not beside it.
        if "/" in name:
            continue
        found.append((cat.get("pages", 0), name))

    found.sort(reverse=True)
    return [name for _, name in found[:limit]]


# ══════════════════════════════════════════════════════════════════════════
# Page fetching
# ══════════════════════════════════════════════════════════════════════════

def fetch_pages(api, titles):
    """
    Fetch wikitext + categories for many titles, batched into as few API
    calls as possible. Returns {title: {wikitext, categories, missing}}.
    """
    out = {}
    titles = [t for t in titles if t]
    for i in range(0, len(titles), MAX_TITLES_PER_CALL):
        chunk = titles[i:i + MAX_TITLES_PER_CALL]
        data = _call(api, {
            "action": "query", "prop": "revisions|categories",
            "rvprop": "content", "rvslots": "main",
            "titles": "|".join(chunk), "redirects": 1, "cllimit": "max",
            "clshow": "!hidden",
        })
        query = data.get("query", {})
        normalized = {n["to"]: n["from"] for n in query.get("normalized", [])}

        # Where each requested title ended up, and whether it landed on a
        # section rather than a page of its own.
        redirects = {}
        for hop in query.get("redirects", []):
            source = normalized.get(hop["from"], hop["from"])
            redirects[source] = (hop["to"], hop.get("tofragment"))

        records = {}
        for page in query.get("pages", []):
            title = page.get("title", "")
            if page.get("missing"):
                records[title] = {"missing": True, "title": title}
                continue
            revs = page.get("revisions") or [{}]
            content = (revs[0].get("slots", {}).get("main", {}).get("content")
                       or revs[0].get("content") or "")
            records[title] = {
                "missing":    False,
                "title":      title,
                "wikitext":   content,
                "categories": [c["title"].split(":", 1)[-1]
                               for c in page.get("categories", [])],
            }

        for title, record in records.items():
            out[title] = record
            out.setdefault(normalized.get(title, title), record)

        for source, (target, fragment) in redirects.items():
            if fragment:
                # A section redirect: several of these collapse onto one host
                # page, so importing them would give every one of them a copy
                # of that whole page instead of their own entry.
                out[source] = {
                    "missing": True,
                    "title":   source,
                    "reason":  f"no page of its own — it is a section of “{target}”",
                }
            elif target in records:
                out[source] = records[target]
        if len(titles) > MAX_TITLES_PER_CALL:
            time.sleep(0.2)      # be polite on large imports
    return out


# Redirects that are not names: section pointers, plural/case variants of the
# title itself, and the maintenance redirects wikis leave behind.
_BAD_REDIRECT = re.compile(r"/|^list of |\(disambiguation\)$", re.I)


THUMB_SIZE = 320              # wide enough for a picker tile on a retina screen

# Stand-in graphics wikis use where an article has no picture of its own.
_PLACEHOLDER_IMAGE = re.compile(
    r"(nopic|no[_-]?pic(ture)?|noimage|no[_-]?image|placeholder|unknown"
    r"|question[_-]?mark|default[_-]?avatar)", re.I)

# Hosts an image may be proxied from. Fandom serves every wiki's media from
# one CDN, and anything else is not ours to fetch on the user's behalf.
_IMAGE_HOST = re.compile(
    r"^(?:[\w-]+\.)*(?:nocookie\.net|wikia\.com|fandom\.com"
    r"|wikimedia\.org|wikipedia\.org)$", re.I)


def is_image_url(url):
    """True for an image URL this app is willing to fetch and pass on."""
    try:
        parts = urlparse(url or "")
    except ValueError:
        return False
    return parts.scheme == "https" and bool(_IMAGE_HOST.match(parts.netloc))


def fetch_image_bytes(url, timeout=TIMEOUT):
    """
    Download one thumbnail. Returns (bytes, content_type).

    The pictures are served straight back to the page by the app rather than
    linked. That keeps the tiles working where a page is not allowed to load
    third-party images at all, and means browsing a wiki does not have the
    user's browser talking to Fandom's CDN a hundred times.
    """
    if not is_image_url(url):
        raise FandomError("That is not an image address we will fetch.")
    try:
        r = SESSION.get(url, timeout=timeout, stream=True)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise FandomError(f"Could not fetch the image: {exc}") from exc
    kind = (r.headers.get("Content-Type") or "").split(";")[0].strip()
    if not kind.startswith("image/"):
        raise FandomError("That address did not return an image.")
    body = r.content[:MAX_IMAGE_BYTES]
    return body, kind


MAX_IMAGE_BYTES = 3 * 1024 * 1024


def fetch_images(api, titles, size=THUMB_SIZE):
    """
    The picture at the top of each page, as a thumbnail URL.

    A pick-list of three hundred names is unusable if you do not already know
    the cast — "Rapt Tokage" means nothing until you see him. The wiki already
    designates a lead image per article (the one above the infobox on
    Yuji Itadori's page), and asking for it costs one request per fifty pages.

    Returns {title: url}; a page with no image is simply absent.
    """
    out = {}
    titles = [t for t in titles if t]
    for i in range(0, len(titles), MAX_TITLES_PER_CALL):
        chunk = titles[i:i + MAX_TITLES_PER_CALL]
        try:
            data = _call(api, {
                "action": "query", "prop": "pageimages",
                "piprop": "thumbnail", "pithumbsize": size,
                "pilimit": "max", "titles": "|".join(chunk), "redirects": 1,
            })
        except FandomError:
            continue
        query = data.get("query", {})

        hops = {}
        for norm in query.get("normalized", []):
            hops[norm["from"]] = norm["to"]
        for hop in query.get("redirects", []):
            hops[hop["from"]] = hop["to"]

        landed = {}
        for page in query.get("pages", []):
            thumb = (page.get("thumbnail") or {}).get("source")
            # Some wikis fill the slot with a placeholder graphic rather than
            # leaving it empty. Showing 40 identical "no picture available"
            # tiles is worse than showing none, so it counts as none.
            if thumb and not _PLACEHOLDER_IMAGE.search(thumb):
                landed[page.get("title", "")] = thumb

        for title in chunk:
            end = title
            for _ in range(3):
                if end in hops:
                    end = hops[end]
                else:
                    break
            if end in landed:
                out[title] = landed[end]
        if len(titles) > MAX_TITLES_PER_CALL:
            time.sleep(0.1)
    return out


def fetch_redirects(api, titles):
    """
    The other names each page answers to, as the wiki records them.

    A lorebook entry only fires on words that appear in the chat, and an
    infobox rarely lists the name a character is actually *called*. The wiki
    does, in its redirects: "Knuckleduster" and "O'Clock" both point at Iwao
    Oguro, "Pop Step" at Kazuho Haneyama, "Scarred Man" at Number 6 — none of
    which the infobox aliases mention. They cost one request per forty pages.

    Returns {title: [name, …]}.
    """
    out = {}
    titles = [t for t in titles if t]
    for i in range(0, len(titles), MAX_TITLES_PER_CALL):
        chunk = titles[i:i + MAX_TITLES_PER_CALL]
        try:
            data = _call(api, {
                "action": "query", "prop": "redirects", "rdnamespace": 0,
                "rdlimit": "max", "titles": "|".join(chunk), "redirects": 1,
            })
        except FandomError:
            continue
        query = data.get("query", {})

        # Where each requested spelling ended up, so the names come back under
        # the title the caller asked about.
        hops = {}
        for norm in query.get("normalized", []):
            hops[norm["from"]] = norm["to"]
        for hop in query.get("redirects", []):
            hops[hop["from"]] = hop["to"]

        landed = {}
        for page in query.get("pages", []):
            if page.get("missing"):
                continue
            names = []
            for red in page.get("redirects", []):
                title = (red.get("title") or "").strip()
                if title and not _BAD_REDIRECT.search(title):
                    names.append(title)
            landed[page.get("title", "")] = names

        for title in chunk:
            end = title
            for _ in range(3):
                if end in hops:
                    end = hops[end]
                else:
                    break
            if end in landed:
                out[title] = landed[end]
        if len(titles) > MAX_TITLES_PER_CALL:
            time.sleep(0.15)
    return out


# ══════════════════════════════════════════════════════════════════════════
# Tabbed subpages
# ══════════════════════════════════════════════════════════════════════════

def declared_subpages(title, wikitext):
    """
    The subpages a page's own tab bar names.

    Wikis that split an article across tabs say so at the top of it:

        {{Tabs |tab1 = Profile |tab2 = Synopsis |tab3 = Image Gallery}}

    Reading that is exact and free — the wikitext is already in hand — and it
    beats guessing at "<Title>/Synopsis" for every page in a bulk import, which
    would be several hundred titles asked for on the chance they exist.
    """
    _, _, params = W.find_template(wikitext or "", r"^(char(acter)?[ _]?)?tabs?$")
    found = []
    for key, value in (params or {}).items():
        if not re.match(r"^tab\d*$|^\d+$", key.strip(), re.I):
            continue
        name = W.clean(value).strip().strip("[]")
        if not name or _SUBPAGE_NOISE.match(name):
            continue
        found.append(f"{title}/{name}")
    return list(dict.fromkeys(found))


# api → {parent title: [suffix, …]}, or None for a wiki too big to sweep.
# One sweep serves every import from that wiki for the life of the process.
_SUBPAGE_INDEX = {}


def subpage_index(api):
    """
    Every article subpage the wiki actually has, grouped by its parent page.

    This is the authoritative answer to "does this character have a Synopsis
    tab", and it replaces two unreliable guesses. Reading the page's own
    `{{Tabs}}` template only works on wikis that pass the tab names as
    parameters — the My Hero Academia wiki writes `{{Tabs/Active}}`, which
    names none of them, so every synopsis on that wiki was invisible.
    Speculating "<Title>/Synopsis" only finds the suffixes we thought to list.
    Asking the wiki for its titles finds whatever it actually calls them.

    Returns None when the wiki is too large to sweep within MAX_INDEX_CALLS,
    which tells the caller to fall back to guessing.
    """
    if api in _SUBPAGE_INDEX:
        return _SUBPAGE_INDEX[api]

    try:
        stats = _call(api, {"action": "query", "meta": "siteinfo",
                            "siprop": "statistics"})
        articles = stats.get("query", {}).get("statistics", {}).get("articles")
    except FandomError:
        articles = None
    if articles and articles > MAX_INDEX_ARTICLES:
        _SUBPAGE_INDEX[api] = None
        return None

    index, params, calls = {}, {"action": "query", "list": "allpages",
                                "apnamespace": 0, "aplimit": "max",
                                "apfilterredir": "nonredirects"}, 0
    while calls < MAX_INDEX_CALLS:
        try:
            data = _call(api, params)
        except FandomError:
            # A wiki that will not answer this is not a wiki we can index;
            # the caller falls back rather than the import failing.
            _SUBPAGE_INDEX[api] = None
            return None
        calls += 1
        for page in data.get("query", {}).get("allpages", []):
            title = page.get("title", "")
            parent, sep, suffix = title.partition("/")
            if not sep or not suffix or _SUBPAGE_NOISE.match(suffix):
                continue
            index.setdefault(parent, []).append(suffix)
        cont = data.get("continue")
        if not cont:
            _SUBPAGE_INDEX[api] = index
            return index
        params = dict(params, **cont)

    _SUBPAGE_INDEX[api] = None      # too big; guess instead
    return None


def subpage_titles(titles, suffixes=SUBPAGE_SUFFIXES):
    """Every "<Title>/<Suffix>" worth asking for, as a speculative fallback."""
    out = []
    for title in titles:
        if "/" in title:          # already a subpage; it has none of its own
            continue
        out += [f"{title}/{suffix}" for suffix in suffixes]
    return out


# A template invocation whose name is itself a subpage: {{Izuku Synopsis/Final
# Act}}. Wikis use these to keep one enormous article in readable pieces, and
# the pieces are the article. Anything with parameters is a formatting helper
# rather than a slab of prose, so a "|" rules a match out.
_CONTENT_TRANSCLUSION = re.compile(
    r"\{\{\s*(?:Template:)?\s*([^|{}\n]+?/[^|{}\n]+?)\s*\}\}")

MAX_TRANSCLUDED = 6           # pieces followed per page
MAX_TRANSCLUDED_CHARS = 500000


def expand_transclusions(api, records):
    """
    Splice transcluded content pages into the pages that include them.

    The My Hero Academia wiki keeps each character's synopsis in template
    subpages — Izuku Midoriya's is three of them holding 330 KB between them —
    so his Synopsis tab is four headings and no story. Templates are stripped
    when wikitext is cleaned, which is right for formatting helpers and wrong
    for these, so they are pulled in first and read as part of the page.

    Mutates `records` and returns how many pieces were inlined.
    """
    wanted, where = [], {}
    for key, record in list(records.items()):
        if not record or record.get("missing") or "/" not in key:
            continue
        suffix = key.split("/", 1)[1]
        if not _STORY_TAB.search(suffix):
            continue
        raw = record.get("wikitext") or ""
        names = []
        for match in _CONTENT_TRANSCLUSION.finditer(raw):
            name = match.group(1).strip()
            if not name or name.lower().startswith(("file:", "category:")):
                continue
            # {{Tabs/Active}} is a slash away from looking like content. What
            # tells them apart is the family the template belongs to.
            family = re.sub(r"[\s_]+", " ", name.split("/", 1)[0]).strip().lower()
            if family in W.TEMPLATE_DROP or _NOT_AN_INFOBOX.match(family):
                continue
            if any(word in family for word in W.TEMPLATE_DROP_SUBSTR):
                continue
            names.append(name)
        for name in list(dict.fromkeys(names))[:MAX_TRANSCLUDED]:
            title = name if name.lower().startswith("template:") else f"Template:{name}"
            wanted.append(title)
            where.setdefault(key, []).append((name, title))

    if not wanted:
        return 0
    pieces = fetch_pages(api, list(dict.fromkeys(wanted)))

    filled = 0
    for key, refs in where.items():
        raw = records[key].get("wikitext") or ""
        budget = MAX_TRANSCLUDED_CHARS
        for name, title in refs:
            piece = pieces.get(title) or {}
            body = "" if piece.get("missing") else (piece.get("wikitext") or "")
            if not body or len(body) > budget:
                continue
            budget -= len(body)
            pattern = re.compile(
                r"\{\{\s*(?:Template:)?\s*"
                + re.escape(name) + r"\s*\}\}")
            raw, count = pattern.subn(lambda _m: body, raw, count=1)
            filled += count
        records[key] = dict(records[key], wikitext=raw)
    return filled


def fetch_with_subpages(api, titles, guess=True):
    """
    Fetch pages together with their tabbed subpages.

    Three ways of finding those, best first:

      1. the wiki's own title index, which is exact — it knows that
         "Koichi Haimawari/Synopsis" exists and that its 142 KB of story is
         the better half of that character for roleplay;
      2. the page's `{{Tabs |tab1=…}}` template, when the wiki passes the tab
         names as parameters and the index was unavailable;
      3. speculating "<Title>/Synopsis" and the rest of SUBPAGE_SUFFIXES.

    The fallbacks matter only for wikis too large to index. `guess` turns off
    step 3 — the extra titles cost nothing but bandwidth, since a subpage that
    does not exist simply comes back missing.
    """
    records = fetch_pages(api, titles)

    # Subpages hang off the page a title really lands on, not off a redirect
    # pointing at it: "Pop☆Step" redirects to "Kazuho Haneyama", and it is the
    # latter that owns /Synopsis.
    resolved = []
    for title in titles:
        record = records.get(title)
        if record and not record.get("missing"):
            resolved.append(record.get("title") or title)

    index = subpage_index(api)
    wanted, untabbed = [], []
    for title in resolved:
        if index is not None:
            found = [f"{title}/{s}" for s in index.get(title, ())]
        else:
            found = declared_subpages(
                title, (records.get(title) or {}).get("wikitext") or "")
        if found:
            wanted += found
        else:
            untabbed.append(title)

    # Only when the index could not answer: a page with no declared tabs might
    # still have subpages, and asking for eight speculative titles per page
    # rides along in the same batched requests.
    if guess and index is None and untabbed:
        wanted += subpage_titles(untabbed)

    wanted = [t for t in dict.fromkeys(wanted) if t not in records]
    if wanted:
        # A subpage that will not load is a thinner entry, not a failed import:
        # one rate-limited request should never lose the page it belongs to.
        try:
            records.update(fetch_pages(api, wanted))
        except FandomError:
            pass
    # Some wikis keep the story in template subpages the tab merely includes.
    expand_transclusions(api, records)
    return records


def arc_key(name):
    """
    Normalise an arc heading so the same arc matches across characters.

    Wikis are not consistent about the suffix — Yuji's synopsis calls it
    "Modulo" and Yuka's calls it "Jujutsu Kaisen Modulo" — so the comparison
    drops the decoration and keeps the distinctive words.
    """
    key = re.sub(r"\s*\(.*?\)\s*", " ", name or "").strip().lower()
    key = re.sub(r"\b(story\s+)?arc\b|\bsaga\b", " ", key)
    key = re.sub(r"[^a-z0-9]+", " ", key).strip()
    return key


def _same_arc(key, want):
    """
    The same arc is not spelled the same on every page — Yuji's synopsis heads
    it "Modulo" and Yuka's heads it "Jujutsu Kaisen Modulo" — so a heading also
    matches a selection that is one of its whole-word tails, and vice versa.
    """
    return key == want or key.endswith(" " + want) or want.endswith(" " + key)


def arc_matches(name, selected):
    """True when an arc heading is one the user picked."""
    if selected is None:
        return True
    key = arc_key(name)
    return any(_same_arc(key, want) for want in selected)


def path_matches(path, selected):
    """
    True when any arc enclosing this section was picked.

    Arcs nest, so picking "First Selection Arc" has to bring its four matches
    with it while picking "Team X vs Team Z" brings only that one. Testing the
    whole path rather than the heading is what makes both work.
    """
    if selected is None:
        return True
    return any(_same_arc(key, want) for key in (path or []) for want in selected)


# Headings that name a part of a story outright. This is the signal the wikis
# themselves provide — "Sky Egg Arc", "Shibuya Incident Arc", "Chapter 3" —
# and it beats measuring, because it identifies the arc level correctly whether
# the arcs are long or short and whether or not they have scenes nested inside.
_ARC_TITLE = re.compile(
    r"\b(arc|saga|season|volume)$"
    r"|^(chapter|part|act|episode|book|volume|season|phase|stage)\b[\s:]*[\divxlc]",
    re.I)

# A heading needs this much prose to count as story rather than scaffolding
# when no heading names itself an arc and the level has to be inferred.
ARC_MIN_CHARS = 120


def _arc_level(sections):
    """
    Which heading level a story tab tells its arcs at.

    Wikis do not agree on the depth. Some list arcs as top-level headings; both
    the My Hero Academia and Jujutsu Kaisen wikis group them under containers:

        == History ==            ← a short lead-in
        === Gojo's Past Arc ===  ← the arcs
        == Synopsis ==           ← empty; it exists only to hold the tabs below
        === Fearsome Womb Arc ===

    Taking every heading for an arc offers "History" and "Synopsis" as arc
    chips, which are not arcs; assuming level 2 finds no arcs at all on a wiki
    like these. So the level is worked out — by name where the wiki says so,
    which is most of the time, and by shape where it does not.
    """
    named = {}
    for section in sections:
        if _ARC_TITLE.search(section["title"] or ""):
            named[section["level"]] = named.get(section["level"], 0) + 1
    # The shallowest level that names two or more arcs. Shallowest matters:
    # when an arc has scenes nested under it, the arc is the outer heading and
    # the scenes belong inside it.
    for level in sorted(named):
        if named[level] >= 2:
            return level

    # No wiki-supplied names: fall back to shape. Whichever level carries the
    # most headings with real prose is where the story is being told.
    weight = {}
    for section in sections:
        if len(section["text"]) >= ARC_MIN_CHARS:
            weight[section["level"]] = weight.get(section["level"], 0) + 1
    if weight:
        return max(weight, key=lambda lvl: (weight[lvl], -lvl))

    # Every section is short. Falling back to the shallowest level here would
    # fold a whole page into one block — a Relationships tab of brief notes,
    # one per person, came out as a single section titled after whoever
    # happened to be first. The level that actually carries the writing is
    # still the right answer, however little of it there is.
    written = {}
    for section in sections:
        if section["text"]:
            written[section["level"]] = written.get(section["level"], 0) + 1
    if written:
        return max(written, key=lambda lvl: (written[lvl], -lvl))
    return min((s["level"] for s in sections), default=2)


def story_sections(raw):
    """
    A story tab's sections, keeping the shape the wiki wrote them in.

    Arcs nest. Blue Lock's synopsis has matches inside arcs inside a plot:

        == Plot ==
        === First Selection Arc ===
        ==== Team X vs Team Z ====

    — and the wiki is not even consistent about the depth, filing later arcs
    at level 2 beside the plot rather than under it. So nothing is folded away
    here: every heading becomes a node carrying the `path` of headings
    enclosing it, outermost first. Picking "First Selection Arc" then takes
    its matches with it, and picking "Team X vs Team Z" takes only that one.

    `is_arc` marks the level the story is mainly told at, which is what the
    chooser shows first; everything else nests under it.

    Returns [{title, level, depth, priority, text, is_arc, key, path}, …].
    """
    _, raw_sections = W.split_sections(raw or "")
    sections = []
    for section in raw_sections:
        text = W.clean(section["body"]).strip()
        sections.append({"title": (section["title"] or "").strip(),
                         "level": section["level"],
                         "priority": section["priority"],
                         # "Coming soon!" is not an arc; treating it as empty
                         # lets the heading be dropped like any other blank.
                         "text": "" if W.is_placeholder(text) else text})
    if not sections:
        return []

    arc_level = _arc_level(sections)
    out, stack = [], []          # stack: (heading level, arc key) enclosing us

    for index, section in enumerate(sections):
        while stack and stack[-1][0] >= section["level"]:
            stack.pop()

        # A heading with neither prose nor anything beneath it is a stray.
        following = sections[index + 1] if index + 1 < len(sections) else None
        holds_more = bool(following and following["level"] > section["level"])
        if not section["text"] and not holds_more:
            continue

        key = arc_key(section["title"])
        section["key"] = key
        section["path"] = [k for _, k in stack] + [key]
        section["depth"] = len(stack)
        section["is_arc"] = (bool(_ARC_TITLE.search(section["title"]))
                             or section["level"] == arc_level)
        stack.append((section["level"], key))
        out.append(section)

    return out


def _children_of(sections, index):
    """The nodes directly beneath sections[index]."""
    depth = sections[index]["depth"]
    out = []
    for section in sections[index + 1:]:
        if section["depth"] <= depth:
            break
        if section["depth"] == depth + 1:
            out.append(section)
    return out


def collapse_sections(sections):
    """
    Fold a story tab back down to its arcs.

    The tree is what makes a single match selectable; it is not what anybody
    wants to read having selected nothing. Unfiltered, Blue Lock would come
    out as forty headings holding a paragraph each.

    Collapsing by depth is wrong here — "Plot" is scaffolding two levels above
    the matches, and folding into it merges every arc into one 68 KB block. So
    the target is the arc itself: a heading that holds only other arcs is
    scaffolding and steps aside, while one holding scenes keeps them.
    """
    targets = []
    for index, section in enumerate(sections):
        if not section["is_arc"]:
            # A lead-in standing outside every arc is its own section, not
            # part of one — folding it into the arc below it would be wrong,
            # and dropping it lost the opening line of Gojo's history.
            if section["depth"] == 0 and section["text"]:
                targets.append(index)
            continue
        children = _children_of(sections, index)
        holds_only_arcs = bool(children) and all(c["is_arc"] for c in children)
        if section["text"] or not holds_only_arcs:
            targets.append(index)
    targets = set(targets)

    merged, host = [], None
    for index, section in enumerate(sections):
        if index in targets:
            host = dict(section)
            merged.append(host)
            continue
        if host is None or not section["text"]:
            continue
        head = f"{section['title']}: " if section["title"] else ""
        joiner = chr(10) * 2
        host["text"] = (host["text"] + joiner + head + section["text"]).strip()
        host["priority"] = min(host["priority"], section["priority"])
    return [s for s in merged if s["text"]]


def page_arcs(records, title):
    """
    The story arcs a page's synopsis is divided into, in reading order.

    Returns [{name, key, chars}, …]. `chars` is how much prose the arc holds,
    which is what tells a main appearance from a one-line cameo.
    """
    prefix = f"{title}/"
    found = []
    for key in sorted(k for k in records if k.startswith(prefix)):
        suffix = key[len(prefix):]
        record = records.get(key)
        if not record or record.get("missing") or _SUBPAGE_NOISE.match(suffix):
            continue
        raw = record.get("wikitext") or ""
        if re.match(r"^\s*#redirect", raw, re.I):
            continue

        if not _STORY_TAB.match(suffix.strip()):
            continue        # a Relationships tab is people, not story arcs
        parsed = story_sections(raw)
        for index, section in enumerate(parsed):
            # A heading that holds nothing but other arcs is scaffolding —
            # "Plot" and "Synopsis" are not parts of the story to choose
            # between, they are where the parts are kept.
            children = _children_of(parsed, index)
            if not section["text"] and children and all(c["is_arc"] for c in children):
                continue
            name = (section["title"] or suffix).strip()
            found.append({"name": name, "key": section["key"] or arc_key(name),
                          "named": bool(_ARC_TITLE.search(name)),
                          "depth": section["depth"], "is_arc": section["is_arc"],
                          "parent": section["path"][-2] if len(section["path"]) > 1 else "",
                          "chars": len(section["text"])})
    return found


def collect_arcs(records, titles):
    """
    Every arc across a batch of pages, nested, with how many cover each one.

    A wiki tells the same story from each character's point of view, so the
    arcs repeat — which is what makes one list of chips able to filter a whole
    import rather than needing a choice per entry. Isagi's "Team X vs Team Z"
    and Bachira's are the same match seen twice, so they are one chip, sitting
    under the First Selection Arc chip that both of them nest it in.
    """
    seen = {}
    for title in titles:
        for arc in page_arcs(records, title):
            row = seen.setdefault(arc["key"], {
                "key": arc["key"], "name": arc["name"], "names": set(),
                "pages": 0, "chars": 0, "depth": arc["depth"],
                "parent": arc["parent"], "is_arc": arc["is_arc"],
                "named": arc["named"]})
            row["pages"] += 1
            row["chars"] += arc["chars"]
            row["names"].add(arc["name"])
            # The shallowest place any page files it is where it belongs: one
            # character may nest a match a level deeper than another does.
            if arc["depth"] < row["depth"]:
                row["depth"] = arc["depth"]
                row["parent"] = arc["parent"]
            row["is_arc"] = row["is_arc"] or arc["is_arc"]
            row["named"] = row["named"] or arc["named"]
            if len(arc["name"]) > len(row["name"]):
                row["name"] = arc["name"]

    # Fold a long spelling into the short one it ends with, so "Modulo" and
    # "Jujutsu Kaisen Modulo" are offered as one chip rather than two.
    for key in sorted(seen, key=len):
        for other in [k for k in seen if k != key and k.endswith(" " + key)]:
            row, dead = seen[key], seen.pop(other)
            row["pages"] += dead["pages"]
            row["chars"] += dead["chars"]
            row["names"] |= dead["names"]

    out = []
    for row in seen.values():
        row["name"] = min(row["names"], key=len) if row["names"] else row["name"]
        row.pop("names", None)
        # A heading on one page holding a couple of lines is that page's own
        # layout ("Members", "Base of Operations"), not part of the story —
        # unless the wiki called it an arc, in which case it is one however
        # briefly it is written. Solo Leveling gives each of its twenty arcs
        # about four hundred characters, and the size test threw away fourteen.
        if not row["chars"]:
            continue        # an arc the wiki has not written yet
        if not row["named"] and row["pages"] < 2 and row["chars"] < MIN_ARC_CHARS:
            continue
        out.append(row)

    # A child whose parent did not survive would be orphaned in the chooser,
    # so it is promoted rather than hidden. So is one whose parent is not an
    # arc at all: Solo Leveling files twenty arcs under a "History" heading
    # that has prose of its own, and burying the whole story one click down
    # from a chip called "History" is not what anybody is looking for.
    by_key = {row["key"]: row for row in out}
    for row in out:
        parent = by_key.get(row["parent"]) if row["parent"] else None
        if row["parent"] and (parent is None or not parent["is_arc"]):
            row["parent"] = ""
            row["depth"] = 0

    # Parents first, each followed by its own children, so the chooser can
    # render the nesting by walking the list once.
    tops = [r for r in out if not r["parent"]]
    tops.sort(key=lambda a: (-a["pages"], -a["chars"]))
    ordered = []
    for top in tops:
        ordered.append(top)
        kids = [r for r in out if r["parent"] == top["key"]]
        kids.sort(key=lambda a: (-a["pages"], -a["chars"]))
        ordered += kids
    return ordered


def subpage_sections(records, title, budget, arcs=None, skip=None):
    """
    Cleaned, budgeted sections from a page's subpages.

    Arcs are kept as separate sections rather than merged into one block. That
    is what makes them selectable, and it is also what makes them *readable*:
    merged, ten arcs share one section's ceiling and 136,000 characters of
    story come out as 1,500. Narrowed to the one or two arcs a lorebook is
    about, each gets a real share of the budget instead.

    `arcs` is a set of normalised keys (see `arc_key`); None keeps everything.
    `skip` is a pattern matched against tab names to leave out — used when a
    tab is being turned into an entry of its own instead.
    """
    prefix = f"{title}/"
    collected = []
    for key in sorted(k for k in records if k.startswith(prefix)):
        suffix = key[len(prefix):]
        record = records.get(key)
        if not record or record.get("missing") or _SUBPAGE_NOISE.match(suffix):
            continue
        raw = record.get("wikitext") or ""
        if re.match(r"^\s*#redirect", raw, re.I):
            continue

        if skip is not None and skip.search(suffix):
            continue        # pulled out into an entry of its own

        # The arc filter answers "which parts of the story", so it only
        # applies to the story tab. A Relationships page is kept whole.
        story = bool(_STORY_TAB.match(suffix.strip()))
        # With nothing picked, the tree is folded back to its arcs — forty
        # headings of a paragraph each is not an entry anybody wants to read.
        # With something picked, it is kept whole so a single match can be
        # taken on its own.
        parsed = story_sections(raw)
        sections = parsed if (story and arcs is not None) else collapse_sections(parsed)
        for section in sections:
            text = section["text"]
            name = (section["title"] or suffix).strip()
            # Testing the whole path, not the heading: picking an arc takes the
            # scenes nested inside it, and picking one scene takes only that.
            if story and not path_matches(section.get("path"), arcs):
                continue
            collected.append({
                "title":    name,
                "tab":      suffix,
                "level":    2,
                # The story is what the entry was short of, so it outranks the
                # other tabs. Without this a Relationships page — one short
                # section per person, a dozen of them — crowds out the one arc
                # the user actually asked for.
                "priority": 1 if story else 2,
                "text":     text,
            })

    if not collected:
        return []

    # The story tab is budgeted apart from the others rather than competing
    # with them. Kafka Hibino's Relationships page is a dozen short sections,
    # one per person; shared out proportionally they leave the single arc the
    # user picked with a couple of sentences, which is the opposite of what
    # picking it was for.
    story_idx = [i for i, s in enumerate(collected) if s["priority"] == 1]
    other_idx = [i for i, s in enumerate(collected) if s["priority"] != 1]
    if not other_idx:
        return _fit(collected,
                    max(budget, min(len(story_idx) * MIN_ARC_BUDGET,
                                    budget * 2)),
                    keep_all=True)
    if not story_idx:
        return _fit(collected, budget)

    # A story told in eleven arcs needs room for eleven arcs. Held to a flat
    # share of the entry budget it gets ~200 characters each, which is a table
    # of contents rather than a synopsis — so the story's floor rises with how
    # many parts it actually has, and the entry grows to match.
    share = int(budget * STORY_TAB_SHARE)
    # The guarantee may stretch the story past its share — that is the point —
    # but not without limit, or "compact" would produce the same entry as
    # "standard" for any character with a long synopsis.
    story_budget = max(share, min(len(story_idx) * MIN_ARC_BUDGET, share * 2))
    fitted = dict(zip(story_idx, _fit([collected[i] for i in story_idx],
                                      story_budget, keep_all=True)))
    fitted.update(zip(other_idx, _fit([collected[i] for i in other_idx],
                                      max(600, budget - share))))
    return [fitted[i] for i in sorted(fitted)]


def _fit(sections, budget, keep_all=False):
    """Budget a set of subpage sections, letting a lone one use the lot."""
    if not sections:
        return []
    # Fewer arcs means each may be longer: one selected arc should be able to
    # spend the whole budget rather than stopping at a section's usual ceiling.
    cap_scale = max(1.0, budget / (len(sections) * 1200.0))
    return W.fit_sections(sections, budget, cap_scale, keep_all)


# ══════════════════════════════════════════════════════════════════════════
# Works — films, episodes, comics, games
# ══════════════════════════════════════════════════════════════════════════
#
# A film page is not a lorebook entry so much as a table of contents for one.
# Its "Cast" and "Appearances" sections list every character, location, event,
# item, organisation and species that turns up in it, already linked to the
# articles describing them — which is exactly the scope someone wants when
# they say "make me a Spider-Man: Homecoming lorebook" rather than dumping
# the whole Marvel Cinematic Universe wiki into one file.

# Wikis split a cast list across several headings and only the first of them
# used to be read: the Invincible wiki lists four people under "Main
# Characters" and another thirty under "Supporting" and "Guest", so a season
# offered a cast of four. Any heading that is a kind of cast counts.
_CAST_SECTION = re.compile(
    r"^((main|supporting|recurring|guest|additional|other|minor|special"
    r"|notable|featured|appearing|returning|new|also)\s+)*"
    r"(cast|characters|voice cast|voice actors|starring|credits cast"
    r"|cast and characters|characters and cast)"
    r"(\s+(and\s+)?(cast|characters))?$", re.I)
_APPEARANCE_SECTION = re.compile(r"^appearances?$", re.I)
_WORK_INFOBOX = re.compile(
    # "TV" and "Documentary" are what the Marvel wiki calls these, and without
    # them every one of its 36 series failed the check and vanished from the
    # step that was meant to offer them.
    r"\b(tv|television|documentary|short film|miniseries|web series"
    r"|movie|film|episode|season|comic|issue|game|book|novel|series|show"
    r"|special|one[- ]shot|short|stor(y|ies)"
    # Comics wikis name this kind of page after the medium rather than calling
    # it a series — {{Infobox manga}} indexes a story exactly the way
    # {{Series Infobox}} does.
    r"|manga|anime|manhwa|manhua|webtoon|light novel|publication"
    r"|franchise|spin[- ]?off|sequel|prequel)\b", re.I)

_SMALL_NOTE = re.compile(r"<small>(.*?)</small>", re.S | re.I)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_CAST_SEPARATOR = re.compile(r"\sas\s|\s[-–—]\s", re.I)

# Annotations meaning the entity barely features: a name-drop, a photo on a
# wall, a building in the skyline. Real enough to list on a wiki, but not
# worth a lorebook entry unless the user asks for it.
_MINOR_NOTE = re.compile(
    r"\b(mentioned|indirectly|name only|deleted scene|archive footage"
    r"|footage|photo|photograph|picture|art|artwork|drawing|logo|sign"
    r"|poster|background|billboard|graffiti|statue|toy|cameo)\b", re.I)

# Wookieepedia marks the same thing with templates rather than words:
# {{1stm}} first mentioned, {{Mo}} mentioned only, {{Imo}} indirect mention.
_MINOR_MARKER = re.compile(r"\{\{\s*(1stm|mo|imo|nc|ncmo)\s*[|}]", re.I)

MAX_MANIFEST_ITEMS = 600      # a Star Wars film lists well over a thousand

# Appearance subsections that are production credits rather than fiction.
_SKIP_APPEARANCE_GROUP = re.compile(
    r"^(uncredited|archive footage.*|stunt.*|crew|production.*|music"
    r"|soundtrack|trivia|references?|notes?)$", re.I)


def _strip_note(line):
    """Split '<small>(mentioned)</small>' annotations off a list item."""
    line = _HTML_COMMENT.sub("", line)
    notes = _SMALL_NOTE.findall(line)
    text = _SMALL_NOTE.sub("", line)
    note = ""
    if notes:
        note = W.clean(" ".join(notes)).strip().strip("()").strip()
    return note, text


def _app_group_label(key):
    """
    'c-characters' → 'Characters', 'l-characters' → 'Characters (Legends)'.

    Wookieepedia lists both continuities in one template. Collapsing them
    would produce two identical chips holding different canons, so the
    non-canon one says which it is.
    """
    key = key.strip()
    prefix = re.match(r"^([a-z]{1,2})-", key, re.I)
    label = re.sub(r"^[a-z]{1,2}-", "", key, flags=re.I)
    label = label.replace("-", " ").replace("_", " ").strip().title()
    if prefix and prefix.group(1).lower() == "l":
        label += " (Legends)"
    return label or "Appearances"


# Pages a Cast or Appearances section links that are not things in the story:
# the wiki's own overflow lists. "List of Minor Characters" turned up in
# Supergirl's cast beside Lobo and Krypto.
_LIST_PAGE = re.compile(
    r"^(lists? of|index of)\b|\b(list|index|gallery|galleries)$", re.I)


def _dedupe_items(items):
    seen, out = set(), []
    for item in items:
        key = item["title"].lower()
        if key in seen or _LIST_PAGE.search(item["title"]):
            continue
        seen.add(key)
        out.append(item)
    return out


def _parse_cast(body):
    """
    '*[[Tom Holland]] as [[Spider-Man|Peter Parker/Spider-Man]]' → Spider-Man.

    The first link is the actor — a real person, and never wanted in a
    lorebook — so only what follows the "as" is kept.
    """
    items = []
    for raw in body.split("\n"):
        line = raw.strip()
        if not line.startswith("*"):
            continue
        note, text = _strip_note(line)
        parts = _CAST_SEPARATOR.split(text, maxsplit=1)
        if len(parts) < 2:
            continue
        links = W.link_targets(parts[1])
        if not links:
            continue           # character has no article of its own
        target, display = links[0]
        items.append({"title": target, "label": display, "note": note,
                      "minor": bool(note and _MINOR_NOTE.search(note))})
    return _dedupe_items(items)


def _parse_appearance_list(body, force_minor=False):
    """Read an indented '*[[Thing]] <small>(note)</small>' list."""
    items = []
    for raw in body.split("\n"):
        line = raw.strip()
        if not line.startswith("*"):
            continue
        note, text = _strip_note(line)
        links = W.link_targets(text)
        if not links:
            continue           # {{WPS|…}} real-world links have no target here
        marked = bool(_MINOR_MARKER.search(line))
        target, display = links[0]
        items.append({
            "title": target,
            "label": display,
            "note":  note or ("mentioned" if marked else ""),
            "minor": force_minor or marked
                     or bool(note and _MINOR_NOTE.search(note)),
        })
    return _dedupe_items(items)[:MAX_MANIFEST_ITEMS]


def extract_manifest(wikitext):
    """
    Read a work's Cast and Appearances sections into named groups.

    Returns [(group_name, [item, …]), …] preserving the page's own order.
    """
    _, sections = W.iter_sections(wikitext)
    groups = []
    index, total = 0, len(sections)

    while index < total:
        section = sections[index]
        title, level = section["title"], section["level"]

        if _CAST_SECTION.match(title):
            body = section["body"]
            nxt = index + 1
            while nxt < total and sections[nxt]["level"] > level:
                if not _SKIP_APPEARANCE_GROUP.match(sections[nxt]["title"]):
                    body += "\n" + sections[nxt]["body"]
                nxt += 1
            found = _parse_cast(body)
            if found:
                groups.append(("Characters", found))
            index = nxt
            continue

        if _APPEARANCE_SECTION.match(title):
            # Wookieepedia-style {{App|c-characters=…|c-vehicles=…}}, where
            # the parameters are the groups instead of sub-headings.
            _, _, app = W.find_template(section["body"], r"^app(earances)?$")
            if app:
                for key, raw in app.items():
                    found = _parse_appearance_list(raw)
                    if found:
                        groups.append((_app_group_label(key), found))
            else:
                direct = _parse_appearance_list(section["body"])
                if direct:
                    groups.append(("Appearances", direct))
            nxt = index + 1
            while nxt < total and sections[nxt]["level"] > level:
                sub = sections[nxt]
                if not _SKIP_APPEARANCE_GROUP.match(sub["title"]):
                    minor = bool(_MINOR_NOTE.search(sub["title"]))
                    found = _parse_appearance_list(sub["body"], minor)
                    if found:
                        groups.append((sub["title"], found))
                nxt += 1
            index = nxt
            continue

        index += 1

    return groups


def is_work_page(infobox_type, manifest):
    """True when a page indexes a story rather than describing one thing."""
    if manifest and sum(len(items) for _, items in manifest) >= 8:
        return True
    return bool(infobox_type and _WORK_INFOBOX.search(infobox_type))


def manifest_browse(ref, info, title, manifest):
    """Shape a work's manifest like a browse result so the picker can show it."""
    groups = []
    for name, items in manifest:
        groups.append({
            "category":  name,
            "total":     len(items),
            "pages":     [i["title"] for i in items],
            "minor":     [i["title"] for i in items if i["minor"]],
            "notes":     {i["title"]: i["note"] for i in items if i["note"]},
            "subgroups": [],
        })
    return {
        "wiki_name":   info.get("name", ""),
        "site":        ref.get("site", ""),
        "api":         ref.get("api", ""),
        "main_page":   info.get("main_page"),
        "source_page": title,
        "groups":      groups,
    }


# ══════════════════════════════════════════════════════════════════════════
# Rendered-infobox fallback
# ══════════════════════════════════════════════════════════════════════════
#
# Some wikis keep the infobox out of the article wikitext — the One Piece wiki
# transcludes it from a separate template, so parsing the source finds nothing.
# Fandom renders all of them as a portable infobox, so when the wikitext turns
# up empty we read the labels and values back out of the rendered HTML.

_ASIDE_RE = re.compile(
    r"<aside[^>]*\bclass=\"[^\"]*portable-infobox[^\"]*\"[^>]*>(.*?)</aside>",
    re.S | re.I)
_PI_PAIR_RE = re.compile(
    r"<h3[^>]*\bclass=\"[^\"]*pi-data-label[^\"]*\"[^>]*>(.*?)</h3>\s*"
    r"<div[^>]*\bclass=\"[^\"]*pi-data-value[^\"]*\"[^>]*>(.*?)</div>",
    re.S | re.I)
_SUP_RE = re.compile(r"<sup[^>]*>.*?</sup>", re.S | re.I)
_BLOCK_END_RE = re.compile(r"</(li|div|p|tr|h\d)\s*>|<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_REF_MARK_RE = re.compile(r"\[\d+\]")


def _html_fragment_to_text(fragment):
    """Rendered infobox cell → the text a reader would see."""
    text = _SUP_RE.sub("", fragment)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _REF_MARK_RE.sub("", text)
    items = []
    for line in re.split(r"[\n;]+", text):
        item = re.sub(r"\s+", " ", line).strip(" ,;")
        if item and item not in items:
            items.append(item)
    return ", ".join(items)


def fetch_infobox_html(api, title):
    """Read a page's rendered portable infobox. Returns rows like parse_infobox."""
    try:
        data = _call(api, {"action": "parse", "page": title, "prop": "text",
                           "redirects": 1})
    except FandomError:
        return []
    markup = (data.get("parse") or {}).get("text") or ""
    aside = _ASIDE_RE.search(markup)
    if not aside:
        return []

    rows = []
    for raw_label, raw_value in _PI_PAIR_RE.findall(aside.group(1)):
        label = _html_fragment_to_text(raw_label).strip(" :")
        value = _html_fragment_to_text(raw_value)
        if not label or not value:
            continue
        if W.is_blocked_field(label):
            continue
        key = re.sub(r"[\s_]+", " ", label).strip().lower()
        rows.append({
            "label": W.LABEL_OVERRIDES.get(key) or label.strip(),
            "value": value[:400],
            "field": key,
        })
    return rows


# ══════════════════════════════════════════════════════════════════════════
# Profile building
# ══════════════════════════════════════════════════════════════════════════

_NAME_FIELDS = ("name", "character name", "page name")
_ALIAS_FIELDS = ("alias", "aliases", "also known as", "other names",
                 "nickname", "nicknames", "epithet", "other", "viz name",
                 "real name", "full name", "true name")

# Some wikis use the infobox "title" row for a rank or job — the Marvel
# Cinematic Universe wiki gives Adrian Toomes "Chief" and Tony Stark "CEO of
# Stark Industries (formerly)". Those must never become the entry's name.
# Rejecting one simply falls back to the article title, which is nearly
# always right, so the test can afford to be strict.
_ROLE_LIKE = re.compile(
    r"\bof\b|[(),;]|\bformerly\b"
    r"|^(the\s+)?(ceo|cfo|coo|chief|director|president|vice|king|queen"
    r"|captain|commander|general|leader|head|owner|founder|manager|agent"
    r"|officer|lord|lady|boss|professor|doctor|member|student|teacher)\b",
    re.I)


def _pick_name(rows, fallback):
    """
    Choose an entry's display name from the infobox, else the page title.

    An infobox name only wins when it is no longer than the article title —
    that keeps the common name a reader would actually type. "Tokyo
    Metropolitan Curse Technical College" gives way to "Tokyo Jujutsu High",
    while "Kiri" is not replaced by "Kiri te Suli Kìreysì'ite" (which stays
    on as a trigger key).
    """
    by_field = {}
    for row in rows:
        by_field.setdefault(row["field"], row["value"])
    for field in _NAME_FIELDS:
        value = (by_field.get(field) or "").strip()
        if (value and len(value) <= 60
                and len(value) <= len(fallback)
                and not _ROLE_LIKE.search(value)):
            return value, field
    return fallback, None


def _split_aliases(value):
    """
    Split an alias list on commas, ignoring commas inside brackets.

    Wikis annotate who uses a nickname — "Sister (by Neteyam, Lo'ak, and
    Jake)" is one alias, not three, and splitting naively turns the tail of
    the annotation into nonsense trigger words.
    """
    parts, buf, depth = [], [], 0
    for char in value:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _first_sentence(text, limit=240):
    if not text:
        return ""
    first = re.split(r"(?<=[.!?])\s+", text.strip().split("\n")[0])[0]
    return first[:limit].strip()


# Sections are budgeted by scrapers.wikitext, which both this module and
# the Wikipedia scraper share — the trimming problem is identical.
MIN_SECTION_CHARS = W.MIN_SECTION_CHARS
_fit_sections = W.fit_sections


def build_profile(ref, record, wiki_name=None, notes="", budget=DEFAULT_BUDGET,
                  records=None, arcs=None, redirects=(), split_relations=False,
                  image=""):
    """
    Turn one fetched page into a lorebook profile dict.

    `records` is the whole batch the page came in, so its tabbed subpages can
    be picked out of it without another request. Pass None to skip them.
    """
    from core.classifier import classify_fandom      # local: avoids a cycle

    raw = record.get("wikitext") or ""
    title = record.get("title") or ref.get("title") or "Untitled"

    if re.match(r"^\s*#redirect", raw, re.I):
        raise FandomError(f"“{title}” is a redirect with no content.")

    infobox_type, rows = W.parse_infobox(raw)
    if not rows and ref.get("api"):
        rows = fetch_infobox_html(ref["api"], title)
        if rows and not infobox_type:
            infobox_type = "Portable Infobox"

    name, name_field = _pick_name(rows, title)

    aliases = []
    facts = []
    for row in rows:
        # The row that supplied the display name is not also a fact to print
        # back at the reader.
        if row["field"] == name_field:
            continue
        if row["field"] in _ALIAS_FIELDS:
            aliases += _split_aliases(row["value"])
        facts.append({"label": row["label"], "value": row["value"]})

    lead_raw, sections = W.split_sections(raw)
    lead = W.trim_text(W.clean(lead_raw), LEAD_BUDGET)

    cleaned = []
    for section in sections:
        cleaned.append({
            "title":    section["title"],
            "level":    section["level"],
            "priority": section["priority"],
            "text":     W.clean(section["body"]),
        })
    cleaned = _fit_sections(cleaned, budget)

    # "Satoru Gojo" says what he is like; "Satoru Gojo/Synopsis" says what
    # happened to him, and for roleplay that is the half worth having.
    extra = []
    if records is not None:
        extra = subpage_sections(records, title,
                                 int(budget * SUBPAGE_BUDGET_SHARE), arcs,
                                 _RELATION_TAB if split_relations else None)
        # A page can name a section the same as one of its own arcs — Yuji has
        # a short "Modulo" under Appearance and an 11 KB "Modulo" in his
        # synopsis. Two identical headings in one entry read as a mistake, so
        # the story one says which tab it came from.
        taken = {s["title"].strip().lower() for s in cleaned}
        for section in extra:
            if section["title"].strip().lower() in taken:
                section["title"] = f"{section.get('tab', 'Story')}: {section['title']}"
            section.pop("tab", None)
        cleaned = cleaned + extra

    categories = [c for c in record.get("categories", [])
                  if not _NOISE_CATEGORY.search(c)][:12]

    category = classify_fandom(infobox_type, categories, name, lead)

    profile = {
        "source":        "fandom",
        "name":          name,
        "category":      category,
        "wiki_name":     wiki_name or "",
        "site":          ref.get("site", ""),
        "api":           ref.get("api", ""),
        "page_title":    title,
        "page_url":      f"{ref.get('site', '')}/wiki/{quote(title.replace(' ', '_'))}",
        "infobox_type":  infobox_type or "",
        "aliases":       list(dict.fromkeys(a for a in aliases if a != name))[:6],
        # The names the wiki itself redirects to this page. They are what
        # people actually type — "Knuckleduster", "Pop Step" — and an infobox
        # almost never lists them.
        "redirects":     [r for r in dict.fromkeys(redirects) if r != name][:12],
        "facts":         facts,
        "description":   _first_sentence(lead),
        "extract":       lead,
        "sections":      cleaned,
        "subpage_sections": len(extra),
        "fandom_categories": categories,
        "budget":        budget,
        # Shown as the entry's tile in the Maker. Not written into the entry
        # text — a lorebook is prose the model reads, and a URL is neither.
        "image":         image or "",
    }
    if notes:
        profile["notes"] = notes
    return profile


def build_relationship_profile(ref, record, records, wiki_name=None,
                               budget=DEFAULT_BUDGET, redirects=(), image=""):
    """
    One character's Relationships tab as an entry in its own right.

    A well-kept wiki writes a paragraph on what the subject is to each person
    they know, which for roleplay is the most directly useful thing on the
    page — and the worst served by being folded into the character entry,
    where a dozen short sections compete with the story and all of them lose.
    Split out, both halves get a whole budget, and the entry fires on the
    *other* person's name as well as the subject's, which is exactly when a
    model needs to know how the two of them stand.

    Returns None when the page has no relationships tab.
    """
    title = record.get("title") or ref.get("title") or ""
    sections = subpage_sections(records, title, budget,
                                skip=_NOT_RELATION_TAB)
    if not sections:
        return None

    _, rows = W.parse_infobox(record.get("wikitext") or "")
    subject, _ = _pick_name(rows, title)
    tab = sections[0].get("tab") or "Relationships"
    for section in sections:
        section.pop("tab", None)
        section["level"] = 2

    # Each heading is somebody's name, so each is a trigger: the entry should
    # come into context when either party is mentioned.
    people = []
    for section in sections:
        for part in re.split(r"\s*(?:,|&|/| and )\s*", section["title"]):
            part = part.strip()
            if 2 < len(part) <= 60:
                people.append(part)

    page = title + "/" + tab
    return {
        "source":        "fandom",
        "name":          subject + " — Relationships",
        "category":      "Relationships",
        "wiki_name":     wiki_name or "",
        "site":          ref.get("site", ""),
        "api":           ref.get("api", ""),
        "page_title":    page,
        "page_url":      f"{ref.get('site', '')}/wiki/{quote(page.replace(chr(32), chr(95)))}",
        "infobox_type":  "",
        "aliases":       [],
        "facts":         [],
        "description":   "How " + subject + " stands with the people around them.",
        "extract":       "",
        "sections":      sections,
        "subpage_sections": len(sections),
        "fandom_categories": [],
        "budget":        budget,
        "image":         image or "",
        # Explicit, because the usual key builder would take the subpage title
        # apart and offer "Relationships" as a trigger word.
        "keys":          list(dict.fromkeys([subject] + list(redirects) + people)),
        "relates_to":    subject,
    }


def scrape_article(url, notes="", budget=DEFAULT_BUDGET, ref=None, info=None,
                   log=None, subpages=True, arcs=None):
    """
    Scrape one Fandom article.

    Returns (profile, browse) where `browse` lists what belongs in a lorebook
    *about this page* — and None for an ordinary article. Two kinds of page
    have that:

      * a film, episode or comic issue, whose Cast and Appearances sections
        already name everything that turns up in it;
      * a sub-series on a franchise wiki, which lists nothing of the sort, so
        its scope is derived from its own chapters and episodes instead.
    """
    if ref is None:
        ref, info = resolve(url)
    if ref["kind"] != "article":
        raise FandomError("That link points at a whole wiki, not one article.")

    info = info or wiki_info(ref["api"])
    pages = (fetch_with_subpages(ref["api"], [ref["title"]], guess=True) if subpages
             else fetch_pages(ref["api"], [ref["title"]]))
    record = pages.get(ref["title"])
    if not record or record.get("missing"):
        reason = (record or {}).get("reason")
        raise FandomError(f"“{ref['title']}” {reason}." if reason
                          else f"“{ref['title']}” does not exist on this wiki.")

    names = fetch_redirects(ref["api"], [ref["title"]]).get(ref["title"], ())
    picture = fetch_images(ref["api"], [ref["title"]]).get(ref["title"], "")
    profile = build_profile(ref, record, info["name"], notes, budget,
                            pages if subpages else None, arcs, names,
                            image=picture)
    raw = record.get("wikitext") or ""

    manifest = extract_manifest(raw)
    browse = None
    if is_work_page(profile.get("infobox_type"), manifest):
        browse = manifest_browse(ref, info, record["title"], manifest)

    # Three ways of working out what is in a story, and no wiki supports all
    # three. The page's own cast list is the most exact where it exists; a
    # sub-series is measured against its own chapters; and some wikis say
    # nothing on the page and file everything in categories named after it.
    # They are gathered rather than raced, because a wiki that supports two
    # will have put different things in each — narrowing Vigilantes to the
    # pages filed under its name alone lost two hundred of them.
    def widen(build, *args):
        nonlocal browse
        try:
            extra = build(*args)
        except FandomError:
            return
        if extra and extra.get("groups"):
            browse = merge_browse(browse, extra) if browse else extra

    if is_series_page(profile.get("infobox_type"), raw,
                      record["title"], info["name"]):
        widen(build_scope, ref, info, record["title"], raw, log)
    widen(build_work_scope, ref, info, record["title"], raw, log)

    # The wiki's own subject, in one of its formats. There is nothing to narrow
    # to — everything on the wiki is in this story — so the answer is the whole
    # wiki, and it is merged in rather than kept as a last resort. Solo
    # Leveling's anime page happens to carry a seventeen-name cast list, and
    # finding that was enough to stop the wiki's other four hundred pages ever
    # being offered.
    if is_wiki_subject(record["title"], info.get("name")):
        say = log.append if log is not None else (lambda _m: None)
        say(f"“{record['title']}” is what this whole wiki is about — "
            f"opening all of it.")
        try:
            whole = browse_wiki(url, ref=ref, info=info, works=False)
        except FandomError:
            whole = None
        if whole and whole.get("groups"):
            browse = merge_browse(browse, whole) if browse else whole
    return profile, browse


def scrape_page(url, notes="", budget=DEFAULT_BUDGET, ref=None, info=None):
    """Scrape one Fandom article URL into a lorebook profile."""
    return scrape_article(url, notes, budget, ref, info)[0]


# How much of the chosen detail level each rank of entry is worth. Scope
# already works out who carries the story and who walks through one chapter of
# it (see scope.rank), and spending the same budget on both is what makes a
# 60-entry import mostly filler: the protagonist is capped at the same size as
# a thug with one line. "core" is the full budget, so the detail setting still
# means what it says for the characters the lorebook is about.
TIER_BUDGET = {"core": 1.0, "supporting": 0.62, "background": 0.38}
MIN_TIER_BUDGET = 900

# A relationships entry is supplementary to its character, so it gets a share
# of that character's budget rather than a second helping of it. At the full
# amount Koichi Haimawari's ran to 13,000 characters — more than most of the
# characters it describes — because nothing was competing with it any more.
RELATION_BUDGET_SHARE = 0.5


def tier_budget(budget, tier):
    """The character budget one entry gets, given how central it is."""
    share = TIER_BUDGET.get((tier or "").lower())
    if share is None or share >= 1.0:
        return budget
    return max(MIN_TIER_BUDGET, int(budget * share))


def scrape_titles(api, site, titles, wiki_name=None, budget=DEFAULT_BUDGET,
                  subpages=True, arcs=None, tiers=None, relations=False):
    """
    Scrape many pages of one wiki at once.
    Returns (profiles, [(title, reason), …]) so the UI can report skips.

    `tiers` maps a title to "core"/"supporting"/"background" so a walk-on is
    not given the same room as the protagonist. `relations` splits each
    character's Relationships tab into an entry of its own.
    """
    titles = list(dict.fromkeys(titles))[:MAX_BULK_PAGES]
    ref = {"api": api, "site": site}
    if wiki_name is None:
        wiki_name = wiki_info(api)["name"]

    records = (fetch_with_subpages(api, titles) if subpages
               else fetch_pages(api, titles))
    names = fetch_redirects(api, titles)
    pictures = fetch_images(api, titles)
    profiles, skipped = [], []
    for title in titles:
        record = records.get(title)
        if not record or record.get("missing"):
            skipped.append((title,
                            (record or {}).get("reason") or "page not found"))
            continue
        try:
            tier = (tiers or {}).get(title)
            entry_budget = tier_budget(budget, tier)
            profiles.append(build_profile(ref, record, wiki_name, "",
                                          entry_budget,
                                          records if subpages else None, arcs,
                                          names.get(title, ()), relations,
                                          pictures.get(title, "")))
            if relations and subpages:
                extra = build_relationship_profile(
                    ref, record, records, wiki_name,
                    max(MIN_TIER_BUDGET,
                        int(entry_budget * RELATION_BUDGET_SHARE)),
                    names.get(title, ()), pictures.get(title, ""))
                if extra:
                    profiles.append(extra)
        except FandomError as exc:
            skipped.append((title, str(exc)))
        except Exception as exc:                      # one bad page ≠ failed import
            skipped.append((title, f"could not parse ({exc})"))
    return profiles, skipped


# ══════════════════════════════════════════════════════════════════════════
# Sub-series scope
# ══════════════════════════════════════════════════════════════════════════
#
# A wiki covering a franchise documents every part of it in one namespace, so
# a link to one part ("Jujutsu Kaisen Modulo") has to be turned into that
# part's cast, places and events without dragging in the rest. The reasoning
# lives in scrapers/scope.py; this is the wiring that gives it wiki access.

def _resolve_targets(api, titles):
    """
    Check a batch of link targets against the wiki.

    Returns (canonical, categories, valid): where each title really points
    after redirects and normalisation, the categories of the page it lands on,
    and which of them exist at all. Red links are common in wikitext — a wiki
    names people it has never written an article about — and importing one
    would produce an empty entry.
    """
    canonical, categories, valid = {}, {}, set()
    titles = [t for t in titles if t]

    for i in range(0, len(titles), MAX_TITLES_PER_CALL):
        chunk = titles[i:i + MAX_TITLES_PER_CALL]
        params = {
            "action": "query", "prop": "categories",
            "titles": "|".join(chunk), "redirects": 1,
            "cllimit": "max", "clshow": "!hidden",
        }
        hops = {}
        # `cllimit` counts categories across the whole batch, not per page, so
        # forty pages carrying twenty categories each overflow it and the rest
        # of the batch comes back on a continuation. Ignoring that quietly lost
        # the tail of every large batch — eight Marvel films looked like red
        # links because their pages were simply never in the reply.
        for _ in range(6):
            try:
                data = _call(api, params)
            except FandomError:
                break
            query = data.get("query", {})

            for norm in query.get("normalized", []):
                hops[norm["from"]] = norm["to"]
            for hop in query.get("redirects", []):
                hops[hop["from"]] = hop["to"]

            for page in query.get("pages", []):
                title = page.get("title", "")
                if page.get("missing"):
                    continue
                valid.add(title)
                categories.setdefault(title, []).extend(
                    c["title"].split(":", 1)[-1]
                    for c in page.get("categories", []))

            cont = data.get("continue")
            if not cont:
                break
            params = dict(params, **cont)

        # Follow normalise → redirect chains back to the requested spelling.
        for title in chunk:
            end = title
            for _ in range(3):
                if end in hops:
                    end = hops[end]
                else:
                    break
            canonical[title] = end
        if len(titles) > MAX_TITLES_PER_CALL:
            time.sleep(0.15)

    return canonical, categories, valid


# A work's own page lists its instalments under a heading like this, and each
# of them is a page saying what happened in it and to whom.
_EPISODE_SECTION = re.compile(
    r"^(episodes?|chapters?|volumes?|issues?|instal?ments?|parts?)\b", re.I)

MAX_WORK_SEEDS = 40           # instalments read to widen one work's scope
MAX_WORK_CONTENT_LISTS = 8    # “Frozen characters”, “Frozen locations”, …


def instalment_links(raw):
    """
    The episodes or chapters a work's own page lists, in order.

    A season page names its cast and almost nothing else — no locations, no
    items, no factions. Its episodes name all of it, and the season page
    already links them in its episode table, so they are free to find.
    """
    _, sections = W.split_sections(raw or "")
    out, unit = [], ""
    for section in sections:
        heading = (section["title"] or "").strip()
        if not _EPISODE_SECTION.match(heading):
            continue
        # The heading also says what these are, so the picker can tell the
        # reader it read eight episodes rather than eight "chapters".
        unit = unit or _EPISODE_SECTION.match(heading).group(1).lower()
        for target, _ in W.link_targets(section["body"]):
            target = (target or "").strip()
            if not target or "/" in target or _RESERVED_PREFIX.match(target):
                continue
            out.append(target)
    return list(dict.fromkeys(out))[:MAX_WORK_SEEDS], unit


# Categories a wiki names after a work to hold what is *in* it. The Disney
# wiki keeps no cast list on a film page at all — "Frozen" is a plot summary
# and nothing else — but it files Elsa under "Frozen characters", Arendelle
# under "Frozen locations" and so on, which says the same thing more exactly.
_WORK_CONTENT_SUFFIX = re.compile(
    r"\b(characters?|locations?|places?|objects?|items?|artifacts?|weapons?"
    r"|vehicles?|events?|organi[sz]ations?|groups?|factions?|teams?"
    r"|species|creatures?|animals?|races?|deities|gods?"
    r"|terms?|terminology|concepts?|technology|magic|spells?|powers?"
    r"|abilities|techniques?)$", re.I)

# …and the ones named after a work that hold something else: its songs, its
# artwork, the real people who made it.
# Categories named after a work that hold the instalments it is told in. Those
# are evidence rather than entries: reading them is how a season with no cast
# list on its page still yields its cast.
_WORK_INSTALMENT_SUFFIX = re.compile(
    r"\b(episodes?|chapters?|volumes?|issues?|parts?)$", re.I)

_WORK_CONTENT_SKIP = re.compile(
    r"\b(people|cast|crew|actors?|actresses|voices?|songs?|music|albums?"
    r"|galler(y|ies)|images?|videos?|screenshots?|artwork|books?|comics?"
    r"|merchandise|toys?|games?|quotes?|trivia|awards?|relationships?"
    r"|episodes?|shorts?|attractions?)$", re.I)


def _work_names(title, wiki_name=""):
    """
    The names a wiki might file this work's categories under.

    A season is titled in full on its own page and abbreviated everywhere
    else: "My Hero Academia Season 1" keeps its episodes in
    `Category:Season 1 Episodes`, so looking only for the full name finds
    nothing at all.
    """
    title = (title or "").strip()
    names = [title]
    parent = scope.parent_name(wiki_name or "")
    if parent:
        match = re.match(rf"^{re.escape(parent)}[\s:/–—-]+(.+)$", title, re.I)
        if match and len(match.group(1).strip()) >= 3:
            names.append(match.group(1).strip())
    return list(dict.fromkeys(n for n in names if n))


def work_content_categories(api, title, wiki_name=""):
    """
    Pages the wiki files under this work by name.

    Returns (lore, instalments): things that belong in the story, and the
    episodes or chapters it is told in. The Disney wiki keeps no cast list on
    a film page at all — "Frozen" is 35 KB of plot — but files Elsa under
    "Frozen characters" and Arendelle under "Frozen locations".
    """
    lore_lists, seed_lists, bare_lists = [], [], []
    for name in _work_names(title, wiki_name):
        try:
            data = _call(api, {"action": "query", "list": "allcategories",
                               "acprefix": name, "aclimit": 100,
                               "acprop": "size"})
        except FandomError:
            continue
        for row in data.get("query", {}).get("allcategories", []):
            found = row.get("category") or ""
            rest = found[len(name):].strip()
            if not row.get("pages"):
                continue
            if not rest:
                # A category named exactly after the work. On some wikis that
                # is a mixed bag; on others it is the only thing there is —
                # Demon Slayer keeps a season's twenty-six episodes in it — so
                # it is read and sorted out by what the titles look like.
                bare_lists.append(found)
                continue
            # What is left has to be the whole of the suffix. "Season 1
            # (Vigilantes) Episodes" ends in "Episodes" too, and it belongs to
            # a different story entirely.
            if _WORK_INSTALMENT_SUFFIX.fullmatch(rest):
                seed_lists.append(found)
                continue
            if _WORK_CONTENT_SKIP.search(rest):
                continue
            if _WORK_CONTENT_SUFFIX.fullmatch(rest):
                lore_lists.append(found)

    def members(names, limit):
        pages = []
        for found in list(dict.fromkeys(names))[:limit]:
            try:
                pages += category_members(api, found, limit=200)
            except FandomError:
                continue
        return list(dict.fromkeys(pages))

    lore = members(lore_lists, MAX_WORK_CONTENT_LISTS)
    seeds = members(seed_lists, 2)
    if not lore and not seeds:
        for page in members(bare_lists, 2):
            if scope._INSTALMENT_RE.search(page):
                seeds.append(page)
            elif is_lorebook_page(page):
                lore.append(page)
    return lore, seeds


def build_work_scope(ref, info, title, raw, log=None):
    """
    Everything that turns up across a work's own instalments.

    The Invincible wiki's "Season 1" page lists a cast and stops there, so a
    lorebook built from it had thirty-three people in it and not one place,
    faction or event. Its eight episodes between them name a hundred and five
    things, ranked by how much of the season each appears in — which is the
    same measurement a sub-series gets from its chapters.

    Returns None when the page lists no instalments to read.
    """
    say = log.append if log is not None else (lambda _m: None)
    api = ref["api"]
    seeds, unit = instalment_links(raw)
    # Some wikis put nothing on a work's page and everything in categories
    # named after it. The Disney wiki is the extreme: "Frozen" is 35 KB of plot
    # with no cast list anywhere on it, while "Frozen characters" holds all
    # thirty-eight of them and "Frozen locations" holds Arendelle.
    direct, filed_seeds = work_content_categories(api, title, info.get("name"))
    if not seeds and filed_seeds:
        # A season page that lists neither cast nor episodes still has its
        # episodes filed under its name, and those name everybody in it.
        seeds, unit = filed_seeds[:MAX_WORK_SEEDS], "episodes"
    if len(seeds) < 2 and not direct:
        return None

    ident = {"title": title, "parent": scope.parent_name(info.get("name", "")),
             "short": "", "shorts": [], "aliases": {title},
             "production": set()}
    tally = scope.harvest(fetch_pages(api, seeds), seeds, ident) if seeds else {}
    for page in direct:
        tally.setdefault(page, {"seeds": 0, "mentions": 0, "debut": False})
    if not tally:
        return None
    if direct:
        say(f"The wiki files {len(direct)} pages under “{title}” by name.")

    seedset = set(seeds)
    wanted = [t for t in tally if scope._plausible(t, ident, seedset)]
    wanted.sort(key=lambda t: -tally[t]["seeds"])
    wanted = wanted[:scope.MAX_CANDIDATES]
    if seeds:
        say(f"Read {len(seeds)} instalments of “{title}”; "
            f"{len(wanted)} things appear in them.")

    canonical, categories, valid = _resolve_targets(api, wanted)
    merged = scope.merge_redirects({t: tally[t] for t in wanted}, canonical)
    merged = {t: row for t, row in merged.items()
              if scope._plausible(t, ident, seedset)}
    filed = {canonical.get(t, t) for t in direct}
    ranked = scope.rank(merged, ident, seeds, categories, valid, filed)
    if not ranked:
        return None
    shaped = scope.to_browse(ranked, ident, ref, info, seeds)
    shaped["scope"]["unit"] = unit or "instalments"
    shaped["scope"]["filed"] = len(direct)
    # A season filed as a subpage of its franchise is called by its own name:
    # "Murder House", not "American Horror Story/Murder House". The franchise
    # is the wiki, and repeating it in every heading says nothing.
    if "/" in title and is_wiki_subject(title.rsplit("/", 1)[0], info.get("name")):
        shaped["scope"]["series"] = title.rsplit("/", 1)[-1].strip()
    return shaped


def merge_browse(base, extra):
    """
    Fold a cast list into a derived scope, keeping both.

    The billed cast is the one thing a work page states outright, so it is
    never demoted by the counting: anybody on it is core whether they turn up
    in two episodes or eight. Everything the instalments turned up that the
    cast list did not — the places, the factions, the events — keeps the rank
    the counting gave it.
    """
    if not base or not base.get("groups"):
        return extra
    if not extra or not extra.get("groups"):
        return base

    groups = {g["category"]: g for g in base["groups"]}
    known = {p for g in base["groups"] for p in g["pages"]}
    for group in extra["groups"]:
        host = groups.get(group["category"])
        if host is None:
            host = dict(group, pages=[], minor=[], notes={}, tiers={},
                        subgroups=[], total=0)
            groups[host["category"]] = host
            base["groups"].append(host)
        for page in group["pages"]:
            tier = (group.get("tiers") or {}).get(page)
            note = (group.get("notes") or {}).get(page)
            if page not in known:
                host["pages"].append(page)
                host["total"] = len(host["pages"])
                known.add(page)
                if page in (group.get("minor") or []):
                    host.setdefault("minor", []).append(page)
            if tier:
                host.setdefault("tiers", {}).setdefault(page, tier)
            if note:
                host.setdefault("notes", {}).setdefault(page, note)
    # Anything the work itself named is core, whatever the counting said.
    for group in base["groups"]:
        tiers = group.setdefault("tiers", {})
        for page in group["pages"]:
            if page in known and page not in tiers:
                tiers[page] = "core"
    base["scope"] = base.get("scope") or extra.get("scope")
    return base


def is_wiki_subject(title, wiki_name):
    """
    True when a page is the thing the whole wiki is about.

    "Blue Lock (Manga)" on the Blue Lock wiki is not one story among many —
    it is *the* story, published in one format. Scoping to it means the whole
    wiki, so narrowing to pages whose titles start with its name is exactly
    wrong: it found fifteen of the wiki's two hundred and nineteen characters.
    """
    parent = scope.parent_name(wiki_name or "")
    if not parent:
        return False
    bare = re.sub(r"\s*\([^)]*\)\s*$", "", title or "").strip()
    return bare.lower() == parent.lower()


def is_series_page(infobox_type, raw, title, wiki_name):
    """
    True when a page is one story inside a wiki that covers several.

    The infobox settles what kind of page it is; the wiki's own name settles
    whether it is *the* story or a part of it. On the Jujutsu Kaisen wiki,
    "Jujutsu Kaisen" is the wiki's subject and "Jujutsu Kaisen Modulo" is a
    sub-series — the difference being that only the latter has a name of its
    own beyond the wiki's.
    """
    if not (infobox_type and _WORK_INFOBOX.search(infobox_type)):
        return False
    # A spin-off is named after the thing it spun off from — "My Hero Academia:
    # Vigilantes", "Jujutsu Kaisen Modulo". A film that merely has a colon in
    # its title is not one, and "Avengers: Endgame" was being taken for a
    # sub-series called "Endgame" and searched for across the whole wiki.
    parent = scope.parent_name(wiki_name)
    if not parent or not re.match(rf"^{re.escape(parent)}\b", title, re.I):
        return False
    return bool(scope.short_name(title, parent))


def build_scope(ref, info, title, raw, log=None):
    """
    Work out one sub-series' cast, places and events, browse-shaped.

    Returns None when the wiki keeps no instalment pages for this story, in
    which case there is nothing to measure scope against and the caller should
    fall back to whatever the page itself lists.
    """
    say = log.append if log is not None else (lambda _m: None)
    api = ref["api"]
    ident = scope.identity(title, info.get("name", ""), raw)

    found = scope.discover(
        lambda params: _call(api, params),
        lambda name: category_members(api, name, limit=500),
        ident)
    seeds, direct = found["seeds"], found["direct"]
    if len(seeds) < 2 and not direct:
        return None

    if seeds:
        say(f"Found {len(seeds)} instalments of “{ident['title']}”.")
    if direct:
        say(f"The wiki files {len(direct)} pages under this story by name.")

    tally = scope.harvest(fetch_pages(api, seeds), seeds, ident) if seeds else {}
    # Pages the wiki filed under the story are candidates in their own right,
    # even if no instalment happens to link them.
    for title in direct:
        tally.setdefault(title, {"seeds": 0, "mentions": 0, "debut": False})

    seedset = set(seeds)
    wanted = [t for t in tally if scope._plausible(t, ident, seedset)]
    wanted.sort(key=lambda t: -tally[t]["seeds"])
    wanted = wanted[:scope.MAX_CANDIDATES]
    if seeds:
        say(f"Read {len(seeds)} instalments; {len(wanted)} things appear in them.")

    canonical, categories, valid = _resolve_targets(api, wanted)
    merged = scope.merge_redirects({t: tally[t] for t in wanted}, canonical)
    direct = {canonical.get(t, t) for t in direct}
    # Redirects can land on a page the filters would have rejected under its
    # own name — "Simurian" resolves to "Simuria", an instalment-shaped title
    # on some wikis — so the plausibility test is re-applied after merging.
    merged = {t: row for t, row in merged.items()
              if scope._plausible(t, ident, seedset)}

    ranked = scope.rank(merged, ident, seeds, categories, valid, direct)
    if not ranked:
        return None
    say(f"Kept {len(ranked)} after dropping red links, indexes and credits.")

    return scope.to_browse(ranked, ident, ref, info, seeds)
