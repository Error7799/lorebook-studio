"""
Wikipedia as a first-class lorebook source — articles and list pages.

`scrapers/wiki.py` handles the *enrichment* side of Wikipedia: the REST
summary, Wikidata claims, social handles. What it never did was read the
article itself, so a real person came out as two sentences and a follower
count while a Fandom character came out with their whole history.

Wikipedia is MediaWiki, exactly like Fandom, so the fix is to point the same
machinery at it: pull raw wikitext, parse the infobox, keep the sections worth
keeping and budget them. `scrapers/wikitext.py` does all of that already and is
shared verbatim — which is also why a musician's Discography, Filmography,
"Awards and nominations", Notes and References drop out without any
Wikipedia-specific rule.

The other half is list pages. "List of hip-hop musicians" is not an entry and
never should be; it is a *menu* of three thousand articles, sectioned A–Z, and
what it deserves is the same picker a Fandom wiki's categories get.
"""

import re
import time
from urllib.parse import quote, unquote, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scrapers import wikitext as W

HEADERS = {"User-Agent": "LorebookStudio/1.0 (educational/personal use)"}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
_retry = Retry(total=3, backoff_factor=0.5,
               status_forcelist=(429, 500, 502, 503, 504),
               allowed_methods=("GET",))
SESSION.mount("https://", HTTPAdapter(max_retries=_retry, pool_maxsize=8))

TIMEOUT = 20
MAX_TITLES_PER_CALL = 40
MAX_BULK_PAGES = 100          # one "import selected" request
MAX_LIST_ITEMS = 6000         # a big A–Z list is several thousand names
MAX_GROUP_ITEMS = 1200        # per section

DEFAULT_BUDGET = 4200
LEAD_BUDGET = 900

_HOST_RE = re.compile(r"^([a-z-]+\.)?(m\.)?wikipedia\.org$", re.I)
_RESERVED_PREFIX = re.compile(
    r"^(category|special|user|talk|user talk|file|template|help|portal|draft|"
    r"wikipedia|mediawiki|module|book|timedtext)\s*:", re.I)


class WikipediaError(Exception):
    """Raised for URLs or pages that cannot become a lorebook entry."""


# ══════════════════════════════════════════════════════════════════════════
# URL handling
# ══════════════════════════════════════════════════════════════════════════

def parse_url(raw):
    """
    Split a Wikipedia URL into {api, site, host, lang, title}.

    Any language edition works; the API lives at the same path on all of them.
    """
    raw = (raw or "").strip().strip("<>\"'")
    if not raw:
        raise WikipediaError("No URL given.")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw

    parts = urlsplit(raw)
    host = parts.netloc.split("@")[-1].split(":")[0].lower()
    if not _HOST_RE.match(host):
        raise WikipediaError(f"“{host}” is not Wikipedia.")

    path = unquote(parts.path or "/")
    title = ""
    if path.startswith("/wiki/"):
        title = path[len("/wiki/"):]
    elif "title=" in (parts.query or ""):
        m = re.search(r"(?:^|&)title=([^&]+)", parts.query)
        if m:
            title = unquote(m.group(1))
    if not title:
        raise WikipediaError("Could not find an article name in that URL.")

    title = title.split("#")[0].strip().replace("_", " ").strip("/")
    if _RESERVED_PREFIX.match(title):
        raise WikipediaError(
            f"“{title}” is not an article — link to a Wikipedia article instead.")

    lang = host.split(".")[0] if host.count(".") > 1 else "en"
    site = f"{parts.scheme}://{host}"
    return {"api": f"{site}/w/api.php", "site": site, "host": host,
            "lang": lang, "title": title}


def is_wikipedia_url(raw):
    """True when a string looks like a Wikipedia link rather than a name."""
    try:
        text = (raw or "").strip()
        if not re.match(r"^https?://", text, re.I):
            text = "https://" + text
        host = urlsplit(text).netloc.split("@")[-1].split(":")[0].lower()
        return bool(_HOST_RE.match(host))
    except ValueError:
        return False


def is_wikipedia_api(url):
    """Re-validate an API endpoint handed back by the browser."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return False
    host = parts.netloc.split("@")[-1].split(":")[0].lower()
    return (parts.scheme in ("http", "https")
            and bool(_HOST_RE.match(host))
            and parts.path.endswith("/api.php"))


def _call(api, params):
    query = dict(params)
    query.setdefault("format", "json")
    query.setdefault("formatversion", "2")
    try:
        r = SESSION.get(api, params=query, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise WikipediaError(f"Could not reach Wikipedia: {exc}") from exc
    except ValueError as exc:
        raise WikipediaError("Wikipedia returned a response we could not read.") from exc


def fetch_pages(api, titles):
    """Wikitext + categories for many titles, batched. {title: record}."""
    out = {}
    titles = [t for t in titles if t]
    for i in range(0, len(titles), MAX_TITLES_PER_CALL):
        chunk = titles[i:i + MAX_TITLES_PER_CALL]
        data = _call(api, {
            "action": "query", "prop": "revisions|categories",
            "rvprop": "content", "rvslots": "main",
            "titles": "|".join(chunk), "redirects": 1,
            "cllimit": "max", "clshow": "!hidden",
        })
        query = data.get("query", {})
        normalized = {n["to"]: n["from"] for n in query.get("normalized", [])}
        redirects = {}
        for hop in query.get("redirects", []):
            redirects[normalized.get(hop["from"], hop["from"])] = hop["to"]

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
        for source, target in redirects.items():
            if target in records:
                out[source] = records[target]
        if len(titles) > MAX_TITLES_PER_CALL:
            time.sleep(0.2)
    return out


# ══════════════════════════════════════════════════════════════════════════
# List pages
# ══════════════════════════════════════════════════════════════════════════

_LIST_TITLE_RE = re.compile(r"^(lists? of|index of|outline of)\b", re.I)
_LIST_TEMPLATE_RE = re.compile(
    r"\{\{\s*(dynamic list|list of|compact toc|div col|columns-list)", re.I)

# `[[Target|Display]]` with the fragment kept, so section links can be told
# apart from articles.
_LINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]*))?\]\]")

# A list bullet, a table row cell, or a numbered item.
_ITEM_LINE_RE = re.compile(r"^\s*(?:[*#]+|\|)\s*(.+)$")

_SKIP_TARGET_RE = re.compile(
    r"^(file|image|category|template|help|portal|wikipedia|special|module"
    r"|draft|book|s|q|wikt|commons|c|w):", re.I)


def is_list_page(title, wikitext):
    """
    True when an article is an index of other articles rather than a subject.

    Title alone is not enough — "List of Star Wars characters" is a list, but
    so is "Hip-hop discography" without saying so — and the templates alone are
    not enough either, so both get a vote alongside the shape of the text.
    """
    if _LIST_TITLE_RE.match(title or ""):
        return True
    text = wikitext or ""
    if re.search(r"\{\{\s*dynamic list", text, re.I):
        return True
    # Thirty-odd bulleted links and almost nothing else is a list whatever it
    # calls itself.
    bullets = len(re.findall(r"^\s*\*\s*\[\[", text, re.M))
    return bullets >= 40 and bullets * 60 > len(text) * 0.25


def _items_from(body):
    """
    Article links from the list items in one section.

    Only the first link on a line is taken — list entries are annotated
    ("[[Nas]] (also a producer)") and the annotations link too.
    """
    items, seen = [], set()
    for raw in body.split("\n"):
        m = _ITEM_LINE_RE.match(raw)
        if not m:
            continue
        for target, display in _LINK_RE.findall(m.group(1)):
            target = target.strip()
            if not target or _SKIP_TARGET_RE.match(target):
                continue
            # "[[List of Wu-Tang Clan affiliates#12 O'Clock|12 O'Clock]]" has
            # no article of its own; importing it would file the whole
            # affiliates index under one member's name.
            if "#" in target:
                break
            label = W.clean(display or target).strip() or target
            key = target.lower()
            if key not in seen:
                seen.add(key)
                items.append({"title": target, "label": label})
            break                      # first link on the line wins
    return items


def browse_list(ref, title, wikitext, per_group=MAX_GROUP_ITEMS):
    """
    Turn a list article into the same pick-list shape a Fandom wiki produces.

    Sections become groups, so "List of hip-hop musicians" arrives as its own
    A–Z and the picker needs no special case.
    """
    lead, sections = W.iter_sections(wikitext)

    groups = []
    lead_items = _items_from(lead)
    if lead_items:
        groups.append(("Listed", lead_items))
    for section in sections:
        if W.is_blocked_section(section["title"]):
            continue
        items = _items_from(section["body"])
        if items:
            groups.append((section["title"] or "Listed", items))

    if not groups:
        return None

    total = sum(len(items) for _, items in groups)
    shaped = [{
        "category":  name,
        "total":     len(items),
        "pages":     [i["title"] for i in items[:per_group]],
        "labels":    {i["title"]: i["label"] for i in items[:per_group]
                      if i["label"] != i["title"]},
        "minor":     [],
        "notes":     {},
        "subgroups": [],
    } for name, items in groups]

    # An A–Z list is 27 chips, and the thing people actually want is to find
    # one name. A combined group up front makes the filter box search the
    # whole list instead of whichever letter happens to be open.
    if len(shaped) > 1 and total <= MAX_LIST_ITEMS:
        every, seen = [], set()
        for name, items in groups:
            for item in items:
                if item["title"].lower() in seen:
                    continue
                seen.add(item["title"].lower())
                every.append(item)
        shaped.insert(0, {
            "category":  "Everything",
            "total":     len(every),
            "pages":     [i["title"] for i in every],
            "labels":    {i["title"]: i["label"] for i in every
                          if i["label"] != i["title"]},
            "minor":     [], "notes": {}, "subgroups": [],
        })

    # The per-section counts double-count anyone filed under two letters, so
    # the headline number is the deduplicated one the "Everything" chip holds.
    unique = shaped[0]["total"] if shaped[0]["category"] == "Everything" else total

    return {
        "source":      "wikipedia",
        "wiki_name":   "Wikipedia",
        "site":        ref["site"],
        "api":         ref["api"],
        "source_page": title,
        "list": {"title": title, "groups": len(groups), "items": unique},
        "groups": shaped,
    }


def search(api, query, limit=20):
    """Full-text search of Wikipedia, for the box in the picker."""
    data = _call(api, {"action": "query", "list": "search", "srsearch": query,
                       "srnamespace": 0, "srlimit": limit, "srprop": "snippet"})
    results = []
    for hit in data.get("query", {}).get("search", []):
        snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", ""))
        results.append({"title": hit["title"], "snippet": snippet.strip()})
    return results


# ══════════════════════════════════════════════════════════════════════════
# Article → profile
# ══════════════════════════════════════════════════════════════════════════

# Infobox rows worth printing on a biography, in the order they read best.
# Anything not named here still survives if it passed the shared noise filter;
# this only fixes the running order of the ones that matter most.
_FACT_ORDER = [
    "birth name", "born", "birth date", "birth place", "died", "death date",
    "death place", "origin", "nationality", "citizenship", "education",
    "alma mater", "occupation", "occupations", "years active", "genre",
    "genres", "instrument", "instruments", "label", "labels",
    "associated acts", "past member of", "member of", "spouse", "partner",
    "children", "relatives", "height", "known for", "position", "team",
]
_FACT_RANK = {name: i for i, name in enumerate(_FACT_ORDER)}

_DISAMBIG_RE = re.compile(r"\s*\((?:rapper|singer|musician|record producer"
                          r"|band|group|American|British|artist|DJ)[^)]*\)\s*$", re.I)


def display_name(title):
    """
    'Ye (rapper)' → 'Ye'. Wikipedia disambiguates in the title; a lorebook
    should not, because nobody types the parenthetical in a roleplay message.
    """
    return _DISAMBIG_RE.sub("", title or "").strip() or title


def _aliases_from(rows, name, title):
    """Alternate names worth using as trigger words."""
    fields = ("birth name", "birthname", "other names", "othernames", "alias",
              "aliases", "nickname", "nicknames", "native name", "full name",
              "also known as", "ring names", "pseudonym")
    found = []
    for row in rows:
        if row["field"] in fields:
            for part in re.split(r"[,;]| and ", row["value"]):
                part = re.sub(r"\s*\([^)]*\)", "", part).strip(" .")
                if part and 1 < len(part) <= 60:
                    found.append(part)
    if title != name:
        found.append(title)
    return [a for a in dict.fromkeys(found) if a.lower() != name.lower()][:8]


def build_profile(ref, record, budget=DEFAULT_BUDGET, notes=""):
    """
    Turn one fetched Wikipedia article into a lorebook profile.

    Shaped like the Fandom profiles so the same formatter, entry list and
    export path handle both, but flagged `wikipedia` so the Wikidata and
    social-stats enrichment can still be merged in on top.
    """
    raw = record.get("wikitext") or ""
    title = record.get("title") or ref.get("title") or "Untitled"
    if re.match(r"^\s*#redirect", raw, re.I):
        raise WikipediaError(f"“{title}” is a redirect with no content.")

    infobox_type, rows = W.parse_infobox(raw)
    name = display_name(title)

    facts, seen = [], set()
    for row in rows:
        label = row["label"]
        if label.lower() in seen or not row["value"]:
            continue
        # The infobox repeats the article title in its "name" row; printing it
        # back under a heading that already says the name is pure noise.
        if row["field"] in ("name", "native name") and                 row["value"].strip().lower() in (name.lower(), title.lower()):
            continue
        seen.add(label.lower())
        facts.append({"label": label, "value": row["value"],
                      "field": row["field"]})
    facts.sort(key=lambda f: _FACT_RANK.get(f["field"], len(_FACT_ORDER)))

    lead_raw, sections = W.split_sections(raw)
    lead = W.trim_text(W.clean(lead_raw), LEAD_BUDGET)

    cleaned = W.fit_sections(_flatten(sections), budget)

    categories = [c for c in record.get("categories", [])
                  if not _NOISE_CATEGORY.search(c)][:12]

    profile = {
        "source":          "wikipedia",
        "name":            name,
        "wikipedia_title": title,
        "wiki_name":       "Wikipedia",
        "site":            ref.get("site", "https://en.wikipedia.org"),
        "page_title":      title,
        "page_url":        f"{ref.get('site', 'https://en.wikipedia.org')}"
                           f"/wiki/{quote(title.replace(' ', '_'))}",
        "infobox_type":    infobox_type or "",
        "aliases":         _aliases_from(rows, name, title),
        "facts":           [{"label": f["label"], "value": f["value"]}
                            for f in facts],
        "extract":         lead,
        "description":     _short_description(raw) or _first_sentence(lead),
        "sections":        cleaned,
        "wikipedia_categories": categories,
    }
    if notes:
        profile["notes"] = notes
    return profile


_flatten = W.flatten_sections


def _short_description(wikitext):
    """
    Wikipedia's own one-line summary, from {{Short description|…}}.

    It is written to be exactly this — "American rapper and actor (born
    1975)" — and beats the article's first sentence, which is usually a
    hundred words of birth name, dates and honorifics.
    """
    m = re.search(r"\{\{\s*short ?description\s*\|([^}|]+)", wikitext or "", re.I)
    if not m:
        return ""
    text = W.clean(m.group(1)).strip()
    return "" if text.lower() in ("none", "") else text[:200]


def _first_sentence(text, limit=240):
    if not text:
        return ""
    first = re.split(r"(?<=[.!?])\s+", text.strip().split("\n")[0])[0]
    return first[:limit].strip()


# Wikipedia's own housekeeping categories. They outnumber the real ones on a
# well-maintained article and none of them describe the subject.
_NOISE_CATEGORY = re.compile(
    r"^(articles?|pages?|wikipedia|cs1|use |all |webarchive|short description"
    r"|commons category|good articles|featured articles|biography with"
    r"|blp |wikidata|template|redirects?|engvar|dmy|mdy|interlanguage"
    r"|harv|source attribution|official website|isbn|orphaned|unprinted)",
    re.I)


def scrape_titles(api, site, titles, budget=DEFAULT_BUDGET):
    """
    Scrape many Wikipedia articles at once.
    Returns (profiles, [(title, reason), …]) so the UI can report skips.
    """
    titles = list(dict.fromkeys(titles))[:MAX_BULK_PAGES]
    ref = {"api": api, "site": site}
    records = fetch_pages(api, titles)

    profiles, skipped = [], []
    for title in titles:
        record = records.get(title)
        if not record or record.get("missing"):
            skipped.append((title, "article not found"))
            continue
        try:
            profiles.append(build_profile(ref, record, budget))
        except WikipediaError as exc:
            skipped.append((title, str(exc)))
        except Exception as exc:
            skipped.append((title, f"could not parse ({exc})"))
    return profiles, skipped
