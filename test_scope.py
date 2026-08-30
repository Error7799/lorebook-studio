"""
Offline checks for the sub-series scoping rules.

Everything here runs on fixed inputs with no network, so it can be run any
time the filters are tuned:

    python test_scope.py

The cases are the real ones the rules were written against — each names the
wiki it came from, because "why is this rule shaped like that" is otherwise
impossible to reconstruct later.
"""

import sys

from core.classifier import classify_fandom
from scrapers import scope
from scrapers import wikitext as W

FAILED = []


def check(label, got, want):
    if got != want:
        FAILED.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")


# ── Naming ────────────────────────────────────────────────────────────────
# The short name is what finds a spin-off's chapters, and which part of the
# title carries it differs per franchise.
check("parent of 'Jujutsu Kaisen Wiki'",
      scope.parent_name("Jujutsu Kaisen Wiki"), "Jujutsu Kaisen")
check("short of 'Jujutsu Kaisen Modulo'",
      scope.short_name("Jujutsu Kaisen Modulo", "Jujutsu Kaisen"), "Modulo")
check("short of 'My Hero Academia: Vigilantes'",
      scope.short_name("My Hero Academia: Vigilantes", "My Hero Academia"),
      "Vigilantes")
check("'Boruto: Naruto Next Generations' offers both halves",
      scope.short_names("Boruto: Naruto Next Generations", "Narutopedia"),
      ["Naruto Next Generations", "Boruto"])
# The wiki's own subject is not a sub-series of itself.
check("main series has no short name",
      scope.short_name("Jujutsu Kaisen", "Jujutsu Kaisen"), "")
check("a bare number is not a short name",
      scope.short_name("Jujutsu Kaisen 0", "Jujutsu Kaisen"), "")


# ── What counts as an instalment ──────────────────────────────────────────
for title in ("Modulo Chapter 1", "Modulo Volume 3", "Chapter 271",
              "Episode 12", "Season 2"):
    check(f"{title!r} is an instalment",
          bool(scope._INSTALMENT_RE.search(title)), True)
for title in ("Yuka Okkotsu", "Levi (No Regrets)", "Cursed Energy",
              "Koichi Haimawari"):
    check(f"{title!r} is not an instalment",
          bool(scope._INSTALMENT_RE.search(title)), False)


# ── Bookkeeping vs. lore ──────────────────────────────────────────────────
# Attack on Titan wiki: a chapter page gives nothing away in its title, but
# one publication category settles it.
check("a chapter is meta via its category",
      scope._is_meta(["No Regrets", "No Regrets Chapters", "No Regrets Volume 2"]),
      True)
check("a character filed beside chapters is not meta",
      scope._is_meta(["Characters (No Regrets Manga)", "No Regrets"]), False)
check("the author's page is meta",  scope._is_meta(["Manga"]), True)
check("the magazine is meta",       scope._is_meta(["Media"]), True)
check("a red link has no categories and is meta", scope._is_meta([]), True)


# ── Classification ────────────────────────────────────────────────────────
# An exact category name outranks one that merely contains the word.
check("'Jujutsu Sorcerer' is a concept, not a person",
      classify_fandom("", ["Jujutsu Sorcerers", "Terminology"], "Jujutsu Sorcerer"),
      "Lore")
check("a concrete kind beats the Lore fallback on a tie",
      classify_fandom("", ["Organizations", "Terminology"], "Jujutsu Headquarters"),
      "Organization")
check("a character filed under Characters stays a character",
      classify_fandom("", ["Characters", "Cursed Spirits", "Female"], "Rika"),
      "Character")
# Attack on Titan wiki qualifies its category names; the qualifier must not vote.
check("'(… Manga)' qualifier does not make a character into Media",
      classify_fandom("", ["Characters (No Regrets Manga)",
                           "Male (No Regrets Manga)", "Military (No Regrets)"],
                      "Levi (No Regrets)"),
      "Character")
check("the infobox still wins outright",
      classify_fandom("Character Infobox", ["Terminology"], "Anyone"), "Character")


# ── Link reading ──────────────────────────────────────────────────────────
check("a leading colon does not smuggle a category through",
      W.link_targets("[[:Category:Cursed Tools]] and [[Honoyagi]]"),
      [("Honoyagi", "Honoyagi")])
check("navigation templates are stripped but links survive",
      W.link_targets(W.strip_noise_templates(
          "{{Chapter Navigation|chap}}\nSee [[Yuka Okkotsu]].")),
      [("Yuka Okkotsu", "Yuka Okkotsu")])
check("a chapter's debut row names its new characters",
      W.infobox_links("{{Chapter Infobox\n|chapter title = X\n"
                      "|new character = [[Yuka Okkotsu]]<br/>[[Tsurugi Okkotsu]]\n"
                      "|previous = [[Epilogue]]\n}}", r"new character"),
      ["Yuka Okkotsu", "Tsurugi Okkotsu"])


# ── Redirect merging ──────────────────────────────────────────────────────
# "Maru" and "Marulu Val Vol Yelvori" are one character, not two walk-ons.
merged = scope.merge_redirects(
    {"Maru":                   {"seeds": 23, "mentions": 26, "debut": False},
     "Marulu Val Vol Yelvori": {"seeds": 25, "mentions": 28, "debut": True}},
    {"Maru": "Marulu Val Vol Yelvori"})
check("aliases fold onto the article they redirect to",
      merged,
      {"Marulu Val Vol Yelvori": {"seeds": 48, "mentions": 54, "debut": True}})


# ── Sampling instalments ─────────────────────────────
# Reading the first sixty of a hundred and twenty-eight chapters measures only
# the first half of the story, so a character who arrives for the finale ranks
# as a walk-on or is cut altogether.
CHAPTERS = ["Chapter %d (Vigilantes)" % i for i in range(1, 129)]
sampled = scope.sample_instalments(CHAPTERS, 80)
check("the sample is capped", len(sampled), 80)
check("it starts at the beginning", sampled[0], "Chapter 1 (Vigilantes)")
check("and reaches the finale", sampled[-1], "Chapter 128 (Vigilantes)")
check("it spans the whole run, not a prefix",
      any("Chapter 9" in t for t in sampled[40:]), True)
check("a short run is left alone",
      scope.sample_instalments(CHAPTERS[:5], 80), CHAPTERS[:5])
check("chapters sort by number, not as text",
      scope.sample_instalments(["Chapter 10 (V)", "Chapter 2 (V)"], 80),
      ["Chapter 2 (V)", "Chapter 10 (V)"])


# ── A disambiguating qualifier is not part of the story name ─────────
# The anime and the manga are the same story and must scope the same way;
# leaving "(Anime)" in searched the wiki for pages beginning
# "Vigilantes (Anime)", which is nothing.
anime = scope.identity("My Hero Academia: Vigilantes (Anime)",
                   "My Hero Academia Wiki", "")
manga = scope.identity("My Hero Academia: Vigilantes", "My Hero Academia Wiki", "")
check("the qualifier is stripped before the short name is derived",
      anime["short"], manga["short"])
check("both spellings stay searchable",
      "My Hero Academia: Vigilantes (Anime)" in anime["aliases"], True)
check("…alongside the name without it",
      "My Hero Academia: Vigilantes" in anime["aliases"], True)

# ── Reporting ─────────────────────────────────────────────────────────────
if FAILED:
    print(f"\n{len(FAILED)} check(s) failed:\n")
    for line in FAILED:
        print(f"  ✕ {line}\n")
    sys.exit(1)
print("All scoping checks passed.")
