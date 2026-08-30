"""
Wikipedia + Wikidata scraper.
Uses only public APIs — no LLM calls, no auth required.

All requests go through one shared Session with connection pooling and
automatic retries on transient failures (429 / 5xx), which makes multi-call
scrapes noticeably faster and far more reliable.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib.parse import quote
from urllib3.util.retry import Retry

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKI_REST = "https://en.wikipedia.org/api/rest_v1"

HEADERS = {"User-Agent": "LorebookStudio/1.0 (educational/personal use)"}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
_retry = Retry(total=3, backoff_factor=0.5,
               status_forcelist=(429, 500, 502, 503, 504),
               allowed_methods=("GET",))
SESSION.mount("https://", HTTPAdapter(max_retries=_retry, pool_maxsize=8))

# Wikidata property IDs → field names
PROP_MAP = {
    "P569": "date_of_birth",
    "P19":  "place_of_birth",
    "P21":  "gender",
    "P27":  "citizenship",
    "P106": "occupation",
    "P2048":"height_cm",
    "P856": "official_website",
    "P571": "founded",
    "P112": "founders",
    "P452": "industry",
    "P136": "genres",
    "P172": "ethnicity",
    "P1477":"birth_name",
    "P641": "sport",
    "P495": "country_of_origin",
    "P17":  "country",
    "P1340":"eye_color",
    "P1884":"hair_color",
}

# Props whose values are Q-IDs that need label resolution
Q_LABEL_PROPS = {"P19","P21","P27","P106","P112","P452","P136","P172","P641","P495","P17","P1340","P1884"}

# Props that should return a list (not just first value)
LIST_PROPS = {"P106","P112","P136","P172"}

# Wikidata social media handle properties → platform name
SOCIAL_PROPS = {
    "P2003": "instagram",
    "P2002": "twitter",
    "P4253": "tiktok",
    "P2397": "youtube",
    "P2013": "facebook",
    "P1902": "spotify",
    "P3984": "subreddit",
}

# URL templates for Wikidata-sourced social handles
SOCIAL_URL_TPL = {
    "instagram": "https://www.instagram.com/{}",
    "twitter":   "https://x.com/{}",
    "tiktok":    "https://www.tiktok.com/@{}",
    "youtube":   "https://www.youtube.com/channel/{}",
    "facebook":  "https://www.facebook.com/{}",
    "spotify":   "https://open.spotify.com/artist/{}",
}


def _batch_labels(qids, lang="en"):
    """Resolve Q-IDs to labels, batched 50-per-request (the Wikidata max)."""
    if not qids:
        return {}
    label_map = {}
    for i in range(0, len(qids), 50):
        chunk = qids[i:i+50]
        params = {
            "action": "wbgetentities",
            "ids":    "|".join(chunk),
            "props":  "labels",
            "languages": lang,
            "format": "json",
        }
        try:
            r = SESSION.get(WIKIDATA_API, params=params, timeout=12)
            r.raise_for_status()
            for qid, ent in r.json().get("entities", {}).items():
                label = ent.get("labels", {}).get(lang, {}).get("value")
                label_map[qid] = label if label else qid
        except Exception:
            for qid in chunk:
                label_map[qid] = qid
    return label_map


def _extract_value(claim):
    """Pull a Python-native value out of one Wikidata claim snak."""
    try:
        dv = claim.get("mainsnak", {}).get("datavalue", {})
        dtype = dv.get("type")
        val = dv.get("value")
        if dtype == "string":
            return val
        if dtype == "time":
            t = val.get("time", "")
            return t[1:11] if t else None          # YYYY-MM-DD
        if dtype == "wikibase-entityid":
            return val.get("id")                    # Q-number
        if dtype == "quantity":
            return str(val.get("amount", "0")).lstrip("+")
        if dtype == "monolingualtext":
            return val.get("text")
    except Exception:
        pass
    return None


def _parse_claims(entity):
    """
    Convert raw Wikidata claims to a clean profile dict.
    Uses a single batched API call to resolve all Q-IDs to labels,
    avoiding the N×HTTP-request problem of per-ID resolution.
    """
    claims = entity.get("claims", {})

    # ── Pass 1: extract raw values, collect Q-IDs that need labels ───────────
    raw = {}
    q_ids = set()
    for pid, field in PROP_MAP.items():
        if pid not in claims:
            continue
        vals = []
        for claim in claims[pid]:
            v = _extract_value(claim)
            if v:
                vals.append(v)
                if pid in Q_LABEL_PROPS and isinstance(v, str) and v.startswith("Q"):
                    q_ids.add(v)
        raw[pid] = vals

    # ── Extract Wikidata social media handles (string props, no label needed) ─
    wikidata_social = {}
    for pid, platform in SOCIAL_PROPS.items():
        if pid not in claims:
            continue
        for claim in claims[pid]:
            v = _extract_value(claim)
            if v and isinstance(v, str):
                wikidata_social[platform] = v
                break

    # ── Batch-resolve all Q-IDs in one API round-trip ────────────────────────
    label_map = _batch_labels(sorted(q_ids))

    # ── Pass 2: build result with resolved labels ─────────────────────────────
    result = {}
    for pid, field in PROP_MAP.items():
        vals = raw.get(pid, [])
        if not vals:
            continue
        resolved = []
        for v in vals:
            if pid in Q_LABEL_PROPS and isinstance(v, str) and v.startswith("Q"):
                v = label_map.get(v, v)
            resolved.append(v)
        result[field] = resolved if pid in LIST_PROPS else resolved[0]

    if wikidata_social:
        result["wikidata_social"] = wikidata_social

    return result


def search_wikipedia(query):
    """Return up to 5 (title, url) pairs matching the query."""
    params = {
        "action": "opensearch",
        "search": query,
        "limit": 5,
        "format": "json",
    }
    r = SESSION.get(WIKIPEDIA_API, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    titles = data[1] if len(data) > 1 else []
    urls   = data[3] if len(data) > 3 else []
    return list(zip(titles, urls))


def _wiki_summary(title):
    """Wikipedia REST summary endpoint — fast, clean JSON."""
    url = f"{WIKI_REST}/page/summary/{quote(title)}"
    try:
        r = SESSION.get(url, timeout=12)
        if r.status_code == 200:
            d = r.json()
            return {
                "title":         d.get("title"),
                "extract":       d.get("extract"),
                "description":   d.get("description"),
                "thumbnail":     (d.get("thumbnail") or {}).get("source"),
                "wikipedia_url": (d.get("content_urls") or {}).get("desktop", {}).get("page"),
            }
    except Exception:
        pass
    return {}


def _wikidata_entity(title):
    """Fetch Wikidata entity for a given Wikipedia article title."""
    params = {
        "action": "wbgetentities",
        "sites":  "enwiki",
        "titles": title,
        "props":  "claims|descriptions",
        "format": "json",
        "languages": "en",
    }
    try:
        r = SESSION.get(WIKIDATA_API, params=params, timeout=12)
        r.raise_for_status()
        entities = r.json().get("entities", {})
        if not entities:
            return None
        entity = list(entities.values())[0]
        return None if entity.get("id") == "-1" else entity
    except Exception:
        return None


def _wiki_categories(title):
    """Fetch Wikipedia page categories, filtering out maintenance cats."""
    SKIP = ("Wikipedia", "Wikidata", "CS1", "Articles", "Pages", "All ", "Use ")
    params = {
        "action": "query",
        "prop":   "categories",
        "titles": title,
        "cllimit": 30,
        "format": "json",
    }
    try:
        r = SESSION.get(WIKIPEDIA_API, params=params, timeout=10)
        pages = r.json().get("query", {}).get("pages", {})
        cats = []
        for page in pages.values():
            for cat in page.get("categories", []):
                name = cat.get("title", "").replace("Category:", "")
                if not any(name.startswith(s) for s in SKIP):
                    cats.append(name)
        return cats
    except Exception:
        return []


def _wiki_extlinks(title):
    """Fetch external links from a Wikipedia page (for social-handle discovery)."""
    params = {
        "action": "query",
        "prop":   "extlinks",
        "titles": title,
        "ellimit": 100,
        "format": "json",
    }
    try:
        r = SESSION.get(WIKIPEDIA_API, params=params, timeout=10)
        pages = r.json().get("query", {}).get("pages", {})
        links = []
        for page in pages.values():
            for link in page.get("extlinks", []):
                links.append(link.get("*", ""))
        return links
    except Exception:
        return []


def _wiki_lead_text(title):
    """
    Fetch the plain-text lead section of a Wikipedia article.
    Longer than the REST summary extract — better for appearance scanning.
    """
    params = {
        "action":     "query",
        "prop":       "extracts",
        "exintro":    True,
        "explaintext": True,
        "titles":     title,
        "format":     "json",
    }
    try:
        r = SESSION.get(WIKIPEDIA_API, params=params, timeout=12)
        pages = r.json().get("query", {}).get("pages", {})
        for page in pages.values():
            return page.get("extract", "")
    except Exception:
        pass
    return ""


def scrape_entity(name):
    """
    Master scrape for one entity name.
    Returns a dict with keys:
      wikipedia_title, wikidata_qid, summary, wikidata, categories,
      external_links, search_results
    or None if nothing found.
    """
    hits = search_wikipedia(name)
    if not hits:
        return None

    best_title = hits[0][0]

    summary   = _wiki_summary(best_title)
    lead_text = _wiki_lead_text(best_title)
    wd_entity = _wikidata_entity(best_title)
    cats      = _wiki_categories(best_title)
    ext_links = _wiki_extlinks(best_title)

    wd_data = {}
    wd_qid  = None
    if wd_entity:
        wd_data = _parse_claims(wd_entity)
        wd_qid  = wd_entity.get("id")
        wd_desc = (
            wd_entity.get("descriptions", {})
                     .get("en", {})
                     .get("value", "")
        )
        if wd_desc and not summary.get("description"):
            summary["description"] = wd_desc

    return {
        "wikipedia_title": best_title,
        "wikidata_qid":    wd_qid,
        "summary":         summary,
        "lead_text":       lead_text,
        "wikidata":        wd_data,
        "categories":      cats,
        "external_links":  ext_links,
        "search_results":  [{"title": t, "url": u} for t, u in hits],
    }
