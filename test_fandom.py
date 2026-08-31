"""
Offline checks for Fandom's tabbed subpages — the Synopsis/History tabs that
hold a character's story.

No network:

    python test_fandom.py

The cases come from the Jujutsu Kaisen wiki, where "Satoru Gojo" is 35 KB of
what he is like and "Satoru Gojo/Synopsis" is 69 KB of what happened to him.
"""

import sys

from scrapers import fandom as F
from scrapers import wikitext as W

FAILED = []


def check(label, got, want):
    if got != want:
        FAILED.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")


# ── Reading the tab bar ───────────────────────────────────────────────────
TABS = """{{Notice|cleanup}}
{{Tabs
|tab1 = Profile
|tab2 = Synopsis
|tab3 = Image Gallery
}}
{{Character_Infobox|name = Satoru Gojo}}
'''Satoru Gojo''' is a teacher.
"""

check("only the tabs worth reading are asked for",
      F.declared_subpages("Satoru Gojo", TABS), ["Satoru Gojo/Synopsis"])
check("Profile is the page itself, not a subpage",
      any("Profile" in t for t in F.declared_subpages("Satoru Gojo", TABS)), False)
check("a gallery tab is never followed",
      any("Gallery" in t for t in F.declared_subpages("Satoru Gojo", TABS)), False)
check("a page with no tab bar declares nothing",
      F.declared_subpages("Nobody", "'''Nobody''' is a person."), [])
check("other wikis' tab templates are read too",
      F.declared_subpages("X", "{{CharacterTabs|tab1=Profile|tab2=History}}"),
      ["X/History"])

# The speculative fallback, for wikis with no tab template at all.
check("guessing only ever appends known suffixes",
      F.subpage_titles(["Ann"])[:3],
      ["Ann/Synopsis", "Ann/History", "Ann/Relationships"])
check("a subpage has no subpages of its own",
      F.subpage_titles(["Ann/Synopsis"]), [])


# ── Wikis name the story tab whatever they like ───────────────────────────
# Jujutsu Kaisen calls it "Synopsis"; Kaiju No. 8 calls it "Plot" and calls the
# main tab "Info" rather than "Profile". Reading the tab bar means neither name
# has to be known in advance.
K8_TABS = """{{Spoiler}}
{{Tabs
|maxwidth = 20
|tab1 = Info
|tab2 = Image Gallery
|tab3 = Plot 
|tab4 = Relationships
}}
{{Character|title = Kafka Hibino}}
"""
check("a 'Plot' tab is followed just like a 'Synopsis' one",
      F.declared_subpages("Kafka Hibino", K8_TABS),
      ["Kafka Hibino/Plot", "Kafka Hibino/Relationships"])
check("'Info' is this wiki's word for the main tab, not a subpage",
      any("Info" in t for t in F.declared_subpages("Kafka Hibino", K8_TABS)), False)
check("a layout parameter is not mistaken for a tab",
      any("20" in t for t in F.declared_subpages("Kafka Hibino", K8_TABS)), False)

for tab in ("Plot", "Synopsis", "Story", "History", "Chronology", "Biography"):
    check(f"{tab!r} holds the story", bool(F._STORY_TAB.match(tab)), True)
for tab in ("Relationships", "Abilities", "Equipment", "Personality"):
    check(f"{tab!r} does not", bool(F._STORY_TAB.match(tab)), False)


# ── Turning a subpage into sections ───────────────────────────────────────
SYNOPSIS = """{{Tabs|tab1=Profile|tab2=Synopsis}}
== History ==
He was born in 1989.

=== Gojo's Past Arc ===
During his second year, Gojo went to check on Mei Mei.

== Synopsis ==

=== Fearsome Womb Arc ===
Gojo arrives at Sugisawa Third High School.

=== Shibuya Incident Arc ===
Gojo is sealed inside the Prison Realm.

== References ==
{{reflist}}

== Navigation ==
{{Character navi}}
"""

records = {
    "Satoru Gojo/Synopsis": {"missing": False, "title": "Satoru Gojo/Synopsis",
                             "wikitext": SYNOPSIS},
    "Satoru Gojo/Image Gallery": {"missing": False,
                                  "title": "Satoru Gojo/Image Gallery",
                                  "wikitext": "== Gallery ==\n[[File:a.png]]"},
    "Satoru Gojo/Nothing": {"missing": True, "title": "Satoru Gojo/Nothing"},
}

sections = F.subpage_sections(records, "Satoru Gojo", 4000)
titles = [s["title"] for s in sections]
# One section per arc, not one merged block: that is what makes them
# individually selectable, and what stops ten arcs sharing one section's
# ceiling and coming out as stubs.
check("each arc is its own section",
      titles, ["History", "Gojo's Past Arc", "Fearsome Womb Arc",
               "Shibuya Incident Arc"])
check("References and Navigation do not",
      any(t in titles for t in ("References", "Navigation")), False)
check("a gallery subpage contributes nothing",
      any("Gallery" in t for t in titles), False)
check("a missing subpage is simply absent", "Nothing" in str(titles), False)
check("an empty parent heading is not carried through",
      "Synopsis" in titles, False)

story = {s["title"]: s["text"] for s in sections}
check("a subpage's own lead text is kept",
      story["History"].startswith("He was born in 1989."), True)
check("each arc keeps its own prose",
      story["Shibuya Incident Arc"], "Gojo is sealed inside the Prison Realm.")

# Sections must still be trimmed to their budget.
tight = F.subpage_sections(records, "Satoru Gojo", 300)
check("a tight budget still yields something",
      bool(tight) and all(s["text"] for s in tight), True)
check("…and stays near the budget",
      sum(len(s["text"]) for s in tight) <= 900, True)


# ── Story arcs ────────────────────────────────────────────────────────────
# The same arc is not spelled the same on every page.
check("the decorative suffix is not part of an arc's identity",
      (F.arc_key("Shibuya Incident Arc"), F.arc_key("Shibuya Incident")),
      ("shibuya incident", "shibuya incident"))
check("a parenthetical is not either",
      F.arc_key("Culling Game Arc (Part 2)"), "culling game")

check("a selection matches the longer spelling of the same arc",
      F.arc_matches("Jujutsu Kaisen Modulo", {"modulo"}), True)
check("…and the shorter one",
      F.arc_matches("Modulo", {"jujutsu kaisen modulo"}), True)
check("a different arc does not match",
      F.arc_matches("Culling Game Arc", {"modulo"}), False)
check("no selection keeps everything", F.arc_matches("Anything", None), True)

ARCS_A = """== Synopsis ==
=== Fearsome Womb Arc ===
""" + ("Yuji fights a curse. " * 60) + """
=== Modulo ===
""" + ("Yuji meets Maru. " * 60) + """
=== Members ===
Two lines only.
"""
ARCS_B = """== Synopsis ==
=== Jujutsu Kaisen Modulo ===
""" + ("Yuka trains hard. " * 60) + """
"""
batch = {
    "A/Synopsis": {"missing": False, "title": "A/Synopsis", "wikitext": ARCS_A},
    "B/Synopsis": {"missing": False, "title": "B/Synopsis", "wikitext": ARCS_B},
}

arcs = F.collect_arcs(batch, ["A", "B"])
names = [a["name"] for a in arcs]
check("both spellings of one arc become a single chip",
      names.count("Modulo"), 1)
check("the shared arc is counted on both pages",
      next(a["pages"] for a in arcs if a["key"] == "modulo"), 2)
check("the shortest spelling is the one shown",
      next(a["name"] for a in arcs if a["key"] == "modulo"), "Modulo")
check("a two-line heading on one page is not offered as an arc",
      any(a["name"] == "Members" for a in arcs), False)
check("arcs are ordered by how much of the cast they cover",
      names[0], "Modulo")

# Filtering is what makes the remaining arc readable rather than a stub, so
# the budget here is deliberately too small for all of them at once.
BUDGET = 900
everything = F.subpage_sections(batch, "A", BUDGET)
just_modulo = F.subpage_sections(batch, "A", BUDGET, {"modulo"})
check("unfiltered, every section of the tab is present — chips filter the "
      "choice, not the content",
      sorted(s["title"] for s in everything),
      ["Fearsome Womb Arc", "Members", "Modulo"])
check("filtered, only the chosen arc is",
      [s["title"] for s in just_modulo], ["Modulo"])
check("…and it is longer for having the budget to itself",
      len(just_modulo[0]["text"]) > len(
          next(s["text"] for s in everything if s["title"] == "Modulo")),
      True)
check("unticking every arc yields no story at all",
      F.subpage_sections(batch, "A", BUDGET, set()), [])


# ── Story tab vs. the other tabs ──────────────────────────────────────────
# A Relationships page is one short section per person. It is lore worth
# keeping, but it is not the story and must not be filtered as though it were.
PLOT = """== The Last Wave Arc ==
""" + ("Kafka fights the kaiju. " * 80) + """
== The Compatible User Arc ==
""" + ("Kafka trains hard. " * 80)
RELS = "\n".join(f"== {name} ==\n{name} and Kafka are close. " * 4
                 for name in ("Mina Ashiro", "Reno Ichikawa", "Kaiju No. 9"))
k8 = {
    "Kafka/Plot": {"missing": False, "title": "Kafka/Plot", "wikitext": PLOT},
    "Kafka/Relationships": {"missing": False, "title": "Kafka/Relationships",
                            "wikitext": RELS},
}

check("only the story tab is divided into arcs",
      [a["name"] for a in F.collect_arcs(k8, ["Kafka"])],
      ["The Last Wave Arc", "The Compatible User Arc"])
check("people from a Relationships tab are never offered as arcs",
      any(a["name"] == "Mina Ashiro" for a in F.collect_arcs(k8, ["Kafka"])), False)

picked = F.subpage_sections(k8, "Kafka", 2000, {"the last wave"})
kept = [s["title"] for s in picked]
check("the unchosen arc is dropped", "The Compatible User Arc" in kept, False)
check("the chosen arc is kept", "The Last Wave Arc" in kept, True)
check("relationships survive an arc filter — they are not story",
      "Mina Ashiro" in kept, True)

# The story tab gets a reserved share rather than competing section-by-section.
arc_chars = next(len(s["text"]) for s in picked
                 if s["title"] == "The Last Wave Arc")
rel_chars = max(len(s["text"]) for s in picked if s["title"] != "The Last Wave Arc")
check("a dozen short relationship sections cannot crowd out the chosen arc",
      arc_chars > rel_chars * 2, True)


# ── The shared flattener ──────────────────────────────────────────────────
flat = W.flatten_sections([
    {"title": "Career", "level": 2, "priority": 1, "body": "Lead."},
    {"title": "Early", "level": 3, "priority": 2, "body": "First."},
    {"title": "Late",  "level": 3, "priority": 2, "body": "Second."},
    {"title": "Other", "level": 2, "priority": 3, "body": "Separate."},
])
check("subsections fold into their parent",
      [s["title"] for s in flat], ["Career", "Other"])
check("their text is joined in order",
      flat[0]["text"], "Lead.\n\nEarly: First.\n\nLate: Second.")
check("a parent inherits the best priority beneath it",
      flat[0]["priority"], 1)


BOLD = "'''"

# ── Arc level: which heading depth a story tab tells its arcs at ──────────
# Both the My Hero Academia and Jujutsu Kaisen wikis nest arcs under container
# headings, so assuming level 2 finds none and taking every heading offers
# "History" as an arc.
NESTED = """== History ==
He was born in 1989.

=== Gojo's Past Arc ===
During his second year, Gojo went to check on Mei Mei.

== Synopsis ==

=== Shibuya Incident Arc ===
Gojo is sealed inside the Prison Realm.
"""

nested = F.story_sections(NESTED)
check("the arcs are the arcs",
      [s["title"] for s in nested if s["is_arc"]],
      ["Gojo's Past Arc", "Shibuya Incident Arc"])
check("a lead-in above them is not an arc",
      next(s["is_arc"] for s in nested if s["title"] == "History"), False)
check("an empty container is scaffolding and does not survive the fold",
      [s["title"] for s in F.collapse_sections(nested)],
      ["History", "Gojo's Past Arc", "Shibuya Incident Arc"])

FLAT = """== Sky Egg Arc ==
Koichi climbs the tower.

== Epilogue Arc ==
Koichi becomes a sidekick.
"""
check("a wiki that lists arcs at the top level works too",
      [s["title"] for s in F.story_sections(FLAT) if s["is_arc"]],
      ["Sky Egg Arc", "Epilogue Arc"])

SCENES = """== Sky Egg Arc ==
Koichi climbs the tower.

=== The Ascent ===
He is slowed by the wind.

== Epilogue Arc ==
Koichi becomes a sidekick.
"""
scened = F.story_sections(SCENES)
check("a scene inside an arc is not itself an arc",
      [(s["title"], s["is_arc"]) for s in scened],
      [("Sky Egg Arc", True), ("The Ascent", False), ("Epilogue Arc", True)])
check("…and with nothing picked its prose folds into the arc above it",
      "The Ascent: He is slowed by the wind."
      in F.collapse_sections(scened)[0]["text"], True)

check("a heading that names an arc is recognised",
      [bool(F._ARC_TITLE.search(t)) for t in
       ("Sky Egg Arc", "Chapter 3", "Part II", "History", "Personality")],
      [True, True, True, False, False])


# ── Every arc survives the budget ───────────────────────
# Sections that lose the tie-break are always the *last* ones, so dropping
# them discards the ending. Koichi Haimawari lost his final four arcs — the
# climax included — to that rule.
LONG_STORY = {"Koichi/Synopsis": {
    "missing": False, "title": "Koichi/Synopsis",
    "wikitext": "".join(
        f"== Arc {i} ==" + chr(10) + ("Something happens. " * 60) + chr(10)
        for i in range(1, 12))}}
fitted = F.subpage_sections(LONG_STORY, "Koichi", 2000)
check("no arc is dropped, however tight the budget", len(fitted), 11)
check("…and every one of them says something",
      all(s["text"] for s in fitted), True)
check("the last arc is present, not truncated away",
      fitted[-1]["title"], "Arc 11")


# ── Cleaning ──────────────────────────────────
check("a page-theme switch is not article text",
      W.clean("{{PageTheme|affiliation-pro_heroes}}Koichi is a hero."),
      "Koichi is a hero.")
check("a per-source citation template leaves no number behind",
      W.clean("He becomes The Skycrawler.{{Ref/Vigilantes|chap=125}}"),
      "He becomes The Skycrawler.")

QUOTE = ("{{Quotes|" + chr(34) + "Nothing like doing good!" + chr(34) + "|"
         + BOLD + "Koichi" + BOLD + " in [[Chapter 1|"
         + chr(34) + "I am Here" + chr(34) + "]]}}")
check("a signature quote keeps the quote, not just the attribution",
      W.clean(QUOTE),
      chr(34) + "Nothing like doing good!" + chr(34)
      + " — Koichi in " + chr(34) + "I am Here" + chr(34))

check("an appearance index is not a section",
      [W.is_blocked_section(t) for t in
       ("Chapter Appearances", "Anime Appearances", "Appearance")],
      [True, True, False])

# A boundary far below the limit throws away the allowance it was given.
PARA = "First para." + chr(10) * 2 + ("Then a long sentence follows. " * 12)
check("trimming prefers a near-full cut over a distant paragraph break",
      len(W.trim_text(PARA, 300)) > 200, True)

# ── Budget by how central an entry is ──────────────────────
# Scope already knows who carries the story; spending the same budget on a
# walk-on is what makes a large import mostly filler.
check("a lead gets the whole budget", F.tier_budget(14000, "core"), 14000)
check("an unranked page is treated as a lead",
      F.tier_budget(14000, None), 14000)
check("supporting cast gets less", F.tier_budget(14000, "supporting") < 14000, True)
check("a walk-on gets least of all",
      F.tier_budget(14000, "background") < F.tier_budget(14000, "supporting"), True)
check("…but never so little that the entry says nothing",
      F.tier_budget(500, "background") >= F.MIN_TIER_BUDGET, True)


# ── Relationships as an entry of its own ────────────────────
RELATIONS = """== Family ==

=== Shoko Haimawari ===
His mother, who does not know he is a vigilante.

== Allies ==

=== Rapt Tokage and Moyuru Tochi ===
Two heroes he works alongside.
"""
SUBJECT = "Koichi Haimawari"
REL_RECORDS = {
    SUBJECT: {"missing": False, "title": SUBJECT,
              "wikitext": "{{Character Infobox" + chr(10)
                          + "|name = " + SUBJECT + chr(10) + "}}"},
    SUBJECT + "/Relationships": {
        "missing": False, "title": SUBJECT + "/Relationships",
        "wikitext": RELATIONS},
    SUBJECT + "/Synopsis": {
        "missing": False, "title": SUBJECT + "/Synopsis",
        "wikitext": "== Sky Egg Arc ==" + chr(10) + "He climbs."},
}
rel = F.build_relationship_profile(
    {"api": "", "site": "https://x.fandom.com", "title": SUBJECT},
    REL_RECORDS[SUBJECT], REL_RECORDS, "X Wiki", 4000)
check("the entry is named for its subject",
      rel["name"], "Koichi Haimawari — Relationships")
check("it holds one section per person",
      [s["title"] for s in rel["sections"]],
      ["Shoko Haimawari", "Rapt Tokage and Moyuru Tochi"])
check("it fires on the other person as well as the subject",
      all(k in rel["keys"] for k in
          ("Koichi Haimawari", "Shoko Haimawari", "Rapt Tokage", "Moyuru Tochi")),
      True)
check("the subpage title is not offered as a trigger",
      any("Relationships" in k for k in rel["keys"]), False)

# Split out, the tab must not also be left in the character entry.
kept = F.subpage_sections(REL_RECORDS, SUBJECT, 4000, skip=F._RELATION_TAB)
check("the character entry no longer carries them",
      any(s["title"] == "Shoko Haimawari" for s in kept), False)
check("…but keeps its story", any("Sky Egg" in s["title"] for s in kept), True)

no_tab = {"A": {"missing": False, "title": "A", "wikitext": "x"}}
check("a page without the tab yields no entry",
      F.build_relationship_profile({"api": "", "site": "", "title": "A"},
                                   no_tab["A"], no_tab, "W", 4000), None)

# ── One page, one home ───────────────────────────────
# The Jujutsu Kaisen wiki lists Yuta Okkotsu under six categories at once, so
# 349 real pages arrived as 663 across eighteen chips and ticking two of them
# imported the same person twice.
GROUPS = [
    {"category": "Characters", "total": 3,
     "pages": ["Yuta", "Maki", "Toge"], "subgroups": []},
    {"category": "Jujutsu Sorcerers", "total": 4,
     "pages": ["Yuta", "Maki", "Toge", "Panda"], "subgroups": []},
    {"category": "Characters by Occupation", "total": 2,
     "pages": ["Yuta", "Toge"], "subgroups": []},
    {"category": "Locations", "total": 1,
     "pages": ["Tokyo Jujutsu High"], "subgroups": []},
]
deduped = F.dedupe_groups(GROUPS)
listed = [p for g in deduped for p in g["pages"]]
check("nothing is listed twice", len(listed), len(set(listed)))
check("nothing is lost either",
      set(listed), {"Yuta", "Maki", "Toge", "Panda", "Tokyo Jujutsu High"})
check("a re-slice stops being a category of its own",
      [g["category"] for g in deduped], ["Characters", "Locations"])
check("a page only it had still gets in",
      "Panda" in [g for g in deduped if g["category"] == "Characters"][0]["pages"],
      True)
facets = [s["category"]
          for g in deduped if g["category"] == "Characters"
          for s in g["subgroups"]]
check("…and it comes back as a filter instead",
      sorted(facets), ["Characters by Occupation", "Jujutsu Sorcerers"])
check("an index category is demoted however little it overlaps",
      "Characters by Occupation" in facets, True)


# ── Pictures ─────────────────────────────────────
# A name means nothing if you do not already know the cast, so the picker
# shows the wiki's own lead image — fetched through the app, and only from
# the wikis it is browsing.
check("a wiki image is fetchable",
      F.is_image_url("https://static.wikia.nocookie.net/x/images/a/b/c.png"), True)
check("somebody else's host is not",
      F.is_image_url("https://evil.example.com/x.png"), False)
check("nor is plain http",
      F.is_image_url("http://static.wikia.nocookie.net/x.png"), False)
check("nor is a missing address", F.is_image_url(""), False)
check("a wiki's own 'no picture' graphic does not count as one",
      [bool(F._PLACEHOLDER_IMAGE.search(u)) for u in
       ("d/d5/NoPicAvailable.png", "9/95/Akari_Nitta.png")],
      [True, False])

# ── Which story first ───────────────────────────────
# A franchise wiki holds several stories, and "characters" is the wrong first
# question until you have said characters from what.
# A fixed list of category names was not enough: the Marvel wiki has no
# category called "Movies" at all — its 47 films are spread across "Released
# Movies", "Upcoming Movies" and one per phase — so the ending is what is read.
check("a shelf is recognised by what it ends in",
      [next((k for suf, k in F.WORK_SUFFIXES
             if n.lower() == suf or n.lower().endswith(" " + suf)), None)
       for n in ("Released Movies", "Phase One Movies", "Disney+ Series",
                 "Short Films", "Light Novels", "Characters")],
      ["Movies", "Movies", "TV Series", "Short Films", "Novels", None])
check("…and the rest of the name says which part of the story it is",
      [F._shelf_label(n, k) for n, k in
       (("Phase One Movies", "Movies"), ("Released Movies", "Movies"),
        ("Movies", "Movies"))],
      ["Phase One", "Released", ""])

# Marvel titles a film "Avengers: Endgame"; the namespace test used to read
# that first word as a prefix and threw fifteen films away.
check("a colon in a title is not a namespace",
      [F._is_work_candidate(t) for t in
       ("Avengers: Endgame", "Thor: Ragnarok", "Category:Movies",
        "File:Poster.png", "Iron Man (film)/Gallery")],
      [True, True, False, False, False])
check("a song filed under Movies is not a work",
      "music" in F._NOT_A_WORK and "lyrics" in F._NOT_A_WORK, True)
check("nor is the author filed under Manga",
      "authors" in F._NOT_A_WORK, True)
check("nor is an index of them", "lists" in F._NOT_A_WORK, True)

check("an infobox tells a work from its author",
      [bool(F._WORK_INFOBOX.search(t)) for t in
       ("Series Infobox", "Movie", "TV", "Documentary", "Character Infobox")],
      [True, True, True, True, False])

check("a subpage is never offered as a work",
      F._is_work_candidate("Attack on Titan, The Movie: Part 1/Videos"), False)
check("…but the film itself is",
      F._is_work_candidate("Attack on Titan, The Movie: Part 1"), True)

# A Cast section links the wiki's overflow lists as well as its people.
check("an overflow list is not a member of the cast",
      [i["title"] for i in F._dedupe_items(
          [{"title": "Lobo"}, {"title": "List of Minor Characters"},
           {"title": "Krypto the Superdog"}, {"title": "Character List"}])],
      ["Lobo", "Krypto the Superdog"])

# ── A cast is more than its first heading ────────────────────
# The Invincible wiki lists four people under "Main Characters" and thirty
# more under "Supporting" and "Guest"; only the first heading was read, so a
# whole season offered a cast of four.
check("every kind of cast heading counts",
      [bool(F._CAST_SECTION.match(t)) for t in
       ("Cast", "Main Characters", "Supporting Characters", "Guest Characters",
        "Cast and Characters", "Voice Cast")],
      [True, True, True, True, True, True])
check("but a plot heading does not",
      [bool(F._CAST_SECTION.match(t)) for t in
       ("Plot", "Locations", "Character Development", "Cast Gallery")],
      [False, False, False, False])


# ── An arc written entirely in scenes ───────────────────────
# "Season 1" carries no prose of its own — its eight subsections are the
# season. Dropping the empty heading left them nothing to fold into, and
# 111 KB of history came out as one nameless block.
SEASONS = """== Background ==

=== Early Life ===
He was born on Earth and grew up ordinary.

== Season 1 ==

=== The Birth of Invincible ===
His powers arrive and he takes the name Invincible.

=== Flaxan Invasion ===
The Flaxans attack and the Guardians answer.

== Season 2 ==

=== New Employment ===
He starts working for the agency that lied to him.
"""
# The tree keeps every heading, so a single scene can be picked on its own…
seasons = F.story_sections(SEASONS)
check("the tree keeps the scenes as well as the seasons",
      [s["title"] for s in seasons],
      ["Background", "Early Life", "Season 1", "The Birth of Invincible",
       "Flaxan Invasion", "Season 2", "New Employment"])
check("a scene knows which season encloses it",
      [s["path"] for s in seasons if s["title"] == "Flaxan Invasion"],
      [["season 1", "flaxan invasion"]])

# …and with nothing picked it folds back to something readable.
folded = F.collapse_sections(seasons)
check("an empty season heading survives to hold its scenes",
      [s["title"] for s in folded], ["Background", "Season 1", "Season 2"])
check("the scenes are folded into the season they belong to",
      "Flaxan Invasion" in folded[1]["text"], True)
check("nothing lands in the wrong season",
      "Flaxan Invasion" in folded[2]["text"], False)

# Picking an arc takes its scenes; picking a scene takes only that scene.
check("picking a season takes the scenes inside it",
      F.path_matches(["season 1", "flaxan invasion"], {"season 1"}), True)
check("picking one scene takes only that scene",
      [F.path_matches(p, {"flaxan invasion"}) for p in
       (["season 1", "flaxan invasion"], ["season 1", "the birth of invincible"])],
      [True, False])
check("picking nothing keeps everything",
      F.path_matches(["season 2"], None), True)

# The season page links its episodes, and those name the world the cast
# moves through — a cast list alone gives a lorebook no places or factions.
EPISODES = """== Episodes 1-2 ==
* [[It's About Time]]
* [[Here Goes Nothing]]

== Cast ==
* [[Steven Yeun]] as [[Invincible]]
"""
links, unit = F.instalment_links(EPISODES)
check("the episode list is read off the work's own page",
      links, ["It's About Time", "Here Goes Nothing"])
check("…and it says what they are called",  unit, "episodes")
check("a page with no episode list yields none",
      F.instalment_links("== Cast ==" + chr(10) + "* [[Someone]]")[0], [])

# ── A wiki that keeps the cast in categories ──────────────────
# The Disney wiki puts no cast list on a film page at all — "Frozen" is 35 KB
# of plot — and files Elsa under "Frozen characters" and Arendelle under
# "Frozen locations" instead.
check("a category named after the work, holding what is in it",
      [bool(F._WORK_CONTENT_SUFFIX.search(t)) for t in
       ("characters", "locations", "objects", "events", "organizations")],
      [True, True, True, True, True])
check("…but not the ones holding something else about it",
      [bool(F._WORK_CONTENT_SKIP.search(t)) for t in
       ("people", "songs", "galleries", "books", "relationships")],
      [True, True, True, True, True])

# A shelf of works, versus a shelf of things about works.
check("a shelf of works is a shelf",
      [bool(F._NOT_A_SHELF.search(t)) for t in
       ("Films", "Released Movies", "Phase One Movies", "TV Series")],
      [False, False, False, False])
check("a shelf of characters is not, however it ends",
      [bool(F._NOT_A_SHELF.search(t)) for t in
       ("Characters in video games", "Songs in video games",
        "Users who are fans of Disney comics", "Film Galleries")],
      [True, True, True, True])

# {{Cite book}} was enough to make Maui and Te Fiti look like published works.
check("a citation template is not an infobox",
      [F.is_work_template(t) for t in
       ("Cite book", "Infobox film", "Movie", "TV", "Navbox series",
        "Character Infobox")],
      [False, True, True, True, False, False])

# ── Arcs inside arcs ────────────────────────────────
# Blue Lock nests matches inside arcs inside a plot, and is not consistent
# about the depth: later arcs sit at level 2 beside the plot rather than under
# it. Depth cannot decide what an arc is; the name does, at any level.
NESTED_ARCS = """== History ==
He played for his school.

== Plot ==

=== Introduction Arc ===
He is invited to Blue Lock.

=== First Selection Arc ===

==== Team X vs Team Z ====
Team Z wins on a last-minute goal.

==== Team Y vs Team Z ====
Team Z loses and has to regroup.

== Second Selection Arc ==
The survivors are paired off.
"""
tree = F.story_sections(NESTED_ARCS)
check("an arc is an arc at whatever level the wiki files it",
      [t["title"] for t in tree if t["is_arc"]],
      ["Introduction Arc", "First Selection Arc", "Second Selection Arc"])
check("a match knows the arc it belongs to",
      [t["path"] for t in tree if t["title"] == "Team X vs Team Z"],
      [["plot", "first selection", "team x vs team z"]])

check("picking the arc takes every match in it",
      [F.path_matches(t["path"], {"first selection"}) for t in tree
       if t["title"].startswith("Team")],
      [True, True])
check("picking one match takes only that match",
      [F.path_matches(t["path"], {"team x vs team z"}) for t in tree
       if t["title"].startswith("Team")],
      [True, False])
check("a different arc is untouched by either",
      F.path_matches([t["path"] for t in tree
                      if t["title"] == "Second Selection Arc"][0],
                     {"first selection"}), False)

# With nothing picked the tree folds to something readable — but "Plot" is
# scaffolding two levels above the matches, so folding into it would merge
# every arc into one block.
folded = [t["title"] for t in F.collapse_sections(tree)]
check("the plot container steps aside and its arcs stand up",
      folded,
      ["History", "Introduction Arc", "First Selection Arc",
       "Second Selection Arc"])
check("…carrying the matches inside them",
      all(w in [t for t in F.collapse_sections(tree)
                if t["title"] == "First Selection Arc"][0]["text"]
          for w in ("Team X vs Team Z:", "Team Y vs Team Z:")), True)

# ── No stray control characters in the source ─────────────────
# A backslash-b written into a pattern as a literal backspace is invisible in
# every editor and turns a word boundary into a character nothing matches. It
# has silently disabled four regexes so far, so it is now checked for.
import glob as _glob
import io as _io
_stray = []
for _path in (_glob.glob("scrapers/*.py") + _glob.glob("core/*.py")
              + _glob.glob("templates/*.html") + ["app.py"]):
    if chr(8) in _io.open(_path, encoding="utf-8").read():
        _stray.append(_path)
check("no source file contains a literal backspace", _stray, [])


# ── A spin-off is named after what it span off from ───────────────
# A film with a colon in its title is not one: "Avengers: Endgame" was taken
# for a sub-series called "Endgame" and searched for across the whole wiki.
check("a spin-off carries its parent's name",
      [F.is_series_page(box, "", title, wiki) for title, wiki, box in (
          ("My Hero Academia: Vigilantes", "My Hero Academia Wiki",
           "Series Infobox"),
          ("Jujutsu Kaisen Modulo", "Jujutsu Kaisen Wiki", "Series Infobox"))],
      [True, True])
check("a film with a colon in its title does not",
      F.is_series_page("Movie", "", "Avengers: Endgame",
                       "Marvel Cinematic Universe Wiki"), False)
check("and the wiki's own subject is the whole wiki",
      [F.is_wiki_subject(t, "Blue Lock Wiki") for t in
       ("Blue Lock (Manga)", "Blue Lock", "Blue Lock - Episode Nagi")],
      [True, True, False])


# ── Pages that are never entries ──────────────────────────
# Browsing a whole wiki took category members verbatim, so Demon Slayer
# offered "Chapter 67" and eight paint books as things to import.
check("instalments and merchandise are not entries",
      [F.is_lorebook_page(t) for t in
       ("Chapter 67", "Blu-ray & DVD: Mugen Train - Volume 1",
        "Calendar/2016", "Kimetsu no Yaiba Paint Book: Blue")],
      [False, False, False, False])
check("…but a character with a number in its name is",
      [F.is_lorebook_page(t) for t in
       ("Tanjiro Kamado", "Number 6", "Chapter Master", "Mugen Train")],
      [True, True, True, True])

# A wiki writes "Coming soon!" where the writing has not happened yet, and
# Solo Leveling does it for fourteen of its twenty arcs.
check("an unwritten section counts as empty",
      [W.is_placeholder(t) for t in
       ("Coming soon!", "TBA", "To be added.", "N/A")],
      [True, True, True, True])
check("…and real prose does not",
      W.is_placeholder("Jinwoo was born on March 8th."), False)

# Navigation furniture named after a medium is not an infobox for one.
check("a navigation template does not make a page a work",
      [F.is_work_template(t) for t in
       ("Anime Navigation", "Game Navigation", "Episode List", "Anime")],
      [False, False, False, True])
check("a page's own infobox can correct the shelf it was found on",
      [F.template_kind([t]) for t in ("Game", "Movie", "TV")],
      ["Video Games", "Movies", "TV Series"])

# ── Reporting ─────────────────────────────────────────────────────────────
if FAILED:
    print(f"\n{len(FAILED)} check(s) failed:\n")
    for line in FAILED:
        print(f"  ✕ {line}\n")
    sys.exit(1)
print("All Fandom subpage checks passed.")
