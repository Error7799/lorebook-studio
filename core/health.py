"""
Whether a finished lorebook will actually work once it is in SillyTavern.

A lorebook fails quietly. Entries that never fire, entries that all fire at
once, a book twice the size of the budget it has to fit in — none of it shows
up until you are mid-roleplay wondering why the model has forgotten who
somebody is. Every one of them is checkable before that happens.

The rules come from how World Info actually behaves:

  * **There is a token budget.** SillyTavern activates entries until the
    budget runs out and then stops, so a book that overruns loses whichever
    entries matched last, silently. The default allowance is 20% of context.
  * **Keys are shared, not owned.** Two entries keyed "Hunter" both fire on
    the word, and both are paid for.
  * **A common word is not a name.** An entry keyed "Rock" is in context for
    the rest of the session.
  * **An entry with no keys and no always-on flag can never fire at all.**
"""

# SillyTavern gives World Info a fifth of the context window by default.
BUDGET_SHARE = 0.20

# Roughly four characters to the token for English prose. The real tokeniser
# depends on the model; this is the approximation the rest of the app uses,
# and it is close enough to answer "does this fit".
CHARS_PER_TOKEN = 4

# A key shorter than this matches inside other words unless whole-word
# matching is on: "Rin" fires on "ring", "bring" and "during".
SHORT_KEY = 4

# One entry eating more than this share of the budget crowds out the rest the
# moment it fires.
HEAVY_SHARE = 0.25

# Words too common to be a trigger on their own. Deliberately short and
# obvious: the point is to catch clear mistakes, not to second-guess a world.
COMMON_WORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at",
    "man", "woman", "boy", "girl", "child", "people", "person", "human",
    "king", "queen", "lord", "lady", "master", "doctor", "captain", "hero",
    "villain", "city", "town", "world", "school", "home", "house", "room",
    "power", "magic", "sword", "gun", "war", "battle", "god", "death", "life",
    "one", "two", "three", "first", "last", "new", "old", "big", "small",
    "red", "blue", "green", "black", "white", "gold", "silver",
    "rock", "stone", "fire", "water", "wind", "earth", "light", "dark",
    "mother", "father", "brother", "sister", "friend", "enemy", "team",
}


def estimate_tokens(text):
    """The app's rough token count for a piece of entry text."""
    return len(text or "") // CHARS_PER_TOKEN


def entry_name(entry):
    """A readable name for an entry, however it was built."""
    comment = (entry.get("comment") or "").strip()
    if comment:
        return comment.split(":", 1)[-1].strip() or comment
    keys = entry.get("key") or []
    return keys[0] if keys else "untitled"


def _keys_of(entry):
    keys = entry.get("key") or []
    if isinstance(keys, str):
        keys = [keys]
    return [k.strip() for k in keys if (k or "").strip()]


def inspect(entries, context_tokens=8192, budget_tokens=None):
    """
    Look over a whole book and report what would go wrong.

    `context_tokens` is the model's context window; the allowance is
    BUDGET_SHARE of it unless `budget_tokens` overrides. Returns

        {tokens, allowance, context, entries, constant_tokens, issues}

    where each issue is {kind, level, title, detail, entries} and `level` is
    "error" for what cannot work, "warn" for what will misbehave, and "note"
    for what is worth knowing.
    """
    entries = list(entries or [])
    allowance = int(budget_tokens or context_tokens * BUDGET_SHARE)
    sizes = {id(e): estimate_tokens(e.get("content")) for e in entries}
    total = sum(sizes.values())
    constant_total = sum(sizes[id(e)] for e in entries if e.get("constant"))

    issues = []

    def add(kind, level, title, detail, names=()):
        issues.append({"kind": kind, "level": level, "title": title,
                       "detail": detail, "entries": list(names)[:12]})

    if total > allowance:
        add("budget", "warn",
            f"{total:,} tokens against a {allowance:,} allowance",
            f"About {total - allowance:,} tokens more than one activation can "
            f"hold. Nothing is lost from the file — entries simply stop being "
            f"added once the budget runs out, and the ones that miss out are "
            f"whichever matched last. Narrow the arcs, drop a detail level, "
            f"or split the book per story.")

    if constant_total and constant_total > allowance * 0.5:
        add("constant-heavy", "warn",
            "Always-on entries take up half the budget",
            f"{constant_total:,} tokens fire on every message, leaving little "
            f"room for the entries that answer what is actually being said.",
            [entry_name(e) for e in entries if e.get("constant")])

    silent = [entry_name(e) for e in entries
              if not e.get("constant") and not _keys_of(e)]
    if silent:
        add("silent", "error",
            f"{len(silent)} " + ("entry" if len(silent) == 1 else "entries")
            + " can never fire",
            "No keywords and not marked always-on, so nothing will ever bring "
            "them into context.", silent)

    empty = [entry_name(e) for e in entries
             if not (e.get("content") or "").strip()]
    if empty:
        add("empty", "error",
            f"{len(empty)} empty "
            + ("entry" if len(empty) == 1 else "entries"),
            "They take up a slot and say nothing.", empty)

    owners = {}
    for entry in entries:
        for key in _keys_of(entry):
            owners.setdefault(key.lower(), []).append(entry)

    shared = {k: v for k, v in owners.items() if len(v) > 1}
    if shared:
        worst = sorted(shared.items(), key=lambda kv: -len(kv[1]))[:6]
        add("collision", "warn",
            f"{len(shared)} "
            + ("keyword is" if len(shared) == 1 else "keywords are")
            + " used by more than one entry",
            "Every entry sharing a keyword fires together and is paid for "
            "together. Usually one of them has borrowed a name belonging to "
            "the other.",
            [k + " — " + ", ".join(entry_name(e) for e in v[:3])
             for k, v in worst])

    broad = []
    for entry in entries:
        whole = entry.get("matchWholeWords")
        for key in _keys_of(entry):
            low = key.lower()
            if low in COMMON_WORDS:
                broad.append(key + " — " + entry_name(entry))
            elif len(low) < SHORT_KEY and whole is not True:
                broad.append(key + " — " + entry_name(entry)
                             + " (matches inside other words)")
    if broad:
        add("broad-key", "warn",
            f"{len(broad)} "
            + ("keyword" if len(broad) == 1 else "keywords")
            + " will fire far too often",
            "An entry keyed on an everyday word stays in context for the rest "
            "of the session, spending budget on every message whether it is "
            "relevant or not.", broad)

    heavy = [(entry_name(e), sizes[id(e)]) for e in entries
             if sizes[id(e)] > allowance * HEAVY_SHARE]
    if heavy:
        heavy.sort(key=lambda pair: -pair[1])
        add("heavy", "note",
            f"{len(heavy)} "
            + ("entry takes" if len(heavy) == 1 else "entries take")
            + " a quarter of the budget on their own",
            "Fine if they are the subject of the roleplay; a problem if they "
            "are scenery.",
            [n + f" — {t:,} tokens" for n, t in heavy])

    if entries and not any(e.get("constant") for e in entries):
        add("no-constant", "note",
            "Nothing is always on",
            "Every entry here waits to be mentioned. A short always-on entry "
            "naming the setting gives the model somewhere to stand before any "
            "keyword matches.")

    rank = ["error", "warn", "note"]
    issues.sort(key=lambda issue: rank.index(issue["level"]))
    return {
        "tokens": total,
        "allowance": allowance,
        "context": context_tokens,
        "entries": len(entries),
        "constant_tokens": constant_total,
        "issues": issues,
    }
