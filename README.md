# ✦ Lorebook Studio

A local web app for **making and editing SillyTavern / Marinara Engine World Info lorebooks** — two tools in one:

| Tool | What it does |
|------|--------------|
| **⚒ Maker** (`/maker`) | Scrapes Wikipedia, Wikidata, **Fandom wikis** and public social media pages to auto-generate lorebook entries — real people and brands, or the characters, locations, factions and story arcs of a fictional world. No LLM/AI APIs — all data from public sources. |
| **✎ Editor** (`/editor`) | Bulk-edit any lorebook: activation strategies, filters, find & replace, duplicate-key detection, undo, and safe saving. |

Build a lorebook in the Maker, click **Open in Editor**, and fine-tune it — no export/import round-trip needed.
Come back to it later by dropping the file onto the Maker: new entries are added onto the
book you already have, and the Editor's **⚒ Add entries** hands its open lorebook straight back.

## Run

Works on **macOS, Linux and Windows** (Python 3.9+).

### macOS / Linux

```bash
./run.sh
```

(or manually: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python app.py`)

### Windows

```bat
run.bat
```

Then open <http://127.0.0.1:5000>. Set `PORT` / `HOST` env vars to change the bind address.

---

## ⚒ Maker

The page is one workspace: a slim bar to say where to look, the pictures filling
everything, and what you have collected kept to one side.

```
┌─────────────────────────────────────────────┬────────────┐
│ [Wikipedia|Fandom]  paste a link…   [Open] [⚙] │  LOREBOOK   │
├─────────────────────────────────────────────┤  □ Elsa     │
│  □ □ □ □ □   ← whatever you are picking from  │  □ Anna     │
│  □ □ □ □ □                                     │  □ Olaf     │
└─────────────────────────────────────────────┴────────────┘
```

The picker used to be a modal on top of a page of forms, which put the one
genuinely visual thing behind a click and gave the top half of the screen to
explanatory prose. Now the posters and portraits *are* the page; the settings
that used to fill a column live behind the ⚙, and the export controls sit under
the entry list where they are needed rather than above it.

The two sources are switched with the chips at the left of the bar.

### 🌐 Wikipedia — real people, brands and places

Type a name, or paste any `wikipedia.org` link. Both end up as the same thing:
an article to scrape.

- **A name** — celebrity, brand, athlete, place — searches Wikipedia, with an
  autocomplete dropdown of matching articles.
- **An article link** (`…/wiki/Gucci`) becomes one entry directly.
- **A list page** (`…/wiki/List_of_hip-hop_musicians`) opens a picker instead —
  see below.

Wikipedia is MediaWiki, exactly like Fandom, so an article is read the same way:
raw wikitext, infobox parsed as literal `|key = value` pairs, sections filtered
structurally. That is what makes the filtering reliable rather than best-effort.

| Kept | Dropped |
|------|---------|
| Early life, Career, Artistry, Musical style | **Discography**, **Filmography**, **Videography**, **Bibliography** |
| Personal life, Legal issues, Controversies | **Awards and nominations** and every subsection under it |
| Business ventures, Philanthropy, Public image | **Video games**, **Notes**, **References**, **External links** |
| Infobox: birth name, born, origin, genres, labels, years active, spouse, education | Infobox: **Television**, **Works**, **Awards** ("Full list"), images, signature |

Nested infoboxes are unwrapped, which matters more than it sounds: a musician's
**genres and labels** live inside a second `{{Infobox musical artist}}` embedded
in the outer `{{Infobox person}}`, and reading only the outer one finds a field
called "module" holding an unreadable blob.

Article furniture — `{{Short description}}`, `{{Use mdy dates}}`,
`{{pp-blp}}` — is dropped rather than rendered, so a biography no longer opens
with "yes / September 2024 / March 2025". The short description *is* kept, as
the entry's one-line summary: it is written to be exactly that ("American rapper
and actor (born 1975)") and beats the first sentence, which is usually a hundred
words of birth name, dates and honorifics.

**Subsections are folded into their parent.** 50 Cent's "Career" has six
date-range subsections and "Feuds" has nine, one per rival. Budgeted
individually each gets a couple of hundred characters and the entry becomes a
list of stubs; merged, "Career" is one block that actually reads. Sub-headings
survive inline ("Ja Rule: …"), so you still know whose feud is being described.

On top of the article, a single entry also pulls:
   - Wikidata structured facts (birth date, height, occupations, genres, founders…)
   - Social handles (from Wikipedia external links + Wikidata) with live follower
     counts where scrapable (Instagram, TikTok, YouTube)

Live follower counts are a page fetch each and Instagram tends to stall rather
than refuse, so the whole attempt is capped at 15 seconds and there is a
checkbox to skip it. Handles are still recorded either way; only the numbers are
given up on. Bulk imports never fetch them — a hundred entities would mean a
hundred stalls.

#### A list page is a menu, not an entry

"List of hip-hop musicians" is 4,853 links across an A–Z. It is not a lorebook
entry and never should be, so it opens the same picker a Fandom wiki's
categories get:

```
📋 List of hip-hop musicians
   — 4,853 linked articles in 27 sections. Each one you tick is scraped from
     its own article, not from this list.

📜 Everything (4853)  📜 0–9 (57)  📜 A (260)  📜 B (342)  📜 C (262)  …
```

Tick the ones your roleplay needs and each is scraped from **its own article** —
so picking 40 rappers gives 40 full biographies, not 40 lines copied off an index.

The **Everything** chip is there because an A–Z is 27 chips and what people
actually want is to find one name; the filter box then searches the whole list
instead of whichever letter happens to be open.

Lists are messy, and the parser accounts for it:

| Situation | What happens |
|-----------|--------------|
| `[[12 Gauge (rapper)\|12 Gauge]]` | Shows **12 Gauge**, imports the disambiguated article |
| `[[List of Wu-Tang Clan affiliates#12 O'Clock\|12 O'Clock]]` | Skipped — a section link has no article of its own |
| `[[File:50 cent.jpg\|thumb\|[[50 Cent]]]]` | The caption is not an entry; the person still is |
| `* [[Aaliyah]] (also a singer, see [[R&B]])` | Only the first link on a line is taken |
| `See also`, `References` | Never offered as groups |

### 📚 Fandom — fictional worlds

Paste any `fandom.com` / `wikia.org` link.

- **An article link** (`…/wiki/Kinji_Hakari`) becomes one entry.
- **A sub-series link** (`…/wiki/Jujutsu_Kaisen_Modulo`) becomes an entry *and*
  opens a picker holding **only that story's** cast, places and events — even
  when the wiki files them together with the rest of the franchise. See below.
- **A film, episode or comic link** (`…/wiki/Spider-Man:_Homecoming`) becomes an entry *and*
  opens its own picker — see below.
- **The wiki's front page** (`…/wiki/Jujutsu_Kaisen_Wiki`) opens a **browser**: the wiki's
  lore categories with checkboxes, a filter, and full-text search across the wiki. Tick what
  your roleplay needs and import the lot in one click.
- **A category link** (`…/wiki/Category:Characters`) opens the browser on that category.

#### Every wiki is organised differently

Categories are a tree, and wikis use it in opposite ways — so the browser reads the tree
rather than one flat level, and offers each category's **subcategories as sub-filters**:

| Wiki | What `Category:Characters` actually holds | What you get |
|------|------------------------------------------|--------------|
| **Avatar** | 1 page, and 19 subcategories — the 287 Na'vi and 178 Humans are one level down | 529 characters, filterable by Na'vi / Humans / Avatars / Antagonists |
| **American Horror Story** | 349 pages *and* subcategories slicing them per season | 349 characters, filterable by Characters of Coven / Murder House / Asylum… |
| **Jujutsu Kaisen** | 140 pages, a few subcategories | 140 characters, filterable by Cursed Spirits / Simurians |

Beyond the categories every wiki tends to have, the browser also surfaces each wiki's own
organising axis. American Horror Story files each season under its own category, so all twelve
— *Murder House*, *Asylum*, *Coven*, *Freak Show*, *Hotel*, *Roanoke*, *Cult*, *Apocalypse*,
*1984*, *Double Feature*, *NYC*, *Delicate* — appear as top-level picks. Click **1984** and you
get that season's 31 pages, narrowed further by *Characters of 1984* or *Locations of "1984"*.
One lorebook per season, which is how that show actually works.

Subcategory page lists load when you open them, so indexing even a large wiki takes a few
seconds rather than a few minutes.

#### One story, not the whole franchise

A wiki is organised around its franchise, not around the part of it you want to
roleplay. The Jujutsu Kaisen wiki covers the 2018 manga **and** its 2025 sequel
*Jujutsu Kaisen Modulo* in one namespace, and nothing separates them —
`Category:Characters` mixes both casts, `Story Arcs` covers only the original,
and Modulo's own leads carry exactly the same categories as characters who never
appear in it. Filing is no help, so scope is **derived**.

Paste a sub-series link — `…/wiki/Jujutsu_Kaisen_Modulo` — and the picker holds
only that story:

```
🎯 Scoped to Jujutsu Kaisen Modulo only
   — read from its 28 own chapters and volumes, not the rest of Jujutsu Kaisen.

⚔️ Characters (34)  🏯 Organizations (2)  📍 Locations (4)  ⚡ Events (1)
✨ Abilities & Techniques (20)  🗡️ Items (4)  📜 Lore & Terminology (21)
```

What makes that possible is the story's own instalments. A spin-off's chapters
and episodes are unambiguously its own — titled after it (`Modulo Chapter 1`),
filed under it (`Category:Jujutsu Kaisen Modulo Chapters`) — and everything that
matters to the story is linked from them, usually many times over. So the
instalments are found, read, and *measured*.

Wikis take one of two approaches and both work:

| Wiki | How it files a spin-off | What is done about it |
|------|-------------------------|-----------------------|
| **Attack on Titan** | *No Regrets* has its own category and disambiguated titles (`Levi (No Regrets)`) | Taken at its word — those pages **are** the scope |
| **Jujutsu Kaisen** | *Modulo*'s cast sits in the same `Category:Characters` as everyone else's; only its chapters carry the name | Read out of the 28 chapters and ranked |
| **My Hero Academia** | *Vigilantes* has scoped chapter and episode categories | Both routes at once |

Links are read from **wikitext with navigation boxes stripped**, never from the
API's link list. A navbox listing all 271 chapters of the parent series is
transcluded onto every one of the spin-off's chapter pages, and `prop=links`
cannot tell that apart from the cast — stripping it first is the difference
between a clean 60-entry lorebook and a 400-entry franchise dump.

Then each candidate is checked against the wiki before being offered:

| Dropped | Why |
|---------|-----|
| Red links | The wiki names people it never wrote an article for — they would import empty |
| `Gege Akutami`, `Yuji Iwasaki`, `Weekly Shonen Jump` | Linked from the series infobox's *author* / *publisher* rows — the people who made the story, not people in it |
| `Modulo Chapter 1…25`, `Volumes & Chapters`, `List of Characters` | Instalments and indexes: the evidence, and the wiki's table of contents |
| `Marulu Val Vol Yelvori/Image Gallery` | Presentation subpages |

Aliases are folded onto the article they redirect to, so `Maru`, `Cross`,
`Usami` and `Dabura` stop looking like four minor walk-ons and become the four
main characters they actually are.

Each surviving entry is then rated by **how much of the story it appears in** —
which is measured, not guessed:

| Dot | Tier | Meaning |
|-----|------|---------|
| 🟢 | Core | Named in a chapter's *new character* row, filed under the story by the wiki, or appearing across a good share of it |
| 🔵 | Supporting | Appears in several chapters |
| ⚫ | Background | Appears in one — hidden until you ask for it |

**Select main cast** takes every core entry across all groups in one click.

Nothing here is specific to one wiki: no hard-coded titles, no per-wiki rules.
If a wiki keeps no instalment pages for a story and files nothing under its name
there is nothing to measure, and the link simply becomes one ordinary entry.

#### One film, not the whole franchise

A shared-universe wiki is far too big for one lorebook — the Marvel Cinematic Universe wiki has
thousands of characters across dozens of films. But a film's own page already says exactly what
belongs in *its* story: the **Cast** and **Appearances** sections list every character,
location, event, item, vehicle, species and organisation that turns up in it, each already
linked to its article.

So pasting a film link adds the film as an entry and opens a picker built from that list:

```
Spider-Man: Homecoming — what appears in it
⚔️ Characters (42)  📍 Locations (29)  ⚡ Events (10)  🗡️ Items (31)
📜 Media (3)  📜 Vehicles (5)  🐉 Sentient Species (1)  🐉 Creatures (3)  🏯 Organizations (11)
```

Cast lines read `[[Tom Holland]] as [[Spider-Man|Peter Parker/Spider-Man]]` — only what follows
the "as" is taken, so you get the character and never the actor.

Wikis annotate how much a thing really features: `(mentioned)`, `(background)`, `(footage)`,
`(deleted scene)`, `(logo)`. Those are hidden by default — the Chrysler Building being visible
in a skyline shot does not deserve a lorebook entry — and a checkbox brings them back, greyed
and labelled with their annotation, when you want them.

Everything imported this way is grouped under the film's name, so the Editor can filter or bulk-edit
one story's entries together.

#### Filtering vs. searching

The box at the top **filters the pages currently listed** — it never navigates you away from
the category you are working in. To search the whole wiki instead, press **Enter** or click the
*Search all of …* chip that appears as you type. Wiki search drops cast and crew articles and
production subpages, which otherwise crowd out the characters (searching a show wiki for a
season name returns the actors who were in it just as readily).

Fandom runs MediaWiki, so entries are built from raw wikitext through each wiki's public
`api.php` — no HTML scraping, no API key, no AI. That structure is what makes the filtering
reliable rather than best-effort:

| Kept | Dropped |
|------|---------|
| Infobox facts — species, age, status, occupation, affiliation, aliases, kanji/rōmaji, ruler, region… | Images, galleries, captions, coats of arms, maps |
| Appearance, Personality, Abilities & Powers, Techniques | **First Appearance** — debut chapter/episode/volume |
| Description, History, Background, Relationships | **Portrayal** — voice actors, cast, live-action, dub credits |
| Plot, Synopsis, Story arcs | **Trivia**, **References**, notes, external links, see also |
| Members, Staff, Inhabitants, roster grids | Merchandise, toys, figures, video games, other media |
| **Synopsis / History tabs** — the character's story, arc by arc | **Image Gallery / Trivia / Quotes tabs** |
| Geography, Culture, Rules, Terminology | Publication data, release dates, reception, awards, popularity polls |

Wiki markup is resolved rather than deleted, so links keep their display text, tables holding
real lore (technique descriptions, member lists) become readable lines, and reference markers
disappear without taking the sentence around them.

#### The Synopsis tab

Long articles are usually split across tabs rather than left to grow to 100 KB.
"Satoru Gojo" is 35 KB of what he is *like* — appearance, personality, abilities —
while "Satoru Gojo/Synopsis" is 69 KB of what *happened* to him, and for roleplay
that is the better half:

```
Profile | Synopsis | Image Gallery      ← the wiki's own tab bar
   ↑        ↑
   kept     kept          Image Gallery, Trivia and Quotes tabs are not
```

**Wikis name this tab whatever they like**, which is exactly why the tab bar is
read rather than a fixed name looked for:

| Wiki | Main tab | Story tab | Also kept |
|------|----------|-----------|-----------|
| Jujutsu Kaisen | Profile | **Synopsis** | — |
| Kaiju No. 8 | Info | **Plot** | Relationships |

Anything that is not a known presentation tab — Image Gallery, Trivia, Quotes,
and the wiki's own word for the main page — is followed. A tab counts as *story*
(and so is what the arc chooser filters) when it is called Plot, Synopsis,
Story, History, Chronology, Biography or similar; other tabs like Relationships
are kept whole, because they are lore rather than story and hiding them behind
an arc filter would be wrong. The story tab also gets a reserved share of the
budget, so a Relationships page of a dozen short sections cannot crowd out the
one arc you asked for.

**The wiki is asked which subpages exist**, rather than guessed at. One sweep of
the wiki's article titles — nine requests and under two seconds on a wiki the
size of My Hero Academia's, cached for the rest of the session — gives an exact
index of every subpage on it, so a batch of any size costs nothing further.

That replaced two guesses, both of which failed in practice. Reading the page's
own `{{Tabs}}` template only works where the wiki passes the tab names as
parameters: the My Hero Academia wiki writes `{{Tabs/Active}}`, which names
none, so **every synopsis on that wiki was invisible to a bulk import** — Koichi
Haimawari's 142 KB of story included. Speculating `<Title>/Synopsis` only ever
finds the suffixes somebody thought to list. Asking finds whatever the wiki
actually calls them.

Wikis too large to index — past `MAX_INDEX_ARTICLES`, checked with a single
request before any sweep starts — fall back to the tab template and then to the
speculative suffixes, which ride along in the same batched requests.

#### Pick the arcs, not the whole story

A synopsis is one section per story arc, and Yuji Itadori's runs to **136,000
characters** across ten of them. No lorebook entry can hold that, and most of it
is the wrong part: a *Modulo* lorebook does not want his Fearsome Womb Arc.

So arcs stay separate and selectable. Tick some pages in the picker and the arcs
found across their Synopsis tabs appear as chips:

```
📖 12 story arcs in the selected pages' Synopsis tabs — untick the parts this
   lorebook is not about, and the rest get the room instead.

☐ History 15k   ☑ Modulo 38k   ☐ Shinjuku Showdown Arc 117k
☐ Shibuya Incident Arc 52k     ☐ Culling Game Arc 120k   ☐ Fearsome Womb Arc 23k …
```

Each chip shows how much prose that arc holds across the pages you picked, so
the expensive ones are obvious. **When the import is already scoped to a
sub-series, its arc is ticked and the rest are not** — narrowing to *Modulo*
narrows the synopses to Modulo too, which is what scoping was for.

Keeping arcs separate is also what makes them readable. Merged into one block
they share a single section's ceiling, and 136,000 characters come out as 1,500;
narrowed to one arc, that arc gets a real share of the budget:

| Yuji Itadori's Modulo arc | Characters kept |
|---------------------------|-----------------|
| All ten arcs merged (before) | 242 |
| All ten arcs, separate | 242 |
| **Only *Modulo* ticked** | **3,201** |

The same arc is not spelled the same on every page — Yuji's synopsis heads it
"Modulo" and Yuka's heads it "Jujutsu Kaisen Modulo" — so both fold into one
chip, and ticking it matches either. Headings that appear on one page only and
hold a couple of lines ("Members", "Base of Operations") are that page's own
layout rather than part of the story, and are not offered.

Subpages get their own share of the budget rather than competing with the main
page, so an entry with a synopsis is *bigger* than one without rather than the
same size with less in it. The checkbox turns it off when a book is getting too
large.

**Detail per entry** sets how much prose each entry gets — Compact, Standard or Detailed. The
budget is shared out by how useful a section is for roleplay, so a 40-part story arc is
summarised across its whole length instead of spending everything on chapter one. Wikis that
transclude their infobox rather than inlining it (the One Piece wiki, for example) fall back to
reading Fandom's rendered portable infobox.

#### Which story first

"Characters" is the wrong first question on a wiki that documents a franchise.
The DC Universe wiki holds ten films and eleven series, so asking for its
characters returns everybody from Superman to Peacemaker in one list — and
nobody is building that lorebook.

So a wiki link opens on the question the wiki can answer itself:

```
What is this lorebook about?          [ Everything on this wiki → ]
DC Universe Wiki covers 23 separate stories.

  Movies 9   TV Series 11   Comics 1   Books 2
  ┌─────────┐ ┌─────────┐ ┌─────────┐
  │ [poster] │ │ [poster] │ │ [poster] │
  │ Supergirl│ │ Superman │ │ Clayface │
  │ USE THIS→│ │ USE THIS→│ │ USE THIS→│
  └─────────┘ └─────────┘ └─────────┘
```

Picking one hands straight to whichever scoping the wiki supports: a film lists
its own cast, so *Supergirl* gives 18 characters, 25 locations and 13 events;
a spin-off lists nothing of the sort, so *My Hero Academia: Vigilantes* is
derived from its own 80 chapters instead. Either way everything after step 1 is
scoped to the answer, and **`← Pick a different story`** goes back.

The works come from the wiki's own filing, found by **what a category ends in**
rather than by its whole name. A fixed list of names was not enough: the Marvel
Cinematic Universe wiki has no category called "Movies" at all — its 47 films
are spread across `Released Movies`, `Upcoming Movies` and one per phase — so a
wiki with nine kinds of work offered three. Reading the ending finds whatever a
wiki calls its shelves, and every shelf of a kind is merged into one tab, with
the rest of the category name kept as the label on the card ("Phase One").

Candidates are then verified by infobox, because categories alone cannot tell a
story from the person who wrote it: the Jujutsu Kaisen wiki files both Gege
Akutami and *Jujutsu Kaisen Modulo* under nothing but "Manga". A work
transcludes a template naming it a series, film or game; an author does not.
That check reads **template names only** — a Marvel film article is 100 KB of
wikitext and its template list is a few hundred bytes.

Opening a work does **not** add it as an entry — you asked who is in the film,
not for a page about the film. And the wiki's categories are not indexed at
all unless you take `Everything on this wiki`, which is what dropped step 1
from fourteen seconds to two.

A wiki documenting a single story has no question to ask, and opens straight
at its categories.


#### A season that is its own story

American Horror Story tells a self-contained story per season — different cast,
different setting, only the title in common — and files them accordingly:

```
Category:Stories
  American Horror Story/Murder House
  American Horror Story/Coven          ← subpages, not "Season 1"
```

Four things had to give way before those appeared. There is no `Seasons`
category on that wiki, so `Stories` is now a shelf (plural only — "American
Horror Story" is the wiki's own subject, not a shelf). `{{Infobox/Story}}`
now marks a work. A work may be a **subpage** of the franchise it belongs to,
where before any title with a slash was rejected — though an article's own tabs
(`/Synopsis`, `/Gallery`) still never are. And the franchise name is stripped
off a subpage to find the season's own, so `Category:Murder House` is found.

The tile, the picker heading and the scope banner all say **Murder House**,
not "American Horror Story/Murder House": the franchise is the wiki, and
repeating it in every heading says nothing.


#### Stories kept in template subpages

The My Hero Academia wiki keeps each character's synopsis in template
subpages the tab merely includes:

```
==Synopsis==
{{Template:Izuku Synopsis/UA Beginnings}}    ← 118 KB
{{Template:Izuku Synopsis/Rise of Villains}} ← 213 KB
```

Templates are stripped when wikitext is cleaned, which is right for
formatting helpers and wrong for these, so Izuku Midoriya's synopsis came out
as **four headings and no story**. Content transclusions are now pulled in
first and read as part of the page — **1 arc became 27**, and 336 KB of
synopsis appeared where there had been none. A template with parameters is a
formatting helper and left alone; `{{Tabs/Active}}` and its kind are matched
by family and skipped.

#### Sections nobody has written yet

Solo Leveling heads twenty arcs on each character page and puts "Coming soon!"
under fourteen of them. Offering an arc that holds twelve characters of that
is worse than not offering it, so placeholder text (`TBA`, `To be added`,
`N/A`, `WIP`) counts as empty and those arcs do not appear. Its six written
arcs do — and a *named* arc is never dropped for being short, which is what
used to lose fourteen more.


#### When the cast is not on the page

Some wikis put nothing useful on a work's own page. The Disney wiki is the
extreme: `Frozen` is 35 KB of plot with no cast list anywhere on it, and
`Moana` is a 585-byte disambiguation page with no infobox at all. Both looked
like dead ends.

What that wiki does instead is file everything in categories named after the
work — `Frozen characters`, `Frozen locations`, `Frozen objects` — which says
the same thing more exactly. Those are read now, and the ones named after a
work that hold something *else* (`Frozen people` is the crew, `Frozen songs`,
`Frozen galleries`) are left alone:

| Film | Characters | Locations | Items |
|------|-----------:|----------:|------:|
| Frozen | 39 | 13 | 6 |
| The Incredibles | 45 | 7 | — |
| Aladdin | 141 | 18 | 15 |
| Zootopia | 88 | 22 | 6 |

This runs for any page that produced nothing else, so it also rescues pages
whose infobox says nothing — if a wiki keeps "Moana characters", then Moana is
a story whatever its own page looks like.

#### Wikis too big to list

The Disney wiki documents **2,607 films**. No grid shows that, and the forty it
can show begin at "10 Things I Hate About You" — so step 1 says what it is
doing and gives you a search box instead:

```
Find a story by name…            [ Frozen                        ]
Showing 40 of 2,607 — search above for a particular one.
```

Typing filters what is on screen; pressing Enter searches the whole wiki and
returns only its *works* — verified the same way, so a search for "Moana" gives
the film and its sequels rather than Maui and Te Fiti.


#### A season is its episodes

Picking a TV season used to give a cast and nothing else — the Invincible
wiki's "Season 1" page names four leads and no place, faction or event, so the
lorebook was people standing in a void.

Two things were wrong. A cast list is split across headings (`Main`,
`Supporting`, `Guest`) and only the first was read, which is why four people
arrived instead of thirty-three. And the rest of the world is not on the season
page at all — it is in the episodes, which the season already links in its own
episode table. Reading those the way a spin-off's chapters are read turns
**4 characters** into **78 characters, 16 locations, 8 organisations, 2 events**
and a species, each ranked by how much of the season it appears in.

The billed cast is never demoted by that counting: anyone the page names
outright is core whether they appear in two episodes or eight.

#### Arcs inside arcs

A story is not a flat list of arcs. Blue Lock nests matches inside arcs inside
a plot, and is not even consistent about the depth — later arcs sit at level 2
beside the plot rather than under it:

```
== Plot ==
=== First Selection Arc ===
==== Team X vs Team Z ====
== Second Selection Arc ==      ← same kind of thing, two levels up
```

So depth cannot decide what an arc is; the wiki's own naming does, at whatever
level it sits. Every heading becomes a node carrying the arcs enclosing it, and
the chooser nests them:

```
☑ Introduction Arc  15k    ▾ ☑ First Selection Arc  86k
                             ↳ ☑ Team V vs Team Z  49k
                             ↳ ☑ Team X vs Team Z   8k
```

Ticking the arc takes its matches with it; opening it and ticking one match
takes only that. And because the chips are gathered across every selected page,
**one choice applies to the whole import** — "Team X vs Team Z" gives Isagi's
account of the match and Bachira's, each from their own page:

| Picked | Isagi | Bachira |
|--------|------:|--------:|
| everything | 6 arcs | 8 arcs |
| First Selection Arc | its 4 matches | its 4 matches |
| Team X vs Team Z | 4,176 chars | 1,668 chars |

Having picked nothing the tree folds back to its arcs — forty headings holding a
paragraph each is not an entry anybody wants to read. Folding by depth would be
wrong here (`Plot` is scaffolding two levels above the matches, and everything
would merge into one 68 KB block), so a heading that holds only other arcs
steps aside while one holding scenes keeps them.


#### Plot that is written in scenes

A character's History tab is often one heading per season with the story told
in subsections beneath it:

```
== Season 1 ==            ← no prose of its own
=== The Birth of Invincible ===
=== Flaxan Invasion ===   ← the season is these
```

Empty headings are normally scaffolding and get dropped — but dropping this one
left its scenes nothing to fold into, and 111 KB of history came out as a
single nameless block. An empty heading **at the arc level** is now kept as the
container it is, so the arc chooser offers `Season 1 · 140k` and picking a
season gives that season's plot. Choosing "Season 1" in step 1 ticks the
matching arc automatically and unticks the other seventy-seven.


#### One page, one home

Wikis file the same character half a dozen ways. Yuta Okkotsu is listed under
Characters, Jujutsu Sorcerers, Culling Game Players, Characters by Occupation,
Characters by Affiliation *and* Gojo Family — so the Jujutsu Kaisen wiki's 349
real pages arrived as **663 listings across eighteen chips**. Ticking two of
them imported the same person twice and no chip meant what it said.

Each page is now claimed by exactly one group, in lore order (a person is a
Character before they are a Culling Game Player). A category left with almost
nothing of its own was never a separate body of content — it is a way of
slicing one — so it comes back as a sub-filter of the group that took its
pages, where it still does its job:

| Wiki | Before | After |
|------|--------|-------|
| Jujutsu Kaisen | 18 chips, 663 listings | **7 chips, 320 pages, 0 repeats** |
| My Hero Academia | — | **11 chips, 677 pages, 0 repeats** |

Nothing is lost on the way: the few pages a demoted category held alone are
moved into its host rather than disappearing with the chip.

#### Tiles, because a name is not a face

A pick-list of three hundred names is unusable if you do not already know the
cast — "Rapt Tokage" means nothing until you see him. Every article has a lead
image (the picture above the infobox on Yuji Itadori's page) and asking for it
costs **one request per fifty pages**, so the picker shows it:

```
┌──────────┐  ┌──────────┐  ┌──────────┐      ● core cast
│ ●     ✓ │  │        │  │ ●      │      ✓ picked
│ [picture]│  │ [picture]│  │ [picture]│
│Koichi H. │  │Kazuho H. │  │Iwao Oguro│
└──────────┘  └──────────┘  └──────────┘
```

Pictures come **through the app** (`/img`) rather than straight from Fandom's
CDN: a page is not always allowed to load third-party images, and this way
browsing a wiki does not have your browser talking to a CDN once per tile.
Only Fandom and Wikimedia hosts are fetched, over HTTPS. A wiki's own
"no picture available" graphic is treated as no picture, so those tiles fall
back to initials rather than showing forty identical placeholders. `≡ List`
switches back to the dense name list, and the choice is remembered.

The same picture becomes the entry's thumbnail in the Maker, so a book of
forty entries reads as faces rather than a wall of text.


#### Relationships become their own entries

A well-kept wiki writes a paragraph on what a character is to each person they
know, and that is the most directly useful thing on the page for roleplay. It
is also the worst served by being folded into the character entry, where a
dozen short sections compete with the story and all of them lose — Koichi
Haimawari's twelve relationships came out at about 200 characters each.

**+ Relationship entries** splits that tab into an entry of its own. It is
keyed on *both* people — the subject and everyone described — so it comes into
context exactly when the model needs to know how the two of them stand, rather
than every time the character is mentioned:

```
[Koichi Haimawari — Relationships]
keys: Koichi Haimawari, The Crawler, Shoko Haimawari, Kazuho Haneyama,
      Knuckleduster, Eraser Head, Ingenium, Captain Celebrity, All Might,
      Makoto Tsukauchi, Soga Kugisaki, Rapt Tokage, Moyuru Tochi, …
```

#### The names people actually type

An infobox rarely lists what a character is *called*. The wiki's redirects do,
and they are one request per forty pages:

| Page | From the infobox | Added by redirects |
|------|------------------|--------------------|
| Iwao Oguro | Iwao Oguro | **Knuckleduster**, **O'Clock** |
| Kazuho Haneyama | Kazuho Haneyama | **Pop Step**, **Pop☆Step** |
| Number 6 | Number 6 | **Scarred Man**, **No. 6**, **O'Clock II** |

Without them an entry never fires on the name the story actually uses. Bare
common nouns are still dropped — a wiki points "Rock" at Number 6, and as a
trigger that fires on any mention of a rock.

#### The budget follows what matters

Scope already works out who carries a story and who walks through one chapter
of it, so entries are not all given the same room. A lead gets the whole detail
budget, supporting cast about two thirds, a walk-on about a third — never less
than `MIN_TIER_BUDGET`, so no entry is reduced to nothing. On a six-character
import that is roughly a **28% smaller book with the protagonist untouched**.


### Continuing a lorebook you already made

A lorebook is never finished in one sitting — a new season airs, a spin-off starts,
or you simply want the book you shipped last month to gain a few entries. Drop it
onto the Maker (or paste its path, or use **⚒ Add entries** in the Editor) and it
becomes the base everything else is added to:

```
📖 My JJK Lorebook          4 existing · +41 new · 2 refreshed
```

Two things matter here, and both are the kind of thing that is only noticed when
it goes wrong:

**Nothing of yours is touched.** Entries are carried through as they are, not
rebuilt from a schema — so fields this app has never heard of, whether from a
newer SillyTavern or another tool entirely, survive by being left alone. The
book's own `scan_depth`, `token_budget` and extensions come back out as they went
in.

**Re-scraping refreshes rather than duplicates.** Scrape a page the book already
has and it updates in place:

| Taken from the fresh scrape | Kept exactly as you set it |
|-----------------------------|----------------------------|
| Content, trigger keys, title, source metadata | Strategy, order, depth, position, probability, group, enabled/disabled |

That split is the whole point: the wiki is authoritative about *what the subject
is*, and knows nothing about *when the entry should fire*. So a book you spent an
evening tuning can be brought up to date without being re-tuned.

Matching is by page URL where the entry has one, and by name otherwise — which is
all a book written by hand or by another tool can offer. Both sides of a match are
labelled in the list (`🔄 will be refreshed` / `🔄 refreshes existing`) so a
character appearing twice on screen reads as intended rather than as a bug, and
the header count is the number of entries you will actually export.

**Clear Scraped Entries Only** throws away a batch import that went wrong while
keeping the loaded book — the usual thing to want at that moment.

### Then

4. Filter your collected entries by **category chips** or **text search**. The badge in the
   header tracks the lorebook's size in approximate tokens, and turns amber past ~40k — a cue
   to drop the detail level or split the book per story.
5. Export as SillyTavern JSON, preview it, or send it straight to the Editor.

All HTTP goes through pooled sessions with automatic retries on 429/5xx.

## ✎ Editor

**Strategy = the colored dot in Marinara Engine.** SillyTavern stores it as three booleans; the Editor maps them to named options:

| Strategy      | Dot | Fields set                        | Meaning |
|---------------|-----|-----------------------------------|---------|
| 🟢 Normal     | green  | `constant=false, selective=false` | Triggers when primary keys match the text |
| 🟡 Constant   | yellow | `constant=true`                   | Injected every time the lorebook is active |
| 🔴 Selective  | red    | `selective=true`                  | Primary keys must match with secondary-key logic |
| 🔵 Vectorized | blue   | `vectorized=true`                 | Retrieved by vector similarity |

### Features

- **Per-entry strategy dropdown** — change one entry inline, colored dot updates live.
- **Bulk strategy change** — select many (or "select all shown") and flip them to any of the four strategies in one click.
- **Powerful filters** — search with a **scope selector** (everywhere / title / keys / content) and optional **regex**; filter by group prefix, strategy, insertion **position**, and enabled/disabled; one-click filter reset.
- **Sortable columns** (title, strategy, order, depth, probability, size).
- **Selection tools** — select all shown, **invert selection**.
- **Undo** — every mutation (edits, bulk ops, deletes) is snapshotted; step back up to 30 changes.
- **Full entry editor** (drawer): title, keys, strategy, selective logic, order, depth, position, probability, group, content.
- **Bulk set field** and **bulk find & replace** (plain text or regex) across content / comment / keys.
- **Duplicate-key finder** — flags primary keys shared by multiple entries.
- **Stats bar** — counts per strategy, disabled count, rough token estimate.
- **Safe save** — *Download* a copy, or *Save in place* which writes a timestamped `.bak-YYYYMMDD-HHMMSS` backup next to the original first.
- **⚒ Add entries** — hands the open lorebook to the Maker to scrape more onto it, unsaved edits included, and comes back with everything intact.
- Load by drag & drop, file picker, or full path (`~` expansion supported on macOS/Linux).

Every field of the original lorebook is preserved on save — the editor only touches what you change.

## Project layout

```
app.py               Flask app — Editor + Maker routes
core/
  classifier.py      Weighted keyword entity classifier (real-world + fictional)
  aggregator.py      Merges wiki/social data into one profile
  formatter.py       Profile → SillyTavern World Info JSON
  lorebook.py        Reads an existing lorebook back in and merges into it
scrapers/
  wikipedia.py       Wikipedia articles + list pages (wikitext, same filters)
  wiki.py            Wikidata claims + Wikipedia enrichment (pooled, retries)
  social.py          Social handle discovery + live stats
  fandom.py          Fandom MediaWiki API — pages, categories, bulk import
  scope.py           Sub-series scoping — one story out of a franchise wiki
  wikitext.py        Wikitext → clean prose; infobox + section noise filters
test_scope.py        Offline checks for the sub-series scoping rules
test_lorebook.py     Offline checks for loading and merging existing books
test_wikipedia.py    Offline checks for URLs, list parsing and article filters
test_fandom.py       Offline checks for tabbed subpages (Synopsis, History)
templates/
  maker.html         Maker UI
  editor.html        Editor UI
```

### Tuning the Fandom filters

Every rule about what counts as noise lives in plain regex lists at the top of
`scrapers/wikitext.py`, so a wiki with unusual section names is a one-line change:

| List | Controls |
|------|----------|
| `SECTION_BLOCKLIST` | Whole sections to drop (and everything nested under them) |
| `INFOBOX_BLOCKLIST` | Infobox rows to drop, matched on the exact field name |
| `INFOBOX_CONTAINS_BLOCKLIST` | Infobox rows to drop wherever a phrase appears in the name |
| `SECTION_CONTAINS_BLOCKLIST` | Sections dropped on a phrase anywhere in the heading ("Awards and nominations", "Selected filmography") |
| `SECTION_PRIORITY` | Which sections keep their length when an entry has to be trimmed |
| `TEMPLATE_DROP` | Templates removed whole, including Wikipedia article furniture |

And in `scrapers/wikipedia.py`:

| Setting | Controls |
|---------|----------|
| `_LIST_TITLE_RE` / `is_list_page` | What counts as a list page rather than a subject |
| `_SKIP_TARGET_RE` | Link namespaces never offered as entries |
| `_FACT_ORDER` | The order infobox rows read in on a biography |
| `_flatten` | How deep subsections are folded into their parent |

And in `scrapers/fandom.py`:

| Setting | Controls |
|---------|----------|
| `LORE_CATEGORIES` | Category names looked for by default, best first |
| `_NOISE_CATEGORY` | Categories never offered — maintenance, cast, chapter/episode indexes |
| `SPARSE_CATEGORY` | Below this many direct pages, a category is treated as a container and filled from its subcategories |
| `subpage_index` | Sweeps the wiki's titles once for an exact list of every subpage |
| `MAX_INDEX_ARTICLES` | Article count past which a wiki is too big to index, and guessing takes over |
| `SUBPAGE_SUFFIXES` | Tabs tried on those wikis, when the page declares none of its own |
| `_SUBPAGE_NOISE` | Tabs never followed — galleries, trivia, and the wiki's word for the main page |
| `_STORY_TAB` | Tabs whose sections are story arcs, and so are filterable |
| `STORY_TAB_SHARE` | Budget reserved for the story tab when a page has others |
| `SUBPAGE_BUDGET_SHARE` | How much extra budget a page's tabs are given |
| `MIN_ARC_CHARS` | Below this, a one-page heading is layout rather than an arc |
| `_ARC_TITLE` | Headings that name a part of a story — how the arc level is found |
| `_CAST_SECTION` | Every heading that is a kind of cast list |
| `story_sections` | The arc tree, each node carrying the arcs enclosing it |
| `collapse_sections` | …folded back to its arcs when nothing is picked |
| `path_matches` | Picking an arc takes its parts; picking a part takes one |
| `is_wiki_subject` | A work that *is* the wiki — scoping to it means all of it |
| `instalment_links` | The episodes a work's own page lists, and what it calls them |
| `work_content_categories` | `Frozen characters`, `Frozen locations` — what a wiki files under a work |
| `_WORK_CONTENT_SKIP` | …and the ones holding its crew, songs or artwork instead |
| `search_works` | Finding one story by name on a wiki too big to list |
| `expand_transclusions` | Pulls in a story kept in template subpages |
| `is_lorebook_page` | Rejects chapters, Blu-rays and merchandise as entries |
| `W.is_placeholder` | "Coming soon!" is an empty section, not a short one |
| `_NAV_TEMPLATE` | Navigation furniture named after a medium is not an infobox |
| `_TRUST_TEMPLATE_KIND` | Where a page's infobox outranks the shelf it sat on |
| `is_work_template` | Whether a template marks a work — `{{Cite book}} does not` |
| `build_work_scope` | What turns up across those instalments, ranked |
| `MIN_MEANINGFUL_CHARS` | Below this a heading is a label, not a section |
| `MIN_ARC_BUDGET` | Room every arc is guaranteed, so trimming never drops the ending |
| `arc_key` | How two spellings of the same arc are recognised as one |
| `_WORK_INFOBOX` | Infobox names that mark a page as indexing a story rather than describing one thing |
| `_RELATION_TAB` | The tab split into an entry of its own |
| `RELATION_BUDGET_SHARE` | How much of a character's budget their relationships entry gets |
| `TIER_BUDGET` | Share of the detail budget a lead, a supporting character and a walk-on each get |
| `fetch_redirects` | The other names a page answers to, harvested for trigger keys |
| `fetch_images` | The lead picture per page, for the picker tiles |
| `discover_works` | The wiki's own films, series, seasons and spin-offs — step 1 |
| `WORK_SUFFIXES` | What a category must end in to be a shelf of works, and what kind |
| `KIND_ORDER` | The order the step-1 tabs are offered in |
| `work_categories` | The wiki's shelves, swept once and cached |
| `MAX_LIST_CALLS` | Shelves opened in total; spent one per kind before any second |
| `_NOT_A_WORK` | Categories that disqualify a candidate — songs, authors, lists |
| `dedupe_groups` | Gives every page one home and demotes re-slices to filters |
| `_CLAIM_ORDER` | Which kind of category gets to claim a page first |
| `FACET_SHARE` | Overlap past which a category is a filter, not a category |
| `is_image_url` / `MAX_INDEX_ARTICLES` | What the `/img` proxy will fetch |

And in `scrapers/scope.py`:

| Setting | Controls |
|---------|----------|
| `_INSTALMENT_RE` | Titles treated as chapters/episodes — evidence for scope, never entries |
| `_INSTALMENT_CATEGORIES` | Categories that identify an instalment outright |
| `_META_ONLY_CATEGORIES` | Categories that are franchise bookkeeping rather than lore |
| `_PRODUCTION_FIELD` | Infobox rows naming the people who made the story, so they are excluded from its cast |
| `_DEBUT_FIELD` | Infobox rows naming who is new in an instalment — the strongest scope signal |
| `CORE_SHARE` | Share of a story's instalments something must appear in to count as core |
