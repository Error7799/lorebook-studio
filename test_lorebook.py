"""
Offline checks for reading an existing lorebook back in and merging into it.

No network — everything here is fixed input:

    python test_lorebook.py

The point of most of these is *preservation*. A merge that loses a field the
user set by hand, or that quietly duplicates an entry, is worse than one that
refuses to run, so those are the cases worth pinning down.
"""

import json
import sys

from core import lorebook as LB

FAILED = []


def check(label, got, want):
    if got != want:
        FAILED.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")


def fails(label, fn):
    try:
        fn()
    except LB.LorebookError:
        return
    FAILED.append(f"{label}\n     expected a LorebookError, got none")


def entry(**kw):
    base = {"uid": 0, "key": ["x"], "content": "text", "comment": "Thing"}
    base.update(kw)
    return base


# ── Reading ───────────────────────────────────────────────────────────────
book, entries = LB.parse(json.dumps({
    "name": "My Book", "scan_depth": 42, "custom_top_level": {"a": 1},
    "entries": {"1": entry(comment="Second"), "0": entry(comment="First")},
}))
check("top-level fields survive the read",
      (book["name"], book["scan_depth"], book["custom_top_level"]),
      ("My Book", 42, {"a": 1}))
check("entries come back in uid order, not dict order",
      [e["comment"] for e in entries], ["First", "Second"])
check("entries are not left in the book dict", "entries" in book, False)

_, listed = LB.parse({"entries": [entry(comment="A"), entry(comment="B")]})
check("an array of entries reads too",
      [e["comment"] for e in listed], ["A", "B"])

fails("plain text is rejected", lambda: LB.parse("not json"))
fails("a JSON file that is not a lorebook is rejected",
      lambda: LB.parse('{"foo": 1}'))
fails("an empty lorebook is rejected", lambda: LB.parse('{"entries": {}}'))


# ── Naming ────────────────────────────────────────────────────────────────
check("the Maker's category prefix comes off the name",
      LB.entry_name(entry(comment="CHARACTER: Yuka Okkotsu")), "Yuka Okkotsu")
check("a plain comment is the name",
      LB.entry_name(entry(comment="Some hand-written note")),
      "Some hand-written note")
check("with no comment, the first key is the name",
      LB.entry_name(entry(comment="", key=["Levi", "Ackerman"])), "Levi")
check("a colon inside prose is not a category prefix",
      LB.entry_name(entry(comment="Note: he lies about this")),
      "Note: he lies about this")
check("the category comes from the prefix when there is no metadata",
      LB.entry_category(entry(comment="LOCATION: Simuria")), "Location")
check("a book from elsewhere is labelled honestly",
      LB.entry_category(entry(comment="Levi")), "Imported")


# ── Identity ──────────────────────────────────────────────────────────────
existing = [
    entry(comment="CHARACTER: Yuta Okkotsu", extensions={"lorebook_meta": {
        "page_url": "https://jujutsu-kaisen.fandom.com/wiki/Yuta_Okkotsu"}}),
    entry(comment="CHARACTER: Someone Else"),
]
check("a page URL identifies an entry",
      LB.find_match(existing, {
          "name": "Renamed Since",
          "page_url": "https://jujutsu-kaisen.fandom.com/wiki/Yuta_Okkotsu"}), 0)
check("a name identifies an entry when there is no URL",
      LB.find_match(existing, {"name": "Someone Else"}), 1)
check("an unrelated page matches nothing",
      LB.find_match(existing, {"name": "Nobody", "page_url": "http://x/y"}), None)


# ── Merging ───────────────────────────────────────────────────────────────
def to_entry(profile):
    return {"uid": 0, "key": [profile["name"]], "content": "FRESH TEXT",
            "comment": f"CHARACTER: {profile['name']}",
            "order": 100, "depth": 4,
            "extensions": {"lorebook_meta": {"page_url": profile.get("page_url")}}}


tuned = entry(
    comment="CHARACTER: Yuta Okkotsu", content="OLD TEXT", key=["old"],
    order=77, depth=9, constant=True, group="MyGroup", disable=True,
    hand_written_field="keep me",
    extensions={"lorebook_meta": {"page_url": "http://w/Yuta"},
                "other_tool": {"note": "keep me too"}})

merged, stats = LB.build(
    [tuned], [{"name": "Yuta Okkotsu", "page_url": "http://w/Yuta"},
              {"name": "Yuka Okkotsu", "page_url": "http://w/Yuka"}],
    to_entry, name="Merged")

check("a re-scrape refreshes instead of duplicating",
      (stats["added"], stats["refreshed"], stats["total"]), (1, 1, 2))

got = merged["entries"]["0"]
check("the refreshed entry takes the new text", got["content"], "FRESH TEXT")
check("…and the new trigger words", got["key"], ["Yuta Okkotsu"])
check("…but keeps every setting the user chose",
      {k: got.get(k) for k in ("order", "depth", "constant", "group", "disable")},
      {"order": 77, "depth": 9, "constant": True, "group": "MyGroup",
       "disable": True})
check("…and unknown fields from other tools",
      got.get("hand_written_field"), "keep me")
check("…and unknown extensions",
      got.get("extensions", {}).get("other_tool"), {"note": "keep me too"})
check("uids are renumbered so none collide",
      sorted(merged["entries"]), ["0", "1"])
check("each entry's uid matches its key",
      [merged["entries"][k]["uid"] for k in ("0", "1")], [0, 1])

# Loading a book and exporting it untouched must change nothing but metadata.
untouched, stats = LB.build([tuned], [], to_entry, name="Same")
check("with nothing scraped, nothing is added or refreshed",
      (stats["added"], stats["refreshed"]), (0, 0))
check("an untouched entry keeps its original text",
      untouched["entries"]["0"]["content"], "OLD TEXT")

kept = LB.build([], [{"name": "Only Scraped", "page_url": "http://w/a"}],
                to_entry, name="New")[0]
check("a book can be built with no existing entries at all",
      len(kept["entries"]), 1)


# ── Reporting ─────────────────────────────────────────────────────────────
if FAILED:
    print(f"\n{len(FAILED)} check(s) failed:\n")
    for line in FAILED:
        print(f"  ✕ {line}\n")
    sys.exit(1)
print("All lorebook merge checks passed.")
