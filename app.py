"""
Lorebook Studio — Maker + Editor in one local web app.

* Maker  (/maker)  — scrapes Wikipedia/Wikidata + social profiles to generate
  SillyTavern-compatible World Info (lorebook) JSON. No LLM/AI APIs used.
* Editor (/editor) — bulk-edit an existing lorebook: activation strategies
  (Normal / Constant / Selective / Vectorized), filters, find & replace, undo.

Run:
    pip install -r requirements.txt
    python app.py            # or: python3 app.py on macOS/Linux
Then open http://127.0.0.1:5000
"""

import copy
import io
import json
import os
import re
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path

from flask import (Flask, jsonify, make_response, redirect, render_template,
                   request, send_file, session)

from scrapers.wiki import scrape_entity, search_wikipedia, SOCIAL_URL_TPL
from scrapers.social import find_social_handles, gather_social_stats
from scrapers import fandom
from scrapers import wikipedia as wp
from scrapers.fandom import FandomError
from scrapers.wikipedia import WikipediaError
from core.classifier import classify_entity
from core.aggregator import aggregate_entity_data
from core.formatter import profile_to_entry, profiles_to_lorebook
from core import lorebook as LB
from core import health as HEALTH

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
# A local tool is edited while it is running; caching the templates only ever
# means a restart to see a change that is already on disk.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))


# ==========================================================================
# EDITOR — in-memory state. Single-user local tool, so module globals are
# fine and keep things simple.
# ==========================================================================
STATE = {
    "data": None,       # the full raw lorebook dict (preserves every field)
    "path": None,       # absolute path we loaded from (for save-in-place)
    "name": None,       # display name
    "dirty": False,     # unsaved changes?
}

# Undo history: snapshots of STATE["data"] taken before each mutation.
UNDO: list = []
UNDO_LIMIT = 30

STRATEGIES = ("normal", "constant", "selective", "vectorized")


def snapshot():
    """Push a deep copy of the current lorebook onto the undo stack."""
    if STATE["data"] is not None:
        UNDO.append(copy.deepcopy(STATE["data"]))
        del UNDO[:-UNDO_LIMIT]


def detect_strategy(entry: dict) -> str:
    """Return the strategy name for an entry from its boolean fields."""
    if entry.get("constant"):
        return "constant"
    if entry.get("vectorized"):
        return "vectorized"
    if entry.get("selective"):
        return "selective"
    return "normal"


def apply_strategy(entry: dict, strategy: str) -> None:
    """Set the boolean fields on an entry to match a strategy name."""
    entry["constant"] = strategy == "constant"
    entry["selective"] = strategy == "selective"
    entry["vectorized"] = strategy == "vectorized"


def entries_dict() -> dict:
    """The raw {uid_str: entry} mapping from the loaded lorebook."""
    if not STATE["data"]:
        return {}
    return STATE["data"].get("entries", {})


def entry_by_uid(uid):
    """Find an entry (and its dict key) by uid, tolerant of int/str."""
    ents = entries_dict()
    key = str(uid)
    if key in ents:
        return key, ents[key]
    for k, v in ents.items():
        if str(v.get("uid")) == key:
            return k, v
    return None, None


def summarize(entry: dict) -> dict:
    """A lightweight view of an entry for the table (content trimmed)."""
    content = entry.get("content", "") or ""
    comment = entry.get("comment", "") or ""
    return {
        "uid": entry.get("uid"),
        "comment": comment,
        "key": entry.get("key", []) or [],
        "keysecondary": entry.get("keysecondary", []) or [],
        "strategy": detect_strategy(entry),
        "disable": bool(entry.get("disable", False)),
        "order": entry.get("order", 100),
        "depth": entry.get("depth", 4),
        "position": entry.get("position", 0),
        "probability": entry.get("probability", 100),
        "selectiveLogic": entry.get("selectiveLogic", 0),
        "group": entry.get("group", "") or "",
        "content_len": len(content),
        "content_preview": content[:160],
        "prefix": (comment.split(":")[0].strip() if ":" in comment else ""),
    }


def _resolve_path(raw: str) -> Path:
    """Normalise a user-supplied path: strip quotes, expand ~ (macOS/Linux)."""
    return Path(raw.strip().strip('"').strip("'")).expanduser()


# ==========================================================================
# MAKER — per-browser-session profile store
# ==========================================================================
MAKER_STORE: dict = {}


def _store():
    """Get (or create) the profile store for the current browser session."""
    sid = session.get("sid")
    if not sid:
        sid = secrets.token_hex(16)
        session["sid"] = sid
    store = MAKER_STORE.setdefault(sid, {})
    # A session opened before a lorebook was ever loaded has no place to put
    # one, and setdefault keeps that from being an error the user sees.
    store.setdefault("profiles", [])     # scraped, rendered at export time
    store.setdefault("existing", [])     # entries loaded from a lorebook, as-is
    store.setdefault("book", None)       # that lorebook's own top-level fields
    store.setdefault("book_name", "")
    return store


def _safe_profile_summary(p):
    """Return a small summary dict safe to send to the browser."""
    return {
        "_id":             p.get("_id"),
        "name":            p.get("name"),
        "category":        p.get("category"),
        "source":          p.get("source", "wikipedia"),
        "description":     (p.get("description") or "")[:120],
        "wikipedia_title": p.get("wikipedia_title"),
        "wiki_name":       p.get("wiki_name"),
        "page_url":        p.get("page_url"),
        "social_count":    len(p.get("social", {})),
        "has_dob":         bool(p.get("date_of_birth")),
        "has_height":      bool(p.get("height")),
        "fact_count":      len(p.get("facts", [])),
        "section_count":   len(p.get("sections", [])),
        "image":           p.get("image") or "",
        "relates_to":      p.get("relates_to") or "",
    }


def _existing_summary(entry):
    """Summarise a loaded lorebook entry the way the entry list expects."""
    keys = entry.get("key") or []
    if isinstance(keys, str):
        keys = [keys]
    content = entry.get("content") or ""
    return {
        "_id":           entry.get("_id"),
        "name":          LB.entry_name(entry),
        "category":      LB.entry_category(entry),
        "source":        "existing",
        "description":   re.sub(r"\s+", " ", content).strip()[:120],
        "key_count":     len(keys),
        "char_count":    len(content),
        "disabled":      bool(entry.get("disable")),
        "group":         entry.get("group") or "",
    }


def _profile_identity(p):
    """
    A stable identity for de-duplication.

    Fandom pages are identified by URL and Wikipedia entities by article
    title; without the source prefix, two profiles that both lack a title
    would collide and overwrite each other.
    """
    if p.get("source") == "fandom":
        return ("fandom", (p.get("page_url") or p.get("page_title") or "").lower())
    return ("wikipedia", (p.get("wikipedia_title") or p.get("name") or "").lower())


def _store_profile(store, profile, name_hint=""):
    """Add a profile to the session, replacing any earlier scrape of the same page."""
    profile["_id"] = f"{name_hint or profile.get('name', 'entry')}_{int(time.time() * 1000)}"
    identity = _profile_identity(profile)
    for i, existing in enumerate(store["profiles"]):
        if _profile_identity(existing) == identity:
            store["profiles"][i] = profile
            return False
    store["profiles"].append(profile)
    return True


# Detail level → per-entry character budget. SillyTavern budgets tokens per
# entry, so a whole 90 KB story arc has to be summarised to something usable.
#
# The ceilings are set against what a character on a well-kept wiki actually
# has: Koichi Haimawari's synopsis alone is 142 KB told across eleven arcs, so
# a budget that leaves each arc a couple of sentences produces a table of
# contents rather than a story. "Detailed" is sized to give every arc a real
# paragraph; narrowing to a few arcs in the chooser spends the same budget on
# fewer of them and goes deeper still.
DETAIL_BUDGETS = {"compact": 2000, "standard": 6000, "detailed": 14000}


def _budget_for(body):
    return DETAIL_BUDGETS.get((body.get("detail") or "standard").lower(),
                              DETAIL_BUDGETS["standard"])


def _subpages_for(body):
    """
    Whether to pull a page's tabbed subpages (its Synopsis, History…).

    On by default: a character's story is usually the half of them worth
    having, and it costs one extra request for a whole batch.
    """
    return bool(body.get("subpages", True))


def _arcs_for(body):
    """
    Which story arcs to keep from a page's synopsis, or None for all of them.

    An empty list means the user unticked every arc, which is a real choice —
    no story at all — and must not be read as "no filter".
    """
    arcs = body.get("arcs")
    if arcs is None:
        return None
    return {fandom.arc_key(a) for a in arcs if str(a).strip()}


def _safe_filename(name: str) -> str:
    safe = re.sub(r"[^\w\- ]", "", name).strip().replace(" ", "_").lower()
    return safe or "lorebook"


# ==========================================================================
# Pages
# ==========================================================================
@app.route("/")
def index():
    return redirect("/maker")


@app.route("/maker")
def maker_page():
    return render_template("maker.html")


@app.route("/editor")
def editor_page():
    return render_template("editor.html")


# ==========================================================================
# EDITOR API
# ==========================================================================
@app.route("/api/editor/load", methods=["POST"])
def api_load():
    """Load a lorebook from an uploaded file or a server-side path."""
    raw = None
    name = None
    path = None

    if "file" in request.files and request.files["file"].filename:
        f = request.files["file"]
        name = f.filename
        raw = f.read().decode("utf-8-sig")
        path = None  # uploaded file has no on-disk path we should overwrite
    else:
        body = request.get_json(silent=True) or {}
        p = _resolve_path(body.get("path", ""))
        if not str(p) or str(p) == ".":
            return jsonify({"error": "No file uploaded and no path given."}), 400
        if not p.is_file():
            return jsonify({"error": f"File not found: {p}"}), 404
        raw = p.read_text(encoding="utf-8-sig")
        name = p.name
        path = str(p)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Invalid JSON: {e}"}), 400

    if not isinstance(data, dict) or "entries" not in data:
        return jsonify({"error": "Not a SillyTavern lorebook (no 'entries' key)."}), 400

    UNDO.clear()
    STATE.update(data=data, path=path, name=name, dirty=False)
    return jsonify({"ok": True, "name": name, "path": path,
                    "count": len(entries_dict())})


@app.route("/api/editor/entries")
def api_entries():
    return jsonify({
        "name": STATE["name"],
        "path": STATE["path"],
        "dirty": STATE["dirty"],
        "can_undo": bool(UNDO),
        "entries": [summarize(e) for e in entries_dict().values()],
    })


@app.route("/api/editor/entry/<uid>")
def api_entry(uid):
    """Full detail for a single entry (for the editor drawer)."""
    _, entry = entry_by_uid(uid)
    if entry is None:
        return jsonify({"error": "Entry not found"}), 404
    return jsonify(entry)


@app.route("/api/editor/entry/<uid>", methods=["POST"])
def api_entry_update(uid):
    """Patch arbitrary fields on one entry."""
    _, entry = entry_by_uid(uid)
    if entry is None:
        return jsonify({"error": "Entry not found"}), 404
    patch = request.get_json(silent=True) or {}

    snapshot()
    if "strategy" in patch:
        apply_strategy(entry, patch.pop("strategy"))
    for field, value in patch.items():
        entry[field] = value

    STATE["dirty"] = True
    return jsonify({"ok": True, "entry": summarize(entry)})


@app.route("/api/editor/bulk", methods=["POST"])
def api_bulk():
    """
    Apply one operation to many entries at once.

    Body: { "uids": [...], "op": "...", ...op-specific args }
    ops: set_strategy, enable, disable, set_field, find_replace, delete
    """
    body = request.get_json(silent=True) or {}
    op = body.get("op")
    uids = body.get("uids", [])
    ents = entries_dict()

    targets = []  # list of (dict_key, entry)
    wanted = {str(u) for u in uids}
    for k, v in ents.items():
        if str(v.get("uid")) in wanted or k in wanted:
            targets.append((k, v))

    changed = 0

    if op == "set_strategy":
        strat = body.get("strategy")
        if strat not in STRATEGIES:
            return jsonify({"error": f"Unknown strategy: {strat}"}), 400
        snapshot()
        for _, e in targets:
            apply_strategy(e, strat)
            changed += 1

    elif op in ("enable", "disable"):
        snapshot()
        for _, e in targets:
            e["disable"] = (op == "disable")
            changed += 1

    elif op == "set_field":
        field = body.get("field")
        value = body.get("value")
        snapshot()
        for _, e in targets:
            e[field] = value
            changed += 1

    elif op == "find_replace":
        find = body.get("find", "")
        repl = body.get("replace", "")
        fields = body.get("fields", ["content"])
        use_regex = bool(body.get("regex"))
        if not find:
            return jsonify({"error": "Empty search string"}), 400
        if use_regex:
            try:
                pattern = re.compile(find)
            except re.error as e:
                return jsonify({"error": f"Bad regex: {e}"}), 400
            do = lambda s: pattern.sub(repl, s)
            has = lambda s: bool(pattern.search(s))
        else:
            do = lambda s: s.replace(find, repl)
            has = lambda s: find in s
        snapshot()
        for _, e in targets:
            hit = False
            for field in fields:
                val = e.get(field)
                if isinstance(val, str) and has(val):
                    e[field] = do(val)
                    hit = True
                elif isinstance(val, list):  # key / keysecondary
                    new = [do(x) if isinstance(x, str) else x for x in val]
                    if new != val:
                        e[field] = new
                        hit = True
            if hit:
                changed += 1

    elif op == "delete":
        snapshot()
        for k, _ in targets:
            ents.pop(k, None)
            changed += 1

    else:
        return jsonify({"error": f"Unknown op: {op}"}), 400

    if changed:
        STATE["dirty"] = True
    elif UNDO:
        UNDO.pop()  # op made no changes; drop this request's snapshot
    return jsonify({"ok": True, "changed": changed})


@app.route("/api/editor/undo", methods=["POST"])
def api_undo():
    """Restore the lorebook to the state before the last mutation."""
    if not UNDO:
        return jsonify({"error": "Nothing to undo"}), 400
    STATE["data"] = UNDO.pop()
    STATE["dirty"] = True
    return jsonify({"ok": True, "can_undo": bool(UNDO)})


@app.route("/api/editor/add", methods=["POST"])
def api_add():
    """Create a new blank entry with sane defaults."""
    ents = entries_dict()
    if STATE["data"] is None:
        return jsonify({"error": "Nothing loaded"}), 400
    body = request.get_json(silent=True) or {}
    snapshot()
    new_uid = max([int(v.get("uid", 0)) for v in ents.values()], default=-1) + 1
    entry = {
        "uid": new_uid, "key": body.get("key", []), "keysecondary": [],
        "comment": body.get("comment", "New Entry"), "content": body.get("content", ""),
        "constant": False, "vectorized": False, "selective": True, "selectiveLogic": 0,
        "addMemo": True, "order": 100, "position": 0, "disable": False,
        "excludeRecursion": False, "preventRecursion": False,
        "delayUntilRecursion": False, "probability": 100, "useProbability": True,
        "depth": 4, "group": "", "groupOverride": False, "groupWeight": 100,
        "scanDepth": None, "caseSensitive": None, "matchWholeWords": None,
        "useGroupScoring": None, "automationId": "", "role": None,
        "sticky": 0, "cooldown": 0, "delay": 0, "displayIndex": len(ents),
    }
    if body.get("strategy"):
        apply_strategy(entry, body["strategy"])
    ents[str(new_uid)] = entry
    STATE["dirty"] = True
    return jsonify({"ok": True, "entry": summarize(entry)})


@app.route("/api/editor/stats")
def api_stats():
    ents = entries_dict()
    from collections import Counter
    strat = Counter()
    prefix = Counter()
    disabled = 0
    total_chars = 0
    for e in ents.values():
        strat[detect_strategy(e)] += 1
        if e.get("disable"):
            disabled += 1
        cm = e.get("comment") or ""
        prefix[cm.split(":")[0].strip() if ":" in cm else "(none)"] += 1
        total_chars += len(e.get("content") or "")
    return jsonify({
        "count": len(ents),
        "by_strategy": dict(strat),
        "by_prefix": dict(prefix),
        "disabled": disabled,
        "total_chars": total_chars,
        "approx_tokens": total_chars // 4,  # rough heuristic
    })


@app.route("/api/editor/duplicates")
def api_duplicates():
    """Find entries whose primary keys overlap — a common lorebook footgun."""
    ents = entries_dict()
    key_map = {}
    for e in ents.values():
        for k in (e.get("key") or []):
            key_map.setdefault(k.lower().strip(), []).append(
                {"uid": e.get("uid"), "comment": e.get("comment", "")})
    dupes = {k: v for k, v in key_map.items() if len(v) > 1}
    return jsonify({"duplicates": dupes})


@app.route("/api/editor/save", methods=["POST"])
def api_save():
    """
    Save the lorebook. mode=download returns a file; mode=inplace overwrites
    the original path after backing it up.
    """
    if not STATE["data"]:
        return jsonify({"error": "Nothing loaded"}), 400
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "download")

    text = json.dumps(STATE["data"], ensure_ascii=False, indent=4)

    if mode == "inplace":
        path = STATE["path"]
        if not path:
            return jsonify({"error": "This lorebook was uploaded; use Download."}), 400
        backup = f"{path}.bak-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(path, backup)
        Path(path).write_text(text, encoding="utf-8")
        STATE["dirty"] = False
        return jsonify({"ok": True, "saved": path,
                        "backup": os.path.basename(backup)})

    # download
    buf = io.BytesIO(text.encode("utf-8"))
    fname = STATE["name"] or "lorebook.json"
    STATE["dirty"] = False
    return send_file(buf, mimetype="application/json",
                     as_attachment=True, download_name=fname)


# ==========================================================================
# MAKER API
# ==========================================================================
@app.route("/api/maker/search", methods=["POST"])
def api_search():
    """Quick Wikipedia title search — used for the autocomplete dropdown."""
    query = (request.json or {}).get("query", "").strip()
    if not query:
        return jsonify({"error": "No query"}), 400
    try:
        hits = search_wikipedia(query)
        return jsonify({"results": [{"title": t, "url": u} for t, u in hits]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/maker/build", methods=["POST"])
def api_build():
    """
    Scrape an entity and add it to the session's lorebook.
    Body JSON: { name, category?, notes?, wiki_title? }
    """
    body = request.json or {}
    name = body.get("name", "").strip()
    category = body.get("category", "").strip()
    notes = body.get("notes", "").strip()
    wiki_title = body.get("wiki_title", "").strip()

    if not name:
        return jsonify({"error": "name is required"}), 400

    store = _store()
    log = []

    try:
        # 1 — Wikipedia + Wikidata
        log.append(f"Searching Wikipedia for \"{wiki_title or name}\"...")
        wiki_data = scrape_entity(wiki_title or name)
        if not wiki_data:
            return jsonify({"error": f"No Wikipedia article found for \"{name}\"",
                            "log": log}), 404
        log.append(f"Found article: {wiki_data['wikipedia_title']}  "
                   f"(QID: {wiki_data.get('wikidata_qid', '—')})")

        # 2 — Social handles: Wikipedia external links + Wikidata social props
        log.append("Scanning for social media handles...")
        handles = find_social_handles(wiki_data.get("external_links", []))
        wd_social = wiki_data.get("wikidata", {}).get("wikidata_social", {})
        for platform, handle in wd_social.items():
            if platform not in handles:
                url = SOCIAL_URL_TPL.get(platform, "").format(handle)
                handles[platform] = {"handle": handle, "url": url}

        if handles:
            log.append(f"Found handles on: {', '.join(handles)}")
        else:
            log.append("No social handles found.")

        # 3 — Live social stats
        social_data = {}
        if handles:
            log.append("Fetching live social stats...")
            social_data = gather_social_stats(handles)
            for platform, data in social_data.items():
                if data.get("scraped"):
                    followers = data.get("followers") or data.get("subscribers", "—")
                    log.append(f"  {platform}: {followers}")
                else:
                    log.append(f"  {platform}: {data.get('note', 'could not scrape')}")

        # 4 — Auto-classify if not provided by user
        if not category:
            log.append("Auto-classifying entity...")
            wd = wiki_data.get("wikidata", {})
            category = classify_entity(
                name,
                wiki_data.get("summary", {}).get("description", ""),
                wiki_data.get("categories", []),
                wd.get("occupation", []),
            )
        log.append(f"Category: {category}")

        # 5 — Aggregate
        log.append("Building profile...")
        profile = aggregate_entity_data(name, wiki_data, social_data, category, notes)

        if _store_profile(store, profile, name):
            log.append("Added new entry.")
        else:
            log.append("Updated existing entry (same Wikipedia article).")

        log.append("Done!")
        return jsonify({
            "success": True,
            "profile": _safe_profile_summary(profile),
            "log":     log,
            "total":   len(store["profiles"]),
        })

    except Exception as exc:
        log.append(f"Error: {exc}")
        return jsonify({"error": str(exc), "log": log}), 500


# ──────────────────────────────────────────────────────────────────────────
# MAKER — Wikipedia articles and list pages
# ──────────────────────────────────────────────────────────────────────────
def _classify_wikipedia(profile):
    """
    Categorise a scraped article from its own infobox and categories.

    The infobox template is the strongest signal a Wikipedia article gives —
    {{Infobox musical artist}} is a musician whatever the prose says — and the
    occupation row settles most of the rest.
    """
    occupations = []
    for fact in profile.get("facts", []):
        if fact["label"].lower() in ("occupation", "occupations", "profession"):
            occupations += [o.strip() for o in re.split(r"[,;]", fact["value"])]
    hint = f"{profile.get('infobox_type', '')} {profile.get('description', '')}"
    return classify_entity(profile.get("name", ""), hint,
                           profile.get("wikipedia_categories", []), occupations)


def _enrich_wikipedia(profile, log):
    """
    Add Wikidata facts and live social stats to a single scraped article.

    Only for one-at-a-time adds: social stats mean a live page fetch each, and
    a hundred of them would turn a bulk import into a several-minute job.
    """
    try:
        extra = scrape_entity(profile["wikipedia_title"])
    except Exception:
        return
    if not extra:
        return

    wd = extra.get("wikidata", {})
    profile["wikidata_qid"] = extra.get("wikidata_qid")
    for field in ("date_of_birth", "place_of_birth", "gender"):
        if wd.get(field) and field not in profile:
            profile[field] = wd[field]

    handles = find_social_handles(extra.get("external_links", []))
    for platform, handle in (wd.get("wikidata_social") or {}).items():
        handles.setdefault(platform, {
            "handle": handle,
            "url": SOCIAL_URL_TPL.get(platform, "").format(handle)})
    if not handles:
        log.append("No social handles found.")
        return

    log.append(f"Found handles on: {', '.join(handles)}")
    try:
        profile["social"] = gather_social_stats(handles)
    except Exception:
        profile["social"] = {p: {"handle": h["handle"]}
                             for p, h in handles.items()}


@app.route("/api/maker/wikipedia/add", methods=["POST"])
def api_wikipedia_add():
    """
    Handle a pasted Wikipedia link.

    An article becomes one entry. A list article — "List of hip-hop musicians"
    and its several thousand names — becomes a picker instead, because the
    list itself is a menu and never a lorebook entry.
    """
    body = request.json or {}
    url = (body.get("url") or "").strip()
    notes = (body.get("notes") or "").strip()
    log = []

    if not url:
        return jsonify({"error": "Paste a Wikipedia link first."}), 400

    try:
        ref = wp.parse_url(url)
        log.append(f"Fetching “{ref['title']}” from {ref['host']}…")
        records = wp.fetch_pages(ref["api"], [ref["title"]])
        record = records.get(ref["title"])
        if not record or record.get("missing"):
            return jsonify({"error": f"“{ref['title']}” does not exist "
                                     f"on {ref['host']}.", "log": log}), 404

        raw = record.get("wikitext") or ""
        if wp.is_list_page(record["title"], raw):
            browse = wp.browse_list(ref, record["title"], raw)
            if not browse:
                return jsonify({"error": "That list page has no article links "
                                         "we could read.", "log": log}), 404
            meta = browse["list"]
            log.append(f"{meta['title']}: {meta['items']} linked articles "
                       f"across {meta['groups']} sections.")
            log.append("Pick the ones you want — each is scraped from its "
                       "own article.")
            return jsonify({"success": True, "mode": "browse",
                            "browse": browse, "log": log})

        profile = wp.build_profile(ref, record, _budget_for(body), notes)
        # A category the user picked in the dropdown beats anything inferred.
        profile["category"] = ((body.get("category") or "").strip()
                              or _classify_wikipedia(profile))
        log.append(f"{profile['infobox_type'] or 'no infobox'} "
                   f"· {profile['category']}")
        log.append(f"Kept {len(profile['facts'])} info fields and "
                   f"{len(profile['sections'])} sections.")
        log.append("Skipped discography, filmography, awards, references and "
                   "external links.")

        if body.get("social", True):
            _enrich_wikipedia(profile, log)

        store = _store()
        added = _store_profile(store, profile)
        log.append("Added new entry." if added else "Updated existing entry.")
        return jsonify({"success": True, "mode": "entry",
                        "profile": _safe_profile_summary(profile),
                        "log": log, "total": len(store["profiles"])})

    except WikipediaError as exc:
        log.append(f"Error: {exc}")
        return jsonify({"error": str(exc), "log": log}), 400
    except Exception as exc:
        log.append(f"Error: {exc}")
        return jsonify({"error": str(exc), "log": log}), 500


@app.route("/api/maker/wikipedia/bulk", methods=["POST"])
def api_wikipedia_bulk():
    """Import a batch of articles picked from a list page."""
    body = request.json or {}
    api = (body.get("api") or "").strip()
    site = (body.get("site") or "").strip() or "https://en.wikipedia.org"
    titles = body.get("titles") or []
    group = (body.get("group") or "").strip()

    if not api or not titles:
        return jsonify({"error": "Nothing selected to import."}), 400
    if not wp.is_wikipedia_api(api):
        return jsonify({"error": "That API endpoint is not Wikipedia."}), 400

    log = [f"Importing {len(titles)} articles…"]
    try:
        profiles, skipped = wp.scrape_titles(api, site, titles, _budget_for(body))
    except WikipediaError as exc:
        log.append(f"Error: {exc}")
        return jsonify({"error": str(exc), "log": log}), 400

    store = _store()

    added = updated = 0
    for profile in profiles:
        profile["category"] = _classify_wikipedia(profile)
        if group:
            profile["group"] = group
        if _store_profile(store, profile):
            added += 1
        else:
            updated += 1

    log.append(f"Added {added} new entries"
               + (f", updated {updated}" if updated else "") + ".")
    for title, reason in skipped[:10]:
        log.append(f"Skipped {title} — {reason}")
    if len(skipped) > 10:
        log.append(f"…and {len(skipped) - 10} more skipped.")

    return jsonify({"success": True, "added": added, "updated": updated,
                    "skipped": len(skipped), "log": log,
                    "total": len(store["profiles"])})


@app.route("/api/maker/wikipedia/search", methods=["POST"])
def api_wikipedia_fulltext():
    """Full-text Wikipedia search — powers the box inside the list picker."""
    body = request.json or {}
    api = (body.get("api") or "").strip()
    query = (body.get("query") or "").strip()
    if not api or not query:
        return jsonify({"results": []})
    if not wp.is_wikipedia_api(api):
        return jsonify({"error": "That API endpoint is not Wikipedia."}), 400
    try:
        return jsonify({"results": wp.search(api, query)})
    except WikipediaError as exc:
        return jsonify({"error": str(exc)}), 400


# ──────────────────────────────────────────────────────────────────────────
# MAKER — Fandom wikis
# ──────────────────────────────────────────────────────────────────────────
@app.route("/api/maker/fandom/add", methods=["POST"])
def api_fandom_add():
    """
    Handle a pasted Fandom link.

    An article link is scraped straight into an entry. A wiki root (or a
    category link) instead returns that wiki's lore categories, so the user
    can pick which characters, locations and arcs to import.
    """
    body = request.json or {}
    url = (body.get("url") or "").strip()
    notes = (body.get("notes") or "").strip()
    log = []

    if not url:
        return jsonify({"error": "Paste a Fandom wiki link first."}), 400

    try:
        ref, info = fandom.resolve(url)

        if ref["kind"] != "article":
            # Which story first, and only then its contents. Indexing a whole
            # wiki's categories takes ten seconds and is wasted work when the
            # answer turns out to be "the Vigilantes spin-off" — that scope is
            # derived from the spin-off's own pages instead. So the categories
            # are not touched until somebody asks for the whole wiki.
            if not body.get("whole_wiki"):
                log.append(f"Reading what {info['name']} covers…")
                works = fandom.discover_works(ref["api"], info)
                if works:
                    count = sum(len(k["works"]) for k in works)
                    log.append(f"{info['name']} covers {count} separate stories "
                               f"across {len(works)} kinds.")
                    return jsonify({
                        "success": True, "mode": "works",
                        "browse": {
                            "wiki_name": info["name"], "site": ref["site"],
                            "api": ref["api"], "main_page": info.get("main_page"),
                            "groups": [], "works": works,
                        },
                        "log": log,
                    })
                log.append("It documents one story — opening all of it.")

            log.append(f"Reading wiki index at {ref['site']}…")
            browse = fandom.browse_wiki(url, ref=ref, info=info, works=False)
            total = sum(len(g["pages"]) for g in browse["groups"])
            if not browse["groups"]:
                return jsonify({
                    "error": "That wiki has no browsable article categories. "
                             "Link to a specific article instead.",
                    "log": log,
                }), 404
            log.append(f"{browse['wiki_name']}: {len(browse['groups'])} "
                       f"categories, {total} pages available.")
            return jsonify({"success": True, "mode": "browse",
                            "browse": browse, "log": log})

        log.append(f"Fetching “{ref['title']}” from {ref['host']}…")
        profile, browse = fandom.scrape_article(
            url, notes, _budget_for(body), ref, info, log,
            subpages=_subpages_for(body), arcs=_arcs_for(body))
        log.append(f"{profile['wiki_name']} · {profile['infobox_type'] or 'no infobox'}")
        log.append(f"Kept {len(profile['facts'])} info fields and "
                   f"{len(profile['sections'])} sections.")
        if profile.get("subpage_sections"):
            log.append(f"Pulled {profile['subpage_sections']} more from its "
                       f"Synopsis and History tabs.")
        log.append("Skipped images, references, trivia and production credits.")

        store = _store()
        # Opening a work to see what is in it is not the same as wanting the
        # work itself as an entry. The stepped picker asks for its cast; it
        # does not ask for a page about the film.
        scope_only = bool(body.get("scope_only"))
        if scope_only:
            log.append("Reading what appears in it — not adding it as an entry.")
        else:
            added = _store_profile(store, profile)
            log.append("Added new entry." if added else "Updated existing entry.")

        result = {
            "success": True,
            "mode":    "scope" if scope_only else "entry",
            "profile": _safe_profile_summary(profile),
            "log":     log,
            "total":   len(store["profiles"]),
        }

        # A film, episode or comic indexes everything that appears in it, and a
        # sub-series' scope has just been derived from its own chapters. Either
        # way the page has turned out to describe a story rather than a thing,
        # and that story's contents are the natural scope for a lorebook.
        if browse and browse["groups"]:
            major = sum(len(g["pages"]) - len(g.get("minor") or [])
                        for g in browse["groups"])
            scope_info = browse.get("scope")
            if scope_info:
                log.append(
                    f"Scoped to “{scope_info['series']}” only — {major} entries "
                    f"across {len(browse['groups'])} groups, from "
                    f"{scope_info['instalments']} instalments.")
            else:
                log.append(f"“{profile['name']}” indexes {major} things that appear "
                           f"in it across {len(browse['groups'])} groups.")
            result["mode"] = "scope+browse" if scope_only else "entry+browse"
            result["browse"] = browse
        return jsonify(result)

    except FandomError as exc:
        log.append(f"Error: {exc}")
        return jsonify({"error": str(exc), "log": log}), 400
    except Exception as exc:
        log.append(f"Error: {exc}")
        return jsonify({"error": str(exc), "log": log}), 500


@app.route("/api/maker/fandom/bulk", methods=["POST"])
def api_fandom_bulk():
    """Import a batch of pages picked from the browse view."""
    body = request.json or {}
    api = (body.get("api") or "").strip()
    site = (body.get("site") or "").strip()
    titles = body.get("titles") or []
    wiki_name = (body.get("wiki_name") or "").strip() or None
    group = (body.get("group") or "").strip()

    if not api or not titles:
        return jsonify({"error": "Nothing selected to import."}), 400
    if not fandom.is_fandom_api(api):
        return jsonify({"error": "That API endpoint is not a Fandom wiki."}), 400

    log = [f"Importing {len(titles)} pages…"]
    try:
        profiles, skipped = fandom.scrape_titles(
            api, site, titles, wiki_name, _budget_for(body),
            subpages=_subpages_for(body), arcs=_arcs_for(body),
            tiers=body.get("tiers") or None,
            relations=bool(body.get("relations")))
    except FandomError as exc:
        log.append(f"Error: {exc}")
        return jsonify({"error": str(exc), "log": log}), 400

    store = _store()
    # A primer is written once for the story a batch came from, and only if
    # the book has not already got one. It has to be settled before the
    # storing loop, or it is built and then thrown away.
    if body.get("primer") and group and not any(
            p.get("always_on") for p in store["profiles"]):
        roster = [p.get("name") for p in profiles[:14]]
        try:
            primer = fandom.build_primer(
                {"api": api, "site": site}, {"name": wiki_name or ""},
                group, roster, log)
        except FandomError:
            primer = None
        if primer:
            profiles.insert(0, primer)

    added = updated = 0
    for profile in profiles:
        # Everything pulled from one film/episode shares a group, so the
        # Editor can filter or bulk-edit that story's entries together.
        if group:
            profile["group"] = group
        if _store_profile(store, profile):
            added += 1
        else:
            updated += 1

    relation_entries = sum(1 for p in profiles if p.get("relates_to"))
    with_story = sum(1 for p in profiles
                     if p.get("subpage_sections") and not p.get("relates_to"))
    log.append(f"Added {added} new entries" +
               (f", updated {updated}" if updated else "") + ".")
    if with_story:
        log.append(f"{with_story} of them had a Synopsis or History tab, "
                   f"pulled in too.")
    if relation_entries:
        log.append(f"{relation_entries} Relationships tabs became entries of "
                   f"their own, keyed on both people.")
    for title, reason in skipped[:10]:
        log.append(f"Skipped {title} — {reason}")
    if len(skipped) > 10:
        log.append(f"…and {len(skipped) - 10} more skipped.")

    return jsonify({
        "success": True,
        "added":   added,
        "updated": updated,
        "skipped": len(skipped),
        "log":     log,
        "total":   len(store["profiles"]),
    })


@app.route("/api/maker/fandom/arcs", methods=["POST"])
def api_fandom_arcs():
    """
    The story arcs the selected pages' synopses are divided into.

    A wiki tells the same story from every character's point of view, so the
    arc headings repeat across them — which is what lets one list of chips
    filter a whole import instead of needing a choice per entry.
    """
    body = request.json or {}
    api = (body.get("api") or "").strip()
    titles = (body.get("titles") or [])[:fandom.MAX_BULK_PAGES]
    if not api or not titles:
        return jsonify({"arcs": []})
    if not fandom.is_fandom_api(api):
        return jsonify({"error": "That API endpoint is not a Fandom wiki."}), 400

    try:
        records = fandom.fetch_with_subpages(api, titles)
    except FandomError as exc:
        return jsonify({"error": str(exc)}), 400

    arcs = fandom.collect_arcs(records, titles)
    return jsonify({"arcs": arcs, "pages": len(titles)})


@app.route("/api/maker/fandom/find-work", methods=["POST"])
def api_fandom_find_work():
    """
    Search a wiki for one of its own stories, for step 1.

    The Disney wiki documents 2,607 films; no grid shows that and the forty it
    can show begin at "10 Things I Hate About You". On a wiki that size you
    ask for Frozen rather than scrolling to it.
    """
    body = request.json or {}
    api = (body.get("api") or "").strip()
    query = (body.get("query") or "").strip()
    if not api or not query:
        return jsonify({"works": []})
    if not fandom.is_fandom_api(api):
        return jsonify({"error": "That API endpoint is not a Fandom wiki."}), 400
    try:
        return jsonify({"works": fandom.search_works(api, query)})
    except FandomError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/maker/fandom/images", methods=["POST"])
def api_fandom_images():
    """
    Lead images for a batch of pages, for the picker's tiles.

    Asked for a category at a time rather than for the whole wiki: three
    hundred names is seven requests nobody needs until they scroll that far.
    """
    body = request.json or {}
    api = (body.get("api") or "").strip()
    titles = (body.get("titles") or [])[:200]
    if not api or not titles:
        return jsonify({"images": {}})
    if not fandom.is_fandom_api(api):
        return jsonify({"error": "That API endpoint is not a Fandom wiki."}), 400
    try:
        return jsonify({"images": fandom.fetch_images(api, titles)})
    except FandomError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/img")
def api_image():
    """
    Serve a wiki thumbnail through the app.

    Linking Fandom's CDN straight from the page fails wherever a page is not
    allowed to load third-party images, and has the user's browser talking to
    a CDN once per tile. Passing them through costs one cached request each
    and keeps browsing a wiki between this app and the wiki.
    """
    url = request.args.get("u", "")
    if not fandom.is_image_url(url):
        return jsonify({"error": "Not a wiki image address."}), 400
    try:
        body, kind = fandom.fetch_image_bytes(url)
    except FandomError as exc:
        return jsonify({"error": str(exc)}), 502
    response = make_response(body)
    response.headers["Content-Type"] = kind
    # The thumbnails are immutable — Fandom versions them in the path — so the
    # browser should never ask twice.
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response


@app.route("/api/maker/fandom/category", methods=["POST"])
def api_fandom_category():
    """
    Page list for one category, fetched when the user opens a sub-filter.

    Browsing only counts a wiki's subcategories up front; loading every one of
    them eagerly would mean dozens of requests for lists nobody opens.
    """
    body = request.json or {}
    api = (body.get("api") or "").strip()
    category = (body.get("category") or "").strip()
    if not api or not category:
        return jsonify({"error": "Missing wiki or category."}), 400
    if not fandom.is_fandom_api(api):
        return jsonify({"error": "That API endpoint is not a Fandom wiki."}), 400
    try:
        return jsonify({"category": category,
                        "pages": fandom.resolve_category(api, category)})
    except FandomError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/maker/fandom/search", methods=["POST"])
def api_fandom_search():
    """Search inside one wiki — powers the search box in the browse view."""
    body = request.json or {}
    api = (body.get("api") or "").strip()
    query = (body.get("query") or "").strip()
    if not api or not query:
        return jsonify({"results": []})
    if not fandom.is_fandom_api(api):
        return jsonify({"error": "That API endpoint is not a Fandom wiki."}), 400
    try:
        return jsonify({"results": fandom.search_wiki(api, query)})
    except FandomError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/maker/entries", methods=["GET"])
def api_maker_entries():
    """List all entry summaries in the current session, with a size estimate."""
    store = _store()
    profiles = store["profiles"]
    existing = store["existing"]

    # Rendering every entry is the only honest way to size a lorebook, and it
    # is cheap enough at these counts. SillyTavern budgets tokens per book, so
    # a 60-entry world import needs to say how big it has become.
    chars = 0
    for profile in profiles:
        try:
            chars += len(profile_to_entry(profile).get("content", ""))
        except Exception:
            pass
    for entry in existing:
        chars += len(entry.get("content") or "")

    # A scrape of a page already in the loaded book refreshes it instead of
    # adding a second copy, so the total is not simply the two counts summed.
    # The pair is still listed twice, so each side has to say so — otherwise
    # the same character appearing twice reads as a bug.
    scraped, refreshed = [], 0
    replaced = set()
    for profile in profiles:
        summary = _safe_profile_summary(profile)
        match = LB.find_match(existing, profile)
        if match is not None:
            refreshed += 1
            replaced.add(existing[match].get("_id"))
            summary["refreshes"] = LB.entry_name(existing[match])
        scraped.append(summary)

    loaded = []
    for entry in existing:
        summary = _existing_summary(entry)
        if entry.get("_id") in replaced:
            summary["will_refresh"] = True
        loaded.append(summary)

    return jsonify({
        "entries":       loaded + scraped,
        "count":         len(existing) + len(profiles) - refreshed,
        "scraped":       len(profiles),
        "loaded":        len(existing),
        "refreshed":     refreshed,
        "book_name":     store.get("book_name") or "",
        "total_chars":   chars,
        "approx_tokens": chars // 4,
    })


@app.route("/api/maker/health", methods=["POST"])
def api_health():
    """
    Whether the book as it stands would work once it is loaded.

    A lorebook fails quietly — entries that never fire, entries that all fire
    at once, a book bigger than the budget it has to fit in — and none of it
    shows until you are mid-roleplay. Rendering every entry to check is cheap
    at these counts.
    """
    body = request.json or {}
    try:
        context = int(body.get("context") or 8192)
    except (TypeError, ValueError):
        context = 8192

    store = _store()
    built = []
    for profile in store["profiles"]:
        try:
            built.append(profile_to_entry(profile))
        except Exception:
            pass
    built += list(store["existing"])
    return jsonify(HEALTH.inspect(built, context_tokens=context))


@app.route("/api/maker/entries/<entry_id>", methods=["DELETE"])
def api_delete_entry(entry_id):
    store = _store()
    before = len(store["profiles"]) + len(store["existing"])
    store["profiles"] = [p for p in store["profiles"] if p.get("_id") != entry_id]
    store["existing"] = [e for e in store["existing"] if e.get("_id") != entry_id]
    if len(store["profiles"]) + len(store["existing"]) < before:
        return jsonify({"success": True})
    return jsonify({"error": "Entry not found"}), 404


# ──────────────────────────────────────────────────────────────────────────
# MAKER — continuing an existing lorebook
# ──────────────────────────────────────────────────────────────────────────
def _adopt(store, book, entries, name):
    """Put a parsed lorebook into the session as the base to build on."""
    for index, entry in enumerate(entries):
        entry["_id"] = f"loaded_{index}_{int(time.time() * 1000)}"
    store["existing"] = entries
    store["book"] = book
    store["book_name"] = name
    return entries


@app.route("/api/maker/load", methods=["POST"])
def api_maker_load():
    """
    Load an existing lorebook into the Maker so new entries build onto it.

    Accepts an uploaded file, a path on this machine, or the JSON itself, since
    a local tool is used from all three directions.
    """
    store = _store()
    raw = name = None

    if "file" in request.files and request.files["file"].filename:
        upload = request.files["file"]
        name = upload.filename
        raw = upload.read()
    else:
        body = request.get_json(silent=True) or {}
        if body.get("data") is not None:
            raw = body["data"]
            name = (body.get("name") or "Pasted lorebook").strip()
        elif (body.get("path") or "").strip():
            path = _resolve_path(body["path"])
            if not path.is_file():
                return jsonify({"error": f"File not found: {path}"}), 404
            raw = path.read_bytes()
            name = path.name
        else:
            return jsonify({"error": "No lorebook file, path or data given."}), 400

    try:
        book, entries = LB.parse(raw)
    except LB.LorebookError as exc:
        return jsonify({"error": str(exc)}), 400
    except (UnicodeDecodeError, OSError) as exc:
        return jsonify({"error": f"Could not read that file: {exc}"}), 400

    _adopt(store, book, entries, book.get("name") or name or "Loaded lorebook")

    log = [f"Loaded “{store['book_name']}” — {len(entries)} entries.",
           "Every field is preserved; new scrapes are added alongside."]
    already = sum(1 for p in store["profiles"]
                  if LB.find_match(entries, p) is not None)
    if already:
        log.append(f"{already} of your scraped entries already exist in it — "
                   f"those will refresh the originals rather than duplicate them.")

    return jsonify({
        "success":   True,
        "book_name": store["book_name"],
        "loaded":    len(entries),
        "log":       log,
    })


@app.route("/api/maker/adopt-editor", methods=["POST"])
def api_adopt_editor():
    """Hand whatever the Editor currently holds to the Maker, to add entries to."""
    if not STATE["data"]:
        return jsonify({"error": "The Editor has no lorebook open."}), 400
    store = _store()
    book, entries = LB.parse(copy.deepcopy(STATE["data"]))
    _adopt(store, book, entries,
           book.get("name") or STATE.get("name") or "Loaded lorebook")
    return jsonify({"success": True, "redirect": "/maker",
                    "book_name": store["book_name"], "loaded": len(entries)})


@app.route("/api/maker/export", methods=["POST"])
def api_export():
    """Generate and stream the SillyTavern lorebook JSON file."""
    store = _store()
    if not store["profiles"] and not store["existing"]:
        return jsonify({"error": "No entries to export"}), 400

    body = request.json or {}
    book_name = body.get("name", "My Lorebook").strip() or "My Lorebook"
    book_desc = body.get("description", "").strip()

    lorebook, _ = LB.build(store["existing"], store["profiles"],
                           profile_to_entry, book_name, book_desc,
                           store.get("book"))
    json_str = json.dumps(lorebook, indent=2, ensure_ascii=False)

    buf = io.BytesIO(json_str.encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/json",
        as_attachment=True,
        download_name=f"{_safe_filename(book_name)}.json",
    )


@app.route("/api/maker/send-to-editor", methods=["POST"])
def api_send_to_editor():
    """Build the lorebook from the session's profiles and open it in the Editor."""
    store = _store()
    if not store["profiles"] and not store["existing"]:
        return jsonify({"error": "No entries to send"}), 400

    body = request.json or {}
    book_name = body.get("name", "My Lorebook").strip() or "My Lorebook"
    book_desc = body.get("description", "").strip()

    lorebook, _ = LB.build(store["existing"], store["profiles"],
                           profile_to_entry, book_name, book_desc,
                           store.get("book"))
    UNDO.clear()
    STATE.update(data=lorebook, path=None,
                 name=f"{_safe_filename(book_name)}.json", dirty=True)
    return jsonify({"ok": True, "redirect": "/editor",
                    "count": len(lorebook["entries"])})


@app.route("/api/maker/clear", methods=["POST"])
def api_clear():
    """
    Empty the session. `scope` lets the loaded lorebook be kept while the new
    scrapes are thrown away — the usual way a batch import goes wrong is worth
    being able to undo without reloading the book.
    """
    store = _store()
    scope = ((request.get_json(silent=True) or {}).get("scope") or "all").lower()
    if scope in ("all", "scraped"):
        store["profiles"] = []
    if scope in ("all", "loaded"):
        store["existing"] = []
        store["book"] = None
        store["book_name"] = ""
    return jsonify({"success": True})


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    print(f" * Lorebook Studio: http://{HOST}:{PORT}")
    # Threaded: the picker asks for a screenful of thumbnails at once, and a
    # single-threaded server serves them one after another while the page sits
    # there showing initials. Each one is a short outbound fetch, so they
    # should overlap.
    app.run(debug=debug, host=HOST, port=PORT, threaded=True)
