"""
MediaWiki wikitext → clean prose, tuned for lorebook building.

Fandom pages are written in wikitext, which is far easier to filter reliably
than the rendered HTML: infobox fields are literal `|key = value` pairs and
section boundaries are literal `== Heading ==` lines. That means we can drop
noise (images, refs, nav boxes, "Trivia", "Voice Actors"…) *structurally*
rather than guessing from CSS classes.

Everything the roleplay AI would not benefit from is stripped:

  * images, galleries, thumbnails, captions, file links
  * reference markers, citation templates and <ref> blocks
  * navigation boxes, tab bars, category and interwiki links
  * real-world production trivia — cast, voice actors, merchandise,
    release dates, chapter/episode numbering, popularity polls

The blocklists below are plain regex lists, deliberately kept at the top of
the file so they are easy to extend for a wiki with unusual section names.
"""

import html
import re

# ══════════════════════════════════════════════════════════════════════════
# NOISE FILTERS — edit these to tune what gets kept
# ══════════════════════════════════════════════════════════════════════════

# ── Sections dropped entirely (matched against the lower-cased heading) ────
# A blocked heading also drops every sub-heading nested beneath it.
SECTION_BLOCKLIST = [
    # meta / citations
    r"trivia", r"references?", r"reference list", r"notes?", r"footnotes?",
    r"sources?", r"citations?", r"bibliography", r"see also",
    r"external links?", r"links?", r"further reading",
    # navigation & images
    r"navigation", r"site navigation", r"navigation boxes?", r"categories",
    r"(image|photo|screenshot|art)? ?gallery", r"galleries", r"images?",
    r"screenshots?", r"artwork", r"fan ?art", r"wallpapers?",
    # real-world production
    r"cast", r"voice cast", r"voice actors?", r"voice actresses?",
    r"credits?", r"portrayals?", r"actors?", r"dub", r"dubbing",
    r"localizations?", r"localisations?", r"behind the scenes",
    r"production", r"development", r"concept art", r"staff credits",
    r"authors?", r"creators?", r"illustrators?",
    # merchandise & other media
    r"merchandise", r"merch", r"toys?", r"figures?", r"figurines?",
    r"products?", r"collectib(le|ilia)s?", r"goods",
    r"video ?games?", r"games?", r"game appearances?", r"apps?",
    r"media", r"in other media", r"other media", r"media appearances?",
    r"adaptations?", r"anime adaptation", r"live[ -]action",
    r"crossovers?", r"cameos?",
    # publication / release meta
    r"publications?", r"publication history", r"releases?", r"release dates?",
    r"reception", r"ratings?", r"reviews?", r"sales", r"awards?",
    r"nominations?", r"promotional videos?", r"promotions?",
    r"advertisements?", r"trailers?", r"commercials?", r"marketing",
    r"soundtracks?", r"music", r"theme songs?", r"openings?", r"endings?",
    r"songs?", r"discography",
    # numbering / indexes  (note: "appearance" singular is KEPT — it is the
    # physical description, which is exactly what roleplay needs)
    r"appearances", r"list of appearances", r"appearances in .*",
    # Any "List of …" heading: an index of the thing, not the thing. Blue
    # Lock's synopsis ends with a "List of Matches" table of links.
    r"lists? of .*", r"index of .*",
    # Qualified appearance indexes — "Chapter Appearances", "Anime
    # Appearances", "Vigilantes Chapter Appearances". These are tables of
    # instalment numbers, so they clean down to a dozen characters of nothing
    # and still cost the entry a heading. The singular "Appearance" is a
    # physical description and stays.
    r".+ appearances",
    r"episodes?", r"episode list", r"chapters?", r"chapter list",
    r"volumes?", r"issues?", r"arcs list", r"timeline of episodes",
    r"popularity polls?", r"polls?", r"rankings?", r"statistics",
    # housekeeping
    r"errors?", r"goofs?", r"bloopers?", r"continuity", r"differences?",
    r"manga and anime differences", r"to ?do", r"stub", r"contents",
]

# ── Infobox rows dropped (matched against the lower-cased field name) ──────
INFOBOX_BLOCKLIST = [
    # images
    r"image\d*", r"images", r"img\d*", r"picture\d*", r"photo\d*", r"icon",
    r"logo", r"cover", r"caption\d*", r"image ?caption", r"image ?size",
    r"image ?width", r"imagewidth", r"gallery", r"screenshot", r"portrait",
    r"sprite", r"thumbnail", r"pixels?", r"box ?image", r"title ?image",
    r"colou?r ?scheme", r"text ?colou?r", r"background",
    # "First Appearance" block
    r"debut\w*", r"\w* ?debut", r"first appearance", r"firstappearance",
    r"first seen", r"first appeared", r"last appearance",
    r"latest appearance", r"final appearance", r"appears in", r"appearances",
    r"manga", r"anime", r"chapters?", r"episodes?", r"volumes?", r"seasons?",
    r"novel", r"movie", r"film", r"ova", r"ona", r"special",
    # "Portrayal" / cast block
    r"voices?", r"voice actors?", r"voiced by", r"\w* ?voice",
    r"voice ?actors?", r"seiyuus?", r"vas?", r"\w* ?va",
    r"actors?", r"actresses?", r"portrayed by", r"portrayals?", r"cast",
    r"live ?action", r"dub", r"performers?", r"model",
    # publication / product meta
    r"isbn", r"run", r"released", r"release ?dates?", r"cost", r"price",
    r"publishers?", r"magazines?", r"serializations?", r"air ?dates?",
    r"networks?", r"studios?", r"directors?", r"producers?", r"writers?",
    r"authors?", r"artists?", r"illustrators?", r"composers?",
    r"runtime", r"pages", r"copyright", r"rating",
    # Film / episode production credits
    r"cinematographers?", r"editors?", r"distributors?", r"screenplay",
    r"story by", r"based on", r"box ?office", r"budget", r"gross",
    r"releases?", r"filming", r"channels?", r"aired", r"airing",
    r"premieres?", r"finale", r"music by", r"score", r"languages?",
    # "Which media did this character turn up in" trackers — the Marvel
    # Cinematic Universe wiki lists One-Shots, Web Series, Docuseries…
    r"one[- ]?shots?", r"web ?series", r"docu[- ]?series", r"mini[- ]?series",
    r"series", r"films?", r"movies?", r"shows?", r"tv shows?", r"podcasts?",
    r"comics?", r"books?", r"video ?games?", r"attractions?",
    # Wikipedia biography infoboxes: rows that are really an index of the
    # subject's output or trophies. "Awards = Full list" is a link and nothing
    # more, and "Works" is a discography in disguise.
    r"television", r"radio", r"works", r"notable works?", r"discography",
    r"awards?", r"honou?rs?", r"medals?", r"nominations?",
    r"signature", r"module\d*", r"embed", r"module ?\w*",
    r"website", r"url", r"homepage",
]

# Phrases that make a field production metadata wherever they appear in its
# name — "Live-Action Portrayal" and "Japanese Voice Actor" are neither an
# exact "portrayal" nor an exact "voice".
INFOBOX_CONTAINS_BLOCKLIST = [
    r"voice", r"portray", r"live[ -]?action", r"debut", r"first appearance",
    r"seiyuu?", r"\bcast\b", r"\bactor\b", r"\bactress\b", r"\bdub\b",
    r"image", r"caption", r"gallery", r"screenshot", r"\bisbn\b",
]

# Phrases that make a whole section an index wherever they appear in its
# heading. Wikipedia headings are compound in a way Fandom's rarely are —
# "Awards and nominations", "Selected filmography", "Music videography" — and
# none of them survive an exact match against the list above.
SECTION_CONTAINS_BLOCKLIST = [
    r"filmograph", r"discograph", r"videograph", r"bibliograph", r"ludograph",
    r"awards? and (nomination|honou?r|accolade)", r"list of awards",
    r"nominations? and awards?", r"accolades",
    r"concert tours?", r"tour dates", r"chart (performance|positions?)",
    r"track listing", r"release history", r"credits and personnel",
    r"published works", r"selected works", r"works? cited",
    r"see also", r"external links?", r"further reading",
]

_SECTION_BLOCK_RE = re.compile(
    r"^(?:%s)$" % "|".join(SECTION_BLOCKLIST), re.I)
_SECTION_CONTAINS_RE = re.compile(
    "|".join(SECTION_CONTAINS_BLOCKLIST), re.I)
_INFOBOX_BLOCK_RE = re.compile(
    r"^(?:%s)$" % "|".join(INFOBOX_BLOCKLIST), re.I)
_INFOBOX_CONTAINS_RE = re.compile(
    "|".join(INFOBOX_CONTAINS_BLOCKLIST), re.I)

# ── Section priority — used when an entry has to be trimmed to fit ─────────
# Lower number = kept longer. Tier 3 is everything unmatched. Patterns match
# anywhere in the heading, so "Anti-Domain Techniques" ranks with "Abilities".
SECTION_PRIORITY = [
    (1, r"appearance|physical description|description|overview|profile"
        r"|summary|personality|traits|biography|bio|about"),
    (1, r"abilities|powers|skills|techniques?|combat|arsenal|equipment"
        r"|weapons?|forms?|transformations?|stats"),
    # A real person's career and artistry are the equivalent of a character's
    # abilities — what they are actually known for doing.
    (1, r"career|artistry|musical style|artistic style|influences"
        r"|public image|persona|playing style|writing style"),
    (2, r"history|background|backstory|past|origins?|creation|early life"),
    (2, r"personal life|legal (issues|troubles|problems)|controvers\w*"
        r"|philanthropy|business ventures?|feuds?|activism|political views"),
    (2, r"relationships?|allies|enemies|rivals|family|connections"),
    (2, r"plot|story|synopsis|journey|role|involvement"),
    (2, r"members|staff|students|inhabitants|residents|roster"
        r"|population|ranks?|hierarchy|organi[sz]ations?"),
    (2, r"geography|locations?|layout|landmarks?|setting|environment"
        r"|climate|culture|society|government|laws?|rules?|regulations?"
        r"|curriculum|uniforms?|terminology|glossary"),
]
_PRIORITY_RES = [(t, re.compile(r"\b(?:%s)\b" % p, re.I))
                 for t, p in SECTION_PRIORITY]

# ══════════════════════════════════════════════════════════════════════════
# TEMPLATE HANDLING
# ══════════════════════════════════════════════════════════════════════════

# Templates whose Nth unnamed parameter is the visible text.
TEMPLATE_TEXT_PARAM = {
    "nihongo": 0, "nihongo foot": 0, "nihongo2": 0, "nihongo3": 0,
    "ruby": 0, "furigana": 0, "sub": 0, "sup": 0, "small": 0, "big": 0,
    "nowrap": 0, "nobr": 0, "italic": 0, "italics": 0, "bold": 0,
    "sic": 0, "abbr": 0, "tooltip": 0, "pov": 0, "nihongo title": 0,
    "lang": 1, "transl": 1, "translation": 1, "nihongo krt": 0,
}

# Templates that are pure wrappers — keep every unnamed parameter.
TEMPLATE_UNWRAP_ALL = {
    "scroll", "scroll box", "scrollbox", "center", "centre", "indent",
    "poem", "plainlist", "collapsible", "collapse", "hidden", "spoiler",
}

# Quotes carry the line, then the speaker, then styling junk. Joining every
# parameter turns a good character quote into "…now! Iron Man and Spider-Man 1".
TEMPLATE_QUOTE = {"quote", "quotes", "quotation", "quotations", "cquote",
                  "quotebox", "dialogue", "quote2", "qt", "epigraph"}

# Named parameters that carry the visible label of an otherwise unknown
# template — Fandom roster grids ({{CharPortal|image=…|name=…}}) are built
# from these, and the names in them are exactly the lore we want.
TEMPLATE_NAME_PARAMS = ("name", "articlename", "article", "title", "label",
                        "text", "content", "char", "character")

# Presentation parameters — never content, whatever template they appear on.
TEMPLATE_STYLE_PARAMS = {
    "width", "height", "size", "color", "colour", "bg", "background",
    "bgcolor", "align", "valign", "halign", "style", "class", "id", "border",
    "padding", "margin", "float", "cellspacing", "cellpadding", "font",
    "image", "img", "file", "icon", "logo", "crop", "pixels", "px",
    "position", "wrap", "state", "collapsed", "sortable", "ref", "refs",
}

# Templates dropped whole (exact, case-insensitive, underscores normalised).
TEMPLATE_DROP = {
    "ref", "refs", "reflist", "references", "cite", "citation",
    "citation needed", "cn", "fact", "verify", "source",
    "main", "main article", "see also", "seealso", "for", "about",
    "redirect", "disambig", "disambiguation", "hatnote",
    "stub", "expand", "cleanup", "delete", "notice", "ambox", "warning",
    "spoiler alert", "spoilers", "construction", "wip",
    "clear", "clr", "br", "-", "toc", "notoc", "shortcut", "portal",
    # Skin and layout switches. Their one argument is a CSS class name, so
    # left in they open an article with a line reading "affiliation-pro_heroes".
    "pagetheme", "page theme", "theme", "pagestyle", "page style", "css",
    "bgcolor", "background", "titlecolor", "colorscheme", "color scheme",
    "tabs", "tabber", "tab", "countdown", "welcome", "wiki welcome",
    "featured", "affiliates", "discordwidget", "poll", "listen", "video",
    "youtube", "external media", "imagemap", "click", "gallery",
    # Wikipedia article furniture. These sit above the first paragraph and
    # carry no prose, but their parameters are words — left in, a biography
    # opens with "yes / September 2024 / March 2025" from the date-format and
    # page-protection notices.
    "short description", "shortdescription", "use dmy dates", "use mdy dates",
    "use american english", "use british english", "use indian english",
    "use canadian english", "use australian english", "use oxford spelling",
    "engvar", "italic title", "italictitle", "displaytitle", "lowercase title",
    "pp", "pp-protected", "pp-semi-indef", "pp-move-indef", "pp-vandalism",
    "pp-blp", "pp-dispute", "protection", "good article", "featured article",
    "multiple issues", "more citations needed", "update", "refimprove",
    "unreferenced", "original research", "peacock", "weasel", "tone",
    "primary sources", "self-published", "cleanup rewrite", "copy edit",
    "very long", "too many photos", "over-quotation", "prose",
    "sfn", "efn", "refn", "harvnb", "rp", "r", "sfnp", "cite web",
    "cite news", "cite book", "cite magazine", "cite journal", "cite av media",
    "authority control", "subject bar", "commons category", "sister project",
    "dynamic list", "compact toc", "toc limit", "columns-list", "div col",
    "div col end", "reflist-talk", "blp sources", "bots", "nobots",
    "coord", "coords", "geobox coor", "sisterlinks", "wikiquote", "wiktionary",
}

# Template families whose subpages are all citations: {{Ref/Vigilantes}},
# {{Cite/Web}}, {{Note/Manga}}. Matched on the part before the slash.
_CITATION_TEMPLATES = {"ref", "refs", "reference", "references", "cite",
                       "citation", "note", "notes", "source", "sources"}


# Any template whose name contains one of these is dropped.
TEMPLATE_DROP_SUBSTR = ("navi", "navbox", "navbar", "infobox", "sidebar",
                        "footer", "header bar", "template:", "char box",
                        "image", "gallery")

_MAX_DEPTH = 12


def _scan_balanced(text, start, open_tok, close_tok):
    """
    Return (inner_text, index_after_close) for the balanced pair beginning at
    `start`, or (None, start) when the markup is unbalanced.
    """
    depth = 0
    i = start
    n = len(text)
    o_len, c_len = len(open_tok), len(close_tok)
    while i < n:
        if text.startswith(open_tok, i):
            depth += 1
            i += o_len
        elif text.startswith(close_tok, i):
            depth -= 1
            i += c_len
            if depth == 0:
                return text[start + o_len:i - c_len], i
        else:
            i += 1
    return None, start


def split_params(body):
    """
    Split a template body on top-level pipes, ignoring pipes nested inside
    other templates, links or tables.
    """
    parts, buf = [], []
    depth = 0
    i, n = 0, len(body)
    while i < n:
        two = body[i:i + 2]
        if two in ("{{", "[["):
            depth += 1
            buf.append(two)
            i += 2
        elif two in ("}}", "]]"):
            depth -= 1
            buf.append(two)
            i += 2
        elif two == "{|":
            depth += 1
            buf.append(two)
            i += 2
        elif two == "|}":
            depth -= 1
            buf.append(two)
            i += 2
        elif body[i] == "|" and depth <= 0:
            parts.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(body[i])
            i += 1
    parts.append("".join(buf))
    return parts


def _template_args(parts):
    """Split template parameters into positional args and named kwargs."""
    args, kwargs = [], {}
    for part in parts:
        m = re.match(r"^\s*([A-Za-z0-9 _()\-]{1,40}?)\s*=\s*(.*)$", part, re.S)
        if m:
            kwargs[m.group(1).strip().lower().replace("_", " ")] = m.group(2)
        else:
            args.append(part)
    return args, kwargs


def _render_template(body, depth):
    """Reduce one template invocation to the text a reader would see."""
    parts = split_params(body)
    raw_name = parts[0].strip()

    # Magic words such as {{DISPLAYTITLE:...}} / {{DEFAULTSORT:...}}
    head = raw_name.split(":", 1)[0]
    if ":" in raw_name and head.isupper() and head.isalpha():
        return ""

    name = re.sub(r"\s+", " ", raw_name.replace("_", " ")).strip().lower()
    if not name or name in TEMPLATE_DROP:
        return ""
    if any(s in name for s in TEMPLATE_DROP_SUBSTR):
        return ""
    # Per-wiki citation templates — {{Qref}}, {{Sref}}, {{ChapRef}} — carry
    # note text that must not end up mid-sentence in the prose. Wikis also
    # subpage them per source ({{Ref/Vigilantes|chap=125}}), which ends with
    # the source's name rather than "ref" and used to leave the chapter number
    # welded to the last word: "…becomes The Skycrawler.125".
    if name.endswith("ref") or name.split("/", 1)[0] in _CITATION_TEMPLATES:
        return ""

    args, kwargs = _template_args(parts[1:])

    if name in TEMPLATE_QUOTE:
        said = re.sub(r"\s+", " ", clean(args[0], depth + 1)) if args else ""
        if not said:
            return ""
        speaker = clean(args[1], depth + 1).strip() if len(args) > 1 else ""
        said = said.strip().strip('"')
        return f'"{said}"' + (f" — {speaker}" if speaker else "")

    if name in TEMPLATE_UNWRAP_ALL:
        rendered = [clean(a, depth + 1) for a in args if a.strip()]
        return " ".join(r for r in rendered if r)

    idx = TEMPLATE_TEXT_PARAM.get(name)
    if idx is not None and len(args) > idx:
        return clean(args[idx], depth + 1)

    # Unknown template — every wiki has dozens of small formatting helpers, so
    # recover their visible text rather than dropping real lore with them.
    # Positional image filenames are common in roster templates
    # ({{CLItem|Mo'at 3.png|Mo'at|…}}) and must never win.
    args = [a for a in args if not _FILE_VALUE_RE.match(a.strip())]
    if len(args) == 1:
        return clean(args[0], depth + 1)
    if len(args) > 1:
        # e.g. {{Color|White|'''Location'''}} — the payload is the long one.
        # Only unwrap when the payload reads like prose. Templates whose
        # arguments are all single tokens are usually formatting or
        # transliteration helpers ({{Chinese|安昂|Ān'áng}}), and taking one
        # would replace the character's actual name with it.
        longest = max(args, key=len)
        if " " in longest.strip():
            return clean(longest, depth + 1)
        return ""
    # Portrait-card templates (an image plus a name) build the roster grids on
    # clan and team pages — keep the name. The image requirement is what tells
    # them apart from citation templates, which also carry a `name=`.
    if any(k in kwargs for k in ("image", "img", "picture", "photo", "file")):
        for key in TEMPLATE_NAME_PARAMS:
            if kwargs.get(key, "").strip():
                return clean(kwargs[key], depth + 1)
        return ""

    # Otherwise a template built purely from named values is usually a small
    # data holder — {{Alias|primary=Tony Stark|codenames=Iron Man}} — and its
    # values are the content. Styling parameters are skipped.
    if kwargs:
        values = []
        for key, raw in kwargs.items():
            if key in TEMPLATE_STYLE_PARAMS:
                continue
            value = clean(raw, depth + 1)
            if value and value not in values:
                values.append(value)
        joined = ", ".join(values)
        if joined and len(joined) <= 400:
            return joined
    return ""


def _strip_templates(text, depth):
    out = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("{{", i):
            body, end = _scan_balanced(text, i, "{{", "}}")
            if body is None:
                out.append(text[i])
                i += 1
                continue
            out.append(_render_template(body, depth))
            i = end
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


# ══════════════════════════════════════════════════════════════════════════
# LINKS
# ══════════════════════════════════════════════════════════════════════════

_DROP_NS = ("file:", "image:", "category:", "media:", "video:", "audio:")

# Language and interwiki prefixes are lower-case by convention ([[pl:…]],
# [[w:c:ghibli:…]]). Matching case-insensitively would swallow ordinary
# titles that merely contain a colon — [[Avatar: Adapt or Die]] is an
# article, not a link to the "Avatar" wiki.
_KNOWN_INTERWIKI = {"w", "c", "wikipedia", "wikia", "fandom", "commons",
                    "meta", "mw", "wikt", "wikiquote", "wikisource"}


def _render_link(body, depth):
    """[[Target|Display]] → Display, dropping files, categories and langlinks."""
    parts = split_params(body)
    target = parts[0].strip()
    low = target.lower()

    if low.startswith(_DROP_NS):
        return ""

    if len(parts) > 1:
        display = parts[-1].strip()
        return clean(display, depth + 1) if display else clean(parts[0], depth + 1)

    # Bare interwiki / language link, e.g. [[pl:Kinji Hakari]] — not prose.
    if ":" in target:
        prefix = target.split(":", 1)[0]
        if " " not in prefix and (
                prefix.lower() in _KNOWN_INTERWIKI
                or (prefix.islower() and len(prefix) <= 3 and prefix.isalnum())):
            return ""

    return target.split("#")[0].strip()


def _strip_links(text, depth):
    out = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("[[", i):
            body, end = _scan_balanced(text, i, "[[", "]]")
            if body is None:
                out.append(text[i])
                i += 1
                continue
            out.append(_render_link(body, depth))
            i = end
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


# ══════════════════════════════════════════════════════════════════════════
# TABLES
# ══════════════════════════════════════════════════════════════════════════

def _render_table(body):
    """
    Flatten a wiki table to one line per cell. Fandom pages often put real
    lore inside tables (technique descriptions, member lists), so the cells
    are kept as text rather than discarded.
    """
    lines = []
    raw_lines = body.split("\n")
    # The text between `{|` and the first newline is the table's HTML
    # attributes, never content.
    if raw_lines and not raw_lines[0].lstrip().startswith(("|", "!")):
        raw_lines = raw_lines[1:]
    for raw in raw_lines:
        s = raw.strip()
        if not s or s.startswith(("{|", "|}", "|-", "|+", "!-")):
            continue
        if s.startswith("!"):
            cells = re.split(r"!!", s[1:])
        elif s.startswith("|"):
            cells = re.split(r"\|\|", s[1:])
        else:
            cells = [s]
        for cell in cells:
            cell = cell.strip()
            # `| style="…" | text` → drop the attribute segment
            if "|" in cell:
                head, rest = cell.split("|", 1)
                if len(head) < 200 and re.search(r"\w+\s*=\s*[\"']?", head):
                    cell = rest
            cell = cell.strip(" |")
            if cell:
                lines.append(cell)
    return "\n".join(lines)


def _strip_tables(text):
    out = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("{|", i):
            body, end = _scan_balanced(text, i, "{|", "|}")
            if body is None:
                out.append(text[i])
                i += 1
                continue
            out.append("\n" + _render_table(body) + "\n")
            i = end
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


# ══════════════════════════════════════════════════════════════════════════
# MAIN CLEANER
# ══════════════════════════════════════════════════════════════════════════

_TAG_BLOCKS = re.compile(
    r"<(gallery|ref|references|imagemap|score|timeline|graph|poll|tabber"
    r"|infobox|nowiki)\b[^>]*>.*?</\1\s*>",
    re.I | re.S)
_SELF_CLOSING = re.compile(
    r"<(ref|references|gallery|nowiki|infobox|hr)\b[^>]*/\s*>", re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_EXT_LINK = re.compile(r"\[(?:https?:)?//[^\s\]]+[ \t]*([^\]]*)\]")
_BR = re.compile(r"<\s*br\s*/?\s*>", re.I)
_HTML_TAG = re.compile(r"<[^>]{1,200}>")
_BEHAVIOUR = re.compile(r"__[A-Z]+__")
_LIST_LINE = re.compile(r"^([*#:;]+)\s*(.*)$")


def clean(text, depth=0):
    """
    Wikitext → plain text.

    Safe to call on a whole article, one section body, or a single infobox
    value. Images, references and navigation markup are removed; links keep
    their display text; tables are flattened to lines.
    """
    if not text or depth > _MAX_DEPTH:
        return ""

    t = _COMMENT.sub("", text)
    t = _BR.sub("\n", t)
    # Self-closing tags must go before the paired-tag sweep: an unclosed
    # `<ref name="x" />` would otherwise pair with the next real `</ref>`
    # and delete everything in between, including template braces.
    t = _SELF_CLOSING.sub("", t)
    t = _TAG_BLOCKS.sub("", t)

    t = _strip_templates(t, depth)
    t = _strip_links(t, depth)
    t = _strip_tables(t)

    t = _EXT_LINK.sub(lambda m: m.group(1), t)
    t = _BR.sub("\n", t)
    t = _HTML_TAG.sub("", t)
    t = _BEHAVIOUR.sub("", t)

    t = t.replace("'''''", "").replace("'''", "").replace("''", "")
    t = html.unescape(t)

    # Bullet lists → "- " with one level of indent per nesting step
    out = []
    for line in t.split("\n"):
        line = line.rstrip()
        m = _LIST_LINE.match(line.strip())
        if m:
            body = m.group(2).strip()
            if not body:
                continue
            level = len(m.group(1)) - 1
            out.append("  " * level + "- " + body)
        elif line.strip() in ("----", "---"):
            continue
        else:
            out.append(line)
    t = "\n".join(out)

    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r" +\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def clean_inline(text, separator=", "):
    """
    Clean a value that should end up on one line — used for infobox rows.
    Line breaks in the source become separated list items.
    """
    cleaned = clean(text)
    if not cleaned:
        return ""
    items = []
    # Infobox values are lists written with line breaks, bullets or
    # semicolons; normalise all three onto one separator.
    for raw in re.split(r"[\n;]+", cleaned):
        item = raw.strip().lstrip("-").strip(" ,;")
        if item and item not in items:
            items.append(item)
    return separator.join(items)


# ══════════════════════════════════════════════════════════════════════════
# INFOBOX
# ══════════════════════════════════════════════════════════════════════════

_INFOBOX_NAME_RE = re.compile(r"^\s*([\w ]*infobox[\w ]*)\s*$", re.I)
_FIELD_RE = re.compile(r"^\s*([^=|{}\[\]]{1,50}?)\s*=\s*(.*)$", re.S)
_FILE_VALUE_RE = re.compile(
    r"^[\w\s',()\-.]+\.(png|jpe?g|gif|svg|webp|bmp|ogg|mp3|mp4|webm)$", re.I)

# Not every wiki names its infobox "…Infobox" — Elder Scrolls uses
# {{SkyrimLocations}}, others use {{Char Box}}. A template near the top of a
# page carrying several of these fields is an infobox whatever it is called.
_INFOBOX_HINT_KEYS = {
    "name", "image", "title", "caption", "type", "race", "species", "gender",
    "age", "born", "died", "birth", "death", "birthday", "status",
    "affiliation", "occupation", "alias", "aliases", "other", "kanji",
    "romaji", "hair", "eye", "eyes", "height", "weight", "location",
    "region", "capital", "leader", "ruler", "members", "founder",
    "population", "family", "relatives", "partner", "user", "users",
    "previous", "next", "class", "rank", "weapon", "weapons", "abilities",
    "powers", "allegiance", "home", "homeworld", "residence", "province",
    "hold", "nationality", "blood", "gender", "creator", "series", "genre",
}
_NOT_INFOBOX = ({s for s in TEMPLATE_DROP_SUBSTR if s != "infobox"}
                | {"issues", "multipleissues", "qref", "quote", "cite"})

LABEL_OVERRIDES = {
    "kanji": "Kanji", "romaji": "Rōmaji", "romanji": "Rōmaji",
    "kana": "Kana", "alias": "Also known as", "aliases": "Also known as",
    "nickname": "Nickname", "nicknames": "Nicknames", "other": "Other names",
    "othernames": "Other names", "vizname": "Viz name", "epithet": "Epithet",
    "race": "Species", "eye": "Eye colour", "eyes": "Eye colour",
    "hair": "Hair colour", "birthday": "Birthday", "born": "Born",
    "occupation": "Occupation", "affiliation": "Affiliation",
    "located in": "Located in", "controlled by": "Controlled by",
    "user": "Users", "users": "Users", "previous": "Previous",
    "next": "Next", "teams": "Teams", "partner": "Partner",
    "relatives": "Relatives", "jname": "Japanese name",
    # Fields wikis write as one word. Title-casing alone gives "Fightingstyle".
    "fightingstyle": "Fighting style", "fighting style": "Fighting style",
    "bloodtype": "Blood type", "blood type": "Blood type",
    "birthplace": "Birthplace", "hairColor": "Hair colour",
    "eyecolor": "Eye colour", "eyecolour": "Eye colour",
    "haircolor": "Hair colour", "haircolour": "Hair colour",
    "firstappearance": "First appearance", "lastappearance": "Last appearance",
    "debut": "Debut", "quirk": "Quirk", "quirktype": "Quirk type",
}


_EMBEDDED_INFOBOX = re.compile(r"^\s*\{\{\s*infobox\b", re.I)


def _expand_embedded(fields, depth=0):
    """
    Splice a nested infobox's rows in where its wrapper row sat.

    Wikipedia biographies routinely carry a second infobox inside the first:
    50 Cent is `{{Infobox person | … | module = {{Infobox musical artist |
    genre = … | label = … }} }}`. Genres and labels — the two facts a musician
    entry most wants — live only in that inner template, and reading the outer
    one alone finds a field called "module" holding an unreadable blob.
    """
    if depth > 3:
        return fields
    out = []
    for field, raw in fields:
        value = (raw or "").strip()
        if not _EMBEDDED_INFOBOX.match(value):
            out.append((field, raw))
            continue
        body, _ = _scan_balanced(value, 0, "{{", "}}")
        if body is None:
            out.append((field, raw))
            continue
        inner = []
        for part in split_params(body)[1:]:
            m = _FIELD_RE.match(part)
            if m:
                inner.append((m.group(1).strip(), m.group(2)))
        out += _expand_embedded(inner, depth + 1)
    return out


def find_infobox(wikitext):
    """
    Return (template_name, [(field, raw_value), …]) for the page's infobox,
    or (None, []) when it has none.

    Only templates above the first heading are considered — that is where an
    infobox always sits, and it keeps citation templates in the body from
    being mistaken for one.
    """
    heading = _HEADING_RE.search(wikitext)
    limit = heading.start() if heading else len(wikitext)

    fallback = None
    i = 0
    while i < limit:
        j = wikitext.find("{{", i)
        if j < 0 or j >= limit:
            break
        body, end = _scan_balanced(wikitext, j, "{{", "}}")
        if body is None:
            break

        parts = split_params(body)
        name = re.sub(r"\s+", " ", parts[0].strip().replace("_", " ")).strip()
        fields = []
        for part in parts[1:]:
            m = _FIELD_RE.match(part)
            if m:
                fields.append((m.group(1).strip(), m.group(2)))
        fields = _expand_embedded(fields)

        low = name.lower()
        if _INFOBOX_NAME_RE.match(name):
            return name, fields

        if (fallback is None and len(fields) >= 4
                and low not in TEMPLATE_DROP
                and not any(s in low for s in _NOT_INFOBOX)
                and {f[0].lower() for f in fields} & _INFOBOX_HINT_KEYS):
            fallback = (name, fields)

        i = end if end > j else j + 2

    return fallback or (None, [])


def is_blocked_field(field):
    """True when an infobox row is production metadata rather than lore."""
    key = re.sub(r"[\s_\-]+", " ", (field or "")).strip().lower()
    return bool(_INFOBOX_BLOCK_RE.match(key) or _INFOBOX_CONTAINS_RE.search(key))


def parse_infobox(wikitext):
    """
    Extract an infobox as an ordered list of {label, value} dicts.

    Rows carrying no roleplay value — images, "First Appearance", "Portrayal",
    voice actors, publication data — are dropped (see INFOBOX_BLOCKLIST).
    """
    name, fields = find_infobox(wikitext)
    rows = []
    for field, raw in fields:
        if is_blocked_field(field):
            continue
        value = clean_inline(raw)
        if not value or value in ("-", "—", "N/A", "None", "TBA", "Unknown"):
            continue
        # A bare filename survived under a field name we do not recognise
        # (coats of arms, maps, portraits) — it is still just an image.
        if _FILE_VALUE_RE.match(value):
            continue
        key = re.sub(r"[\s_]+", " ", field).strip().lower()
        label = LABEL_OVERRIDES.get(key) or key.title()
        rows.append({"label": label, "value": value, "field": key})
    return name, rows


# ══════════════════════════════════════════════════════════════════════════
# SECTIONS
# ══════════════════════════════════════════════════════════════════════════

_HEADING_RE = re.compile(r"^[ \t]*(={2,6})[ \t]*(.+?)[ \t]*\1[ \t]*$", re.M)


def normalise_heading(raw):
    """Heading wikitext → a bare lower-case string for blocklist matching."""
    t = clean(raw)
    t = re.sub(r"[​ ]", " ", t)
    t = t.replace("&", "and")
    t = re.sub(r"[^\w\s/-]", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def is_blocked_section(raw_title):
    """True when a heading names a section a lorebook should not include."""
    norm = normalise_heading(raw_title)
    return bool(_SECTION_BLOCK_RE.match(norm) or _SECTION_CONTAINS_RE.search(norm))


def section_priority(raw_title):
    """1 = keep at all costs, 3 = drop first when trimming."""
    norm = normalise_heading(raw_title)
    for tier, pattern in _PRIORITY_RES:
        if pattern.search(norm):
            return tier
    return 3


def split_sections(wikitext):
    """
    Split an article into (lead_wikitext, [section, …]).

    Sections on the blocklist are removed along with everything nested under
    them, so dropping "Media" also drops its "Video Games" subsection.
    """
    matches = list(_HEADING_RE.finditer(wikitext))
    lead = wikitext[:matches[0].start()] if matches else wikitext

    sections = []
    blocked_at = None          # level of the heading currently being skipped
    ancestors = {}             # heading level → priority, for inheritance
    for idx, m in enumerate(matches):
        level = len(m.group(1))
        title_raw = m.group(2)
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(wikitext)

        if blocked_at is not None:
            if level > blocked_at:
                continue       # still inside the blocked branch
            blocked_at = None

        if is_blocked_section(title_raw):
            blocked_at = level
            continue

        title = clean(title_raw).strip()
        if not title:
            continue

        # A subsection is as important as its parent unless it scores better
        # on its own — "Physical Prowess" under "Abilities and Powers" is not
        # filler just because its name is unusual.
        priority = section_priority(title_raw)
        inherited = min([p for lvl, p in ancestors.items() if lvl < level],
                        default=priority)
        priority = min(priority, inherited)
        ancestors = {lvl: p for lvl, p in ancestors.items() if lvl < level}
        ancestors[level] = priority

        sections.append({
            "title":    title,
            "level":    level,
            "priority": priority,
            "body":     wikitext[m.end():end],
        })

    return lead, sections


_LINK_TARGET_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]*))?\]\]")


def find_template(text, name_pattern):
    """
    Return (name, args, kwargs) for the first template whose name matches.

    Some wikis hold a whole structured list in one template — Wookieepedia
    files a film's appearances as {{App|c-characters=…|c-vehicles=…}} — and
    the parameters are the groups.
    """
    matcher = re.compile(name_pattern, re.I)
    i, n = 0, len(text or "")
    while i < n:
        j = text.find("{{", i)
        if j < 0:
            break
        body, end = _scan_balanced(text, j, "{{", "}}")
        if body is None:
            break
        parts = split_params(body)
        name = re.sub(r"\s+", " ", parts[0].strip().replace("_", " ")).strip()
        if matcher.match(name):
            args, kwargs = _template_args(parts[1:])
            return name, args, kwargs
        i = end if end > j else j + 2
    return None, [], {}


def iter_sections(wikitext):
    """
    Every section of a page, unfiltered, as (lead, [{level, title, body}, …]).

    `split_sections` drops the sections a lorebook should not quote. This one
    keeps them, because some of those sections are still worth *reading
    structurally* — a film's "Cast" and "Appearances" lists are the index of
    everything that appears in it, even though neither belongs in its text.
    """
    matches = list(_HEADING_RE.finditer(wikitext))
    lead = wikitext[:matches[0].start()] if matches else wikitext

    sections = []
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(wikitext)
        sections.append({
            "level": len(m.group(1)),
            "title": clean(m.group(2)).strip(),
            "body":  wikitext[m.end():end],
        })
    return lead, sections


def link_targets(text):
    """
    [(target, display), …] for every wiki link in `text`.

    Files, categories and language links are skipped; section anchors are
    dropped so "[[Sorcerer Clan#Zenin]]" resolves to the article itself.
    """
    found = []
    for m in _LINK_TARGET_RE.finditer(text or ""):
        target = m.group(1).strip()
        # "[[:Category:Cursed Tools]]" — the leading colon makes it a link to
        # the category rather than a filing under it, but it is the same page
        # and just as unwanted in a lorebook.
        target = target.lstrip(":").strip()
        low = target.lower()
        if low.startswith(_DROP_NS):
            continue
        prefix = target.split(":", 1)[0] if ":" in target else ""
        if prefix and (prefix.lower() in _KNOWN_INTERWIKI
                       or (prefix.islower() and len(prefix) <= 3 and prefix.isalnum())):
            continue
        target = target.split("#")[0].strip()
        if not target:
            continue
        display = clean(m.group(2) or target).strip() or target
        found.append((target, display))
    return found


def trim_text(text, limit):
    """
    Cut text to `limit` characters on a paragraph or sentence boundary.

    Boundaries are tried nearest-the-limit first rather than best-kind first.
    Preferring a paragraph break wherever one falls threw away up to half the
    allowance: an arc whose first paragraph ended at 339 characters came back
    339 characters long however much room it had been given, because that was
    the only break inside the window. A sentence end at 640 is the better cut.
    """
    if not text or len(text) <= limit:
        return text
    window = text[:limit]

    para = max(window.rfind(chr(10) * 2), window.rfind(chr(10)))
    if para > limit * 0.75:
        return window[:para].rstrip()

    stop = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if stop > limit * 0.5:
        return window[:stop + 1].rstrip()
    if para > limit * 0.4:
        return window[:para].rstrip()
    if stop > limit * 0.3:
        return window[:stop + 1].rstrip()
    return window.rstrip() + "…"


def strip_noise_templates(text):
    """
    Drop navigation, footer and infobox templates while leaving `[[links]]`
    intact, so a page's links can be read structurally.

    `clean` resolves links to their display text, which is right for prose and
    useless for working out *what a page points at*. Scope discovery needs the
    targets, but not the ones every page on the wiki carries: a navbox listing
    all 271 chapters of the parent series appears on each of a spin-off's
    chapter pages and would otherwise drown out its actual cast.

    The surviving templates keep their parameters, because Fandom roster grids
    and infobox rows hold real links ("new character = [[Yuka Okkotsu]]").
    """
    out, i, n = [], 0, len(text or "")
    while i < n:
        j = text.find("{{", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        body, end = _scan_balanced(text, j, "{{", "}}")
        if body is None:
            out.append(text[j:])
            break
        name = re.sub(r"\s+", " ",
                      split_params(body)[0].strip().replace("_", " ")).strip().lower()
        if not (name in TEMPLATE_DROP
                or any(s in name for s in TEMPLATE_DROP_SUBSTR)):
            out.append(body)
        i = end if end > j else j + 2
    return "".join(out)


def infobox_links(wikitext, field_pattern):
    """
    Link targets from the infobox rows whose field name matches a pattern.

    A chapter's "new character" row is the wiki stating outright which
    characters debut in it — the single most reliable way to tell a spin-off's
    own cast from the parent series' cast walking through it.
    """
    _, fields = find_infobox(wikitext)
    matcher = re.compile(field_pattern, re.I)
    found = []
    for field, raw in fields:
        if matcher.search(re.sub(r"[\s_\-]+", " ", field).strip()):
            found += [t for t, _ in link_targets(raw)]
    return found

def flatten_sections(sections, keep_level=2):
    """
    Fold subsections into the top-level section they belong to.

    Wiki pages nest three and four deep — 50 Cent's "Career" holds six
    date-range subsections, and a Fandom character's "Synopsis" holds one per
    story arc. Budgeted individually each gets a couple of hundred characters
    and the entry becomes a list of stubs. Merged, each is one substantial
    block that actually reads, which is what a lorebook wants.

    Sub-headings are kept inline rather than dropped: "Shibuya Incident Arc: …"
    says which part of the story is being described, in a fraction of the space
    a heading costs.
    """
    merged = []
    for section in sections:
        text = clean(section["body"]).strip() if "body" in section             else (section.get("text") or "").strip()
        if section["level"] <= keep_level or not merged:
            merged.append({
                "title":    section["title"],
                "level":    min(section["level"], keep_level),
                "priority": section["priority"],
                "parts":    [text] if text else [],
            })
            continue
        if text:
            title = (section["title"] or "").strip()
            merged[-1]["parts"].append(f"{title}: {text}" if title else text)
        # A subsection can be the more useful half of its parent, so the
        # parent inherits the best priority found anywhere beneath it.
        merged[-1]["priority"] = min(merged[-1]["priority"], section["priority"])

    return [{"title": m["title"], "level": m["level"],
             "priority": m["priority"],
             "text": (chr(10)*2).join(m["parts"]).strip()}
            for m in merged]



# ════════════════════════════════════════════════════════════════════════
# SECTION BUDGETING
# ════════════════════════════════════════════════════════════════════════

_TIER_WEIGHT = {1: 3, 2: 2, 3: 1}
_TIER_CAP = {1: 2600, 2: 1600, 3: 800}
MIN_SECTION_CHARS = 260

# Below this a "section" is a label with a stub under it, not writing — the
# Invincible wiki has "Emperor Suit I" carrying the word "N/A". Set low on
# purpose: a genuine one-sentence section is still a section.
MIN_MEANINGFUL_CHARS = 12


# The least a story arc may be cut to before it stops saying anything. Well
# under MIN_SECTION_CHARS, because for a story a thin account of the ending
# beats no account of it.
MIN_ARC_CHARS = 130


def fit_sections(sections, budget, cap_scale=1.0, keep_all=False):
    """
    Trim cleaned sections to fit a character budget.

    `cap_scale` widens the per-section ceilings. The defaults stop one long
    section from eating an entry, which is right for an article's own sections
    — but a story synopsis *is* the entry's substance, and when the user has
    narrowed it to one or two arcs those arcs should be allowed the whole
    budget rather than 1,600 characters each.

    `keep_all` stops sections being dropped outright when there are more of
    them than the budget comfortably covers. Dropping is right for an
    article's sections — better to say something about Personality than a
    fragment of each of nine headings — but wrong for a story told in order,
    where the sections that lose the tie-break are always the *last* ones and
    what gets discarded is the ending. Koichi Haimawari's synopsis lost its
    final four arcs, climax included, to that rule.

    Space is shared out in proportion to how useful each section is for
    roleplay (see wikitext.SECTION_PRIORITY), not first-come-first-served —
    otherwise a 40-part story arc would spend its whole budget on chapter one
    and never reach the ending. Sections shorter than their share hand the
    remainder back to the ones that were truncated.
    """
    # A heading with three characters under it is a label, not a section, and
    # it costs more in the entry than it gives back — "Emperor Suit I" arrived
    # carrying the word "N/A". Treated as empty, it is pruned unless it has
    # real subsections beneath it.
    def has_prose(section):
        return len((section["text"] or "").strip()) >= MIN_MEANINGFUL_CHARS

    with_text = [i for i, s in enumerate(sections) if has_prose(s)]
    kept = {i: "" for i, s in enumerate(sections) if not has_prose(s)}
    if not with_text:
        return [dict(sections[i], text="") for i in sorted(kept)]

    headings = sum(len(sections[i]["title"]) + 6 for i in range(len(sections)))
    pot = max(400, budget - headings)

    # Too many sections to say anything useful about each? Drop the least
    # relevant ones rather than reducing everything to a fragment.
    ranked = sorted(with_text, key=lambda i: (sections[i]["priority"], i))
    floor = MIN_ARC_CHARS if keep_all else MIN_SECTION_CHARS
    if not keep_all:
        keep_n = max(1, min(len(ranked), int(pot // MIN_SECTION_CHARS)))
        ranked = ranked[:keep_n]

    weights = {i: _TIER_WEIGHT[sections[i]["priority"]] for i in ranked}
    total_w = sum(weights.values()) or 1
    alloc = {i: pot * weights[i] / total_w for i in ranked}

    for _ in range(4):
        spare, needy = 0.0, []
        for i in ranked:
            ceiling = min(len(sections[i]["text"]),
                          _TIER_CAP[sections[i]["priority"]] * cap_scale)
            if alloc[i] > ceiling:
                spare += alloc[i] - ceiling
                alloc[i] = ceiling
            elif alloc[i] < ceiling:
                needy.append(i)
        if spare < 1 or not needy:
            break
        needy_w = sum(weights[i] for i in needy) or 1
        for i in needy:
            alloc[i] += spare * weights[i] / needy_w

    for i in ranked:
        kept[i] = trim_text(sections[i]["text"], max(floor, int(alloc[i])))

    # Drop headings that ended up with neither text nor a surviving child.
    result = [dict(sections[i], text=kept[i]) for i in sorted(kept)]
    pruned = []
    for idx, section in enumerate(result):
        if section["text"]:
            pruned.append(section)
            continue
        has_child = any(nxt["level"] > section["level"] and nxt["text"]
                        for nxt in result[idx + 1:idx + 6])
        if has_child:
            pruned.append(section)
    return pruned
