"""
Offline checks for the Wikipedia scraper — URL handling, list parsing and the
noise filters that separate a biography from its indexes.

No network:

    python test_wikipedia.py

The section and infobox cases are the ones from the articles the rules were
written against, because "why is Filmography dropped but Legal issues kept" is
otherwise guesswork six months from now.
"""

import sys

from core.formatter import _build_keys
from scrapers import wikipedia as WP
from scrapers import wikitext as W

FAILED = []


def check(label, got, want):
    if got != want:
        FAILED.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")


def fails(label, fn):
    try:
        fn()
    except WP.WikipediaError:
        return
    FAILED.append(f"{label}\n     expected a WikipediaError, got none")


# ── URLs ──────────────────────────────────────────────────────────────────
ref = WP.parse_url("https://en.wikipedia.org/wiki/List_of_hip-hop_musicians")
check("a URL yields the API and the title",
      (ref["api"], ref["title"]),
      ("https://en.wikipedia.org/w/api.php", "List of hip-hop musicians"))
check("a section anchor is dropped",
      WP.parse_url("https://en.wikipedia.org/wiki/50_Cent#Career")["title"],
      "50 Cent")
check("other language editions work",
      WP.parse_url("https://fr.wikipedia.org/wiki/Nas")["lang"], "fr")
check("a bare host is accepted", WP.is_wikipedia_url("en.wikipedia.org/wiki/Nas"), True)
check("a Fandom link is not Wikipedia",
      WP.is_wikipedia_url("https://jujutsu-kaisen.fandom.com/wiki/Yuta_Okkotsu"), False)
check("only api.php counts as the endpoint",
      (WP.is_wikipedia_api("https://en.wikipedia.org/w/api.php"),
       WP.is_wikipedia_api("https://en.wikipedia.org/wiki/Nas"),
       WP.is_wikipedia_api("https://evil.example.com/w/api.php")),
      (True, False, False))

fails("a non-Wikipedia host is refused",
      lambda: WP.parse_url("https://example.com/wiki/Nas"))
fails("a Special: page is refused",
      lambda: WP.parse_url("https://en.wikipedia.org/wiki/Special:Random"))
fails("a Category: page is refused",
      lambda: WP.parse_url("https://en.wikipedia.org/wiki/Category:Rappers"))


# ── List detection and parsing ────────────────────────────────────────────
LIST = """This is a list of notable [[hip-hop]] musicians.
{{Dynamic list}}

== 0-9 ==
[[File:50 cent.jpg|thumb|[[50 Cent]]]]
{{div col|colwidth=30em}}
<!-- NOTE: do not add groups here -->
* [[03 Greedo]]
* [[12 Gauge (rapper)|12 Gauge]]
* [[List of Wu-Tang Clan affiliates#12 O'Clock|12 O'Clock]]
* [[50 Cent]]
{{div col end}}

== A ==
* [[A Boogie wit da Hoodie]]
* [[Aaliyah]] (also a singer, see [[R&B]])

== See also ==
* [[List of hip-hop groups]]

== References ==
{{reflist}}
"""

check("a dynamic list is recognised", WP.is_list_page("Anything", LIST), True)
check("a 'List of' title is enough on its own",
      WP.is_list_page("List of hip-hop musicians", ""), True)
check("an ordinary biography is not a list",
      WP.is_list_page("50 Cent", "'''50 Cent''' is a rapper."), False)

browse = WP.browse_list(ref, "List of hip-hop musicians", LIST)
groups = {g["category"]: g for g in browse["groups"]}
check("sections become groups, plus a combined one",
      sorted(groups), ["0-9", "A", "Everything"])
check("See also and References are not offered as groups",
      any(k in groups for k in ("See also", "References")), False)
check("image captions do not become entries",
      "50 Cent" in groups["0-9"]["pages"], True)
check("a section link has no article of its own and is skipped",
      any("Wu-Tang" in p for p in groups["0-9"]["pages"]), False)
check("the disambiguated title is what gets imported",
      "12 Gauge (rapper)" in groups["0-9"]["pages"], True)
check("…while the clean name is what gets shown",
      groups["0-9"]["labels"].get("12 Gauge (rapper)"), "12 Gauge")
check("only the first link on a line is taken",
      groups["A"]["pages"], ["A Boogie wit da Hoodie", "Aaliyah"])
check("the combined group holds every name once",
      groups["Everything"]["total"], 5)
check("the picker knows which source it is driving",
      browse["source"], "wikipedia")


# ── Section filtering, from real articles ─────────────────────────────────
for heading in ("Discography", "Filmography", "Selected filmography",
                "Awards and nominations", "Video games", "Notes",
                "References", "External links", "Concert tours",
                "Videography", "Awards and honours"):
    check(f"{heading!r} is dropped", W.is_blocked_section(heading), True)

for heading in ("Early life", "Career", "Artistry", "Personal life",
                "Legal issues", "Business ventures", "Feuds",
                "Musical style", "Public image"):
    check(f"{heading!r} is kept", W.is_blocked_section(heading), False)

check("career ranks with the most useful sections",
      W.section_priority("Career"), 1)
check("a date-range subsection inherits nothing odd",
      W.section_priority("1996-2002: Rise to fame"), 3)


# ── Infobox handling ──────────────────────────────────────────────────────
INFOBOX = """{{Infobox person
| name = 50 Cent
| birth_name = Curtis James Jackson III
| occupation = Rapper
| television = {{flatlist|* ''[[Power]]''}}
| works = {{flatlist|* [[50 Cent albums discography|Albums]]}}
| awards = [[List of awards received by 50 Cent|Full list]]
| module = {{Infobox musical artist
| embed = yes
| genre = [[East Coast hip-hop]]
| label = Shady
}}
}}
Body text.
"""
_, rows = W.parse_infobox(INFOBOX)
labels = {r["field"] for r in rows}
check("a nested infobox's rows are spliced in",
      {"genre", "label"} <= labels, True)
check("an index-of-output row is dropped",
      labels & {"television", "works", "awards"}, set())
check("real biographical rows survive",
      {"birth name", "occupation"} <= labels, True)

check("Wikipedia article furniture leaves no words behind",
      W.clean("{{Short description|American rapper}}{{Use mdy dates|date=May 2024}}"
              "{{pp-blp|small=yes}}Curtis Jackson is a rapper.").strip(),
      "Curtis Jackson is a rapper.")


# ── Trigger keys ──────────────────────────────────────────────────────────
def keys(name, category, aliases=()):
    return _build_keys({"source": "wikipedia", "name": name,
                        "category": category, "aliases": list(aliases),
                        "sections": [{"title": "Career"}]})


check("a personal name contributes its parts",
      keys("Kendrick Lamar", "Musician"),
      ["Kendrick", "Kendrick Lamar", "Lamar"])
check("a stage name containing a number is never split",
      keys("50 Cent", "Musician"), ["50 Cent"])
check("an ordinary word is not a trigger",
      keys("Nas", "Musician", ["Classic", "Nasty Nas"]), ["Nas", "Nasty Nas"])
check("an organisation's name is not split",
      keys("Death Row Records", "Brand"), ["Death Row Records"])


# ── Reporting ─────────────────────────────────────────────────────────────
if FAILED:
    print(f"\n{len(FAILED)} check(s) failed:\n")
    for line in FAILED:
        print(f"  ✕ {line}\n")
    sys.exit(1)
print("All Wikipedia checks passed.")
