"""
Offline checks for the fit report — the thing that says whether a finished
lorebook will actually work once SillyTavern loads it.

No network:

    python test_health.py

The failures it looks for are the ones that never announce themselves: a book
bigger than the budget it has to fit in loses entries silently, a key shared by
two entries pays for both, and an entry with no keys can never fire at all.
"""

import sys

from core import health as H
from core.formatter import profile_to_entry

FAILED = []


def check(label, got, want):
    if got != want:
        FAILED.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")


def kinds(report):
    return sorted(i["kind"] for i in report["issues"])


def one(report, kind):
    return next((i for i in report["issues"] if i["kind"] == kind), None)


def entry(name, keys=(), text="word " * 40, **extra):
    row = {"comment": f"CHARACTER: {name}", "key": list(keys),
           "content": text, "constant": False}
    row.update(extra)
    return row


# ── The budget ────────────────────────────────────────────────────────────
# SillyTavern activates entries until the allowance runs out and then stops,
# so an overrun is not an error the user ever sees — it is entries quietly
# going missing.
small = [entry("Elsa", ["Elsa"], "x" * 400)]
check("a book inside its allowance says nothing about the budget",
      one(H.inspect(small, 8192), "budget"), None)

huge = [entry("Elsa", ["Elsa"], "x" * 200000)]
report = H.inspect(huge, 8192)
check("an overrun is reported", one(report, "budget") is not None, True)
check("the allowance is a fifth of the context",
      report["allowance"], int(8192 * H.BUDGET_SHARE))
check("a bigger context raises the allowance",
      H.inspect(huge, 32768)["allowance"] > report["allowance"], True)
check("an explicit budget overrides the context",
      H.inspect(huge, 8192, budget_tokens=999)["allowance"], 999)


# ── Entries that cannot work ──────────────────────────────────────────────
check("an entry with no keys and no always-on flag can never fire",
      one(H.inspect([entry("Arendelle", [])], 8192), "silent")["entries"],
      ["Arendelle"])
check("…unless it is always on",
      one(H.inspect([entry("Arendelle", [], constant=True)], 8192), "silent"),
      None)
check("an empty entry is reported",
      one(H.inspect([entry("Olaf", ["Olaf"], "")], 8192), "empty")["entries"],
      ["Olaf"])


# ── Keys that fire the wrong things ───────────────────────────────────────
shared = [entry("Elsa", ["Elsa", "Ice"]), entry("Anna", ["Anna", "Ice"])]
collision = one(H.inspect(shared, 8192), "collision")
check("a key used by two entries is reported", collision is not None, True)
check("…and it names both of them",
      "Elsa" in collision["entries"][0] and "Anna" in collision["entries"][0],
      True)
check("a key used once is not",
      one(H.inspect([entry("Elsa", ["Elsa"]), entry("Anna", ["Anna"])], 8192),
          "collision"), None)

check("an everyday word is too broad to be a key",
      one(H.inspect([entry("Boulder", ["Rock"])], 8192), "broad-key")
      is not None, True)
check("a short key matches inside other words",
      one(H.inspect([entry("Rintarou", ["Rin"])], 8192), "broad-key")
      is not None, True)
check("…unless whole-word matching is on",
      one(H.inspect([entry("Rintarou", ["Rin"], matchWholeWords=True)], 8192),
          "broad-key"), None)
check("a real name is left alone",
      one(H.inspect([entry("Kristoff", ["Kristoff", "Bjorgman"])], 8192),
          "broad-key"), None)


# ── Shape of the book ─────────────────────────────────────────────────────
check("a book with nothing always on is worth a note",
      one(H.inspect([entry("Elsa", ["Elsa"])], 8192), "no-constant")
      is not None, True)
check("…and a primer settles it",
      one(H.inspect([entry("Elsa", ["Elsa"]),
                     entry("Arendelle", ["Arendelle"], constant=True)], 8192),
          "no-constant"), None)

hog = [entry("Elsa", ["Elsa"], "x" * 6000), entry("Anna", ["Anna"], "x" * 40)]
check("an entry taking a quarter of the budget is worth knowing about",
      one(H.inspect(hog, 8192), "heavy")["entries"][0].startswith("Elsa"), True)

check("errors are listed before warnings and notes",
      [i["level"] for i in H.inspect(
          [entry("Elsa", ["Elsa", "Rock"], "x" * 90000),
           entry("Anna", ["Rock"]),
           entry("Nobody", [])], 8192)["issues"]][0],
      "error")


# ── What the builder emits should pass its own checks ─────────────────────
# The fixes belong at the source: a fragile key gets whole-word matching when
# the entry is built, rather than being reported for ever afterwards.
fragile = profile_to_entry({
    "source": "fandom", "name": "Rin", "category": "Character",
    "aliases": [], "facts": [], "keys": ["Rin", "Rintarou Okabe"],
    "sections": [{"title": "About", "text": "word " * 40}],
})
check("a fragile key is built with whole-word matching",
      fragile["matchWholeWords"], True)
check("…so it is not flagged as too broad",
      one(H.inspect([fragile], 8192), "broad-key"), None)

sturdy = profile_to_entry({
    "source": "fandom", "name": "Constance Langdon", "category": "Character",
    "aliases": [], "facts": [], "keys": ["Constance Langdon"],
    "sections": [{"title": "About", "text": "word " * 40}],
})
check("an ordinary key is left to the global setting",
      sturdy["matchWholeWords"], None)

primer = profile_to_entry({
    "source": "fandom", "name": "Arendelle", "category": "Lore",
    "aliases": [], "facts": [], "keys": ["Arendelle"], "always_on": True,
    "sections": [{"title": "The world", "text": "word " * 40}],
})
check("a primer is always on", primer["constant"], True)
check("…and goes in first", primer["order"] < 100, True)

relations = profile_to_entry({
    "source": "fandom", "name": "Elsa — Relationships",
    "category": "Relationships", "aliases": [], "facts": [],
    "keys": ["Elsa", "Anna"], "relates_to": "Elsa",
    "sections": [{"title": "Anna", "text": "word " * 40}],
})
check("a relationships entry does not drag every name it holds in with it",
      relations["preventRecursion"], True)
check("an ordinary entry still can", sturdy["preventRecursion"], False)


# ── Reporting ─────────────────────────────────────────────────────────────
if FAILED:
    print(f"\n{len(FAILED)} check(s) failed:\n")
    for line in FAILED:
        print(f"  ✕ {line}\n")
    sys.exit(1)
print("All fit-report checks passed.")
