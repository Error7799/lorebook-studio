"""
Reading an existing lorebook back in, and merging new entries into it.

The Maker builds books from scratch; this is what lets it *continue* one. Load
the lorebook you shipped last month, scrape a new story into it, and export the
two together — with the original entries byte-for-byte as they were, including
every field SillyTavern or a hand-edit put there that this app knows nothing
about.

Two rules follow from that, and both matter more than they look:

  * **Unknown fields survive.** An entry is carried through as its own dict, not
    rebuilt from a schema. Anything a future SillyTavern version adds is
    preserved by doing nothing to it.
  * **Re-scraping refreshes rather than duplicates.** Scraping a page that is
    already in the book replaces its *text* and keeps its *tuning* — the
    strategy, order, depth and group you set by hand are the part the wiki
    cannot tell you, so they are never overwritten.
"""

import json
import re
from datetime import datetime

# Entry fields that describe the page, and so are re-derived from a fresh
# scrape. Everything else on an existing entry is the user's own tuning.
REFRESHED_FIELDS = ("key", "keysecondary", "content", "comment")

# "CHARACTER: Yuka Okkotsu" → "Yuka Okkotsu". The Maker writes this prefix, so
# it has to come off before a comment can be compared with a page name.
_COMMENT_PREFIX = re.compile(r"^[A-Z][A-Z &/-]{2,24}:\s*")


class LorebookError(Exception):
    """Raised for files that are not a lorebook we can read."""


# ══════════════════════════════════════════════════════════════════════════
# Reading
# ══════════════════════════════════════════════════════════════════════════

def parse(raw):
    """
    Read lorebook JSON into (book, entries).

    `book` keeps every top-level field except the entries themselves, so the
    original scan depth, token budget and extensions survive a round trip.
    `entries` is a plain list — SillyTavern keys them by uid in an object, but
    some exports use an array, and nothing downstream should have to care.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8-sig")
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LorebookError(f"That file is not valid JSON: {exc}") from exc
    else:
        data = raw

    if not isinstance(data, dict) or "entries" not in data:
        raise LorebookError(
            "That is not a SillyTavern lorebook — it has no “entries”.")

    raw_entries = data.get("entries")
    if isinstance(raw_entries, dict):
        # Keys are uids as strings; numeric order is the book's own order.
        def sort_key(item):
            try:
                return (0, int(item[0]))
            except (TypeError, ValueError):
                return (1, str(item[0]))
        entries = [e for _, e in sorted(raw_entries.items(), key=sort_key)
                   if isinstance(e, dict)]
    elif isinstance(raw_entries, list):
        entries = [e for e in raw_entries if isinstance(e, dict)]
    else:
        raise LorebookError("The “entries” field is neither a list nor an object.")

    if not entries:
        raise LorebookError("That lorebook has no entries in it.")

    book = {k: v for k, v in data.items() if k != "entries"}
    return book, entries


def entry_name(entry):
    """The best human name for an entry — its comment, else its first key."""
    comment = (entry.get("comment") or "").strip()
    if comment:
        return _COMMENT_PREFIX.sub("", comment).strip() or comment
    keys = entry.get("key") or []
    if isinstance(keys, str):
        keys = [keys]
    return (keys[0] if keys else "").strip() or "Untitled entry"


def entry_category(entry):
    """
    The entry's category, from the Maker's own metadata or its comment prefix.

    Books written elsewhere have neither, and "Imported" is an honest answer —
    guessing a category from an entry's prose would mislabel far more than it
    would place.
    """
    meta = ((entry.get("extensions") or {}).get("lorebook_meta") or {})
    if meta.get("category"):
        return meta["category"]
    m = _COMMENT_PREFIX.match((entry.get("comment") or "").strip())
    if m:
        return m.group(0).rstrip(": ").strip().title()
    return "Imported"


# ══════════════════════════════════════════════════════════════════════════
# Identity — deciding when a scrape and an existing entry are the same thing
# ══════════════════════════════════════════════════════════════════════════

def _identities(page_url="", wiki_title="", name=""):
    """Every handle one subject can be recognised by, strongest first."""
    out = []
    if page_url:
        out.append(("url", page_url.strip().lower()))
    if wiki_title:
        out.append(("wiki", wiki_title.strip().lower()))
    if name:
        out.append(("name", name.strip().lower()))
    return out


def entry_identities(entry):
    meta = ((entry.get("extensions") or {}).get("lorebook_meta") or {})
    return _identities(meta.get("page_url") or "",
                       meta.get("wikipedia_title") or "",
                       entry_name(entry))


def profile_identities(profile):
    return _identities(profile.get("page_url") or "",
                       profile.get("wikipedia_title") or "",
                       profile.get("name") or "")


def find_match(entries, profile):
    """
    Index of the existing entry describing the same subject, or None.

    A URL match is proof; a name match is a strong guess and the only thing
    available for a book written by hand or by another tool.
    """
    lookup = {}
    for index, entry in enumerate(entries):
        for ident in entry_identities(entry):
            lookup.setdefault(ident, index)
    for ident in profile_identities(profile):
        if ident in lookup:
            return lookup[ident]
    return None


# ══════════════════════════════════════════════════════════════════════════
# Merging
# ══════════════════════════════════════════════════════════════════════════

def refresh_entry(existing, fresh):
    """
    Update an entry from a new scrape without losing how it was set up.

    The wiki is authoritative about what the subject *is*; it knows nothing
    about when the entry should fire. So text and trigger words come from the
    scrape and everything else — strategy, order, depth, probability, group,
    whether it is disabled — stays exactly as the user left it.
    """
    merged = dict(existing)
    for field in REFRESHED_FIELDS:
        if field in fresh:
            merged[field] = fresh[field]

    extensions = dict(existing.get("extensions") or {})
    fresh_meta = (fresh.get("extensions") or {}).get("lorebook_meta")
    if fresh_meta:
        extensions["lorebook_meta"] = fresh_meta
    if extensions:
        merged["extensions"] = extensions
    return merged


def build(existing, profiles, to_entry, name="Lorebook", description="",
          book=None):
    """
    Merge loaded entries and freshly scraped profiles into one lorebook.

    Returns (lorebook, stats) where stats counts what happened, so the UI can
    say "12 added, 3 refreshed" rather than leaving the user to diff two files.

    `to_entry` renders a profile; it is passed in rather than imported so this
    module stays independent of how entries are built.
    """
    merged = [dict(e) for e in (existing or [])]
    added = refreshed = 0

    for profile in profiles or []:
        fresh = to_entry(profile)
        index = find_match(merged, profile)
        if index is None:
            merged.append(fresh)
            added += 1
        else:
            merged[index] = refresh_entry(merged[index], fresh)
            refreshed += 1

    # uids have to be unique within a book and are what SillyTavern keys on;
    # loaded entries may collide with each other after edits elsewhere.
    entries = {}
    for uid, entry in enumerate(merged):
        entry["uid"] = uid
        if not isinstance(entry.get("displayIndex"), int):
            entry["displayIndex"] = uid
        entries[str(uid)] = entry

    out = dict(book or {})
    out.update({
        "name":        name,
        "description": description or out.get("description")
                       or f"Lorebook ({len(entries)} entries).",
    })
    out.setdefault("scan_depth", 100)
    out.setdefault("token_budget", 500)
    out.setdefault("recursive_scanning", False)

    extensions = dict(out.get("extensions") or {})
    extensions["generator"] = "Lorebook Studio"
    extensions.setdefault("created_at", datetime.now().isoformat())
    extensions["updated_at"] = datetime.now().isoformat()
    out["extensions"] = extensions

    out["entries"] = entries
    return out, {"added": added, "refreshed": refreshed,
                 "kept": len(existing or []), "total": len(entries)}
