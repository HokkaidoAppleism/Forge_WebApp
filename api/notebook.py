"""Notebook internals: where a saved item is filed, and how guides are built.

Two jobs, both lifted from `merged_api.py`, and both worth stating plainly
because they are the parts a straight CRUD port gets wrong.

**Filing.** Adaptive Learning picks questions by *sub*category -- the keys of
the cluster models are "Chemistry", "American Literature" -- and hands that
back to the client as `category`. Every writer that trusted it filed the item
under a name that is not a real notebook category, which split Chemistry
flashcards off the Science shelf and minted a phantom tile per subcategory.
The desktop build fixed that by resolving the category off the question row;
`canonical_category` below is that same rule, and on the web it is also the
rule the rest of this API already follows for anything that matters -- the
client says *which* question, the server says what that question is.

**Guides.** A notebook with one entry per tossup is unusable within a week, so
several notes roll up into one named guide: each source note becomes a section
headed by its answerline, sorted A-Z like a reference list. That is pure text
assembly with no model involved, which is why it is here rather than waiting
on the server-side Gemini key the AI features still need.

Nothing in this module opens a transaction. Every function that needs a query
takes a connection the caller already scoped to a user, so a notebook query
cannot accidentally run outside RLS.
"""

import re
from datetime import datetime

# The AI opens a guide by restating the question:
#   Here's an explanation of the quizbowl question about Gaius Julius Caesar:
#   Here's an explanation of each clue in context of the answer "post-office":
# The answerline is what belongs in a heading, not the whole sentence.
_ANSWER_PATTERNS = [
    re.compile(r'\bin context of\s+"([^"]+)"', re.I),
    re.compile(r'\bcontext of the answer\s+"([^"]+)"', re.I),
    re.compile(r'\bthe answer\s+"([^"]+)"', re.I),
    re.compile(r'\bregarding\s+"([^"]+)"', re.I),
    re.compile(r'\babout\s+"([^"]+)"', re.I),
    re.compile(r'\bregarding\s+(.+?)\s*:', re.I),
    re.compile(r'\babout\s+(.+?)\s*:', re.I),
]

_SORT_ARTICLES = ("a ", "an ", "the ")

SECTION_RULE = "\n\n---\n\n"


# ------------------------------------------------------------------ filing ---

def resolve_bare_category(conn, name):
    """Split a bare category name into (category, subcategory).

    The question table is the authority on which names are categories and
    which are subcategories, so no hardcoded list can drift from it. A name
    that is already a real category passes straight through; one that is only
    ever a subcategory is filed under its parent and kept as the shelf.

    This only runs on a save with no source question -- a category-wide study
    guide, mostly -- and both lookups are indexed.
    """
    if not name:
        return name, None

    if conn.execute("select 1 from public.questions where category = %s limit 1",
                    (name,)).fetchone():
        return name, None

    row = conn.execute(
        "select category from public.questions where subcategory = %s limit 1",
        (name,)).fetchone()
    if row and row["category"]:
        return row["category"], name

    # Not a name the question set knows -- a hand-made category. Left alone
    # rather than guessed at.
    return name, None


def canonical_category(conn, source_question_id, fallback=None):
    """Where a saved item belongs: (category, subcategory), from the question.

    The client names the question; the server decides what that question is.
    Same principle as the scoring in routes/answers.py and the cluster a skill
    update is filed against -- a value that decides where data lands is read
    off the row, not off the request.
    """
    if source_question_id is None:
        return resolve_bare_category(conn, fallback)

    row = conn.execute(
        "select category, subcategory from public.questions where id = %s",
        (source_question_id,)).fetchone()
    if row:
        return (row["category"] or fallback), row["subcategory"]
    return fallback, None


# ------------------------------------------------------------------ titles ---

def looks_like_intro_sentence(text):
    """True for the AI's opening line, which is never a usable title."""
    t = (text or "").strip().lower()
    return bool(t) and (
        t.startswith("here's") or t.startswith("here is")
        or "explanation of the quizbowl" in t
        or ("explanation" in t and t.endswith(":"))
    )


def _shorten(text, limit=58):
    """Trim a fallback heading at a word boundary rather than mid-word."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{cut or text[:limit]}…"


def derive_title_from_content(content):
    """Best-effort answerline for a note saved without one.

    Falls through: a markdown heading, an answer quoted in the intro sentence,
    the first bold run, then the first line. Without this most guides would be
    titled "Here's an explanation of the quizbowl question...", identical for
    every note -- so they would all sort together and read as duplicates of
    each other.
    """
    if not content:
        return ""
    lines = [l.strip() for l in str(content).splitlines() if l.strip()]
    if not lines:
        return ""

    heading = re.match(r"^#{1,3}\s+(.+)$", lines[0])
    if heading:
        name = heading.group(1).strip()
        if name and not looks_like_intro_sentence(name):
            return _shorten(name)

    first = lines[0].lstrip("#").strip()
    for pattern in _ANSWER_PATTERNS:
        m = pattern.search(first)
        if m:
            name = m.group(1).strip(" \"'")
            # "the quizbowl question" is the sentence's own subject, not an answer
            if name and "quizbowl question" not in name.lower():
                return name[:80]

    for line in lines[:6]:
        m = re.search(r"\*\*(.+?)\*\*", line)
        if m:
            return _shorten(m.group(1).strip().rstrip(":"))

    return _shorten(first)


def strip_intro_line(content, heading_name):
    """Drop a note's own title line when it becomes a section of a guide.

    Two ways the same thing ends up said twice: the AI's restatement of the
    question, and the note's own markdown title sitting one line under the
    section heading that already says it. Any leading heading goes.
    """
    if not content:
        return ""
    text = str(content).lstrip()
    lines = text.split("\n")
    if not lines:
        return text

    raw_first = lines[0].strip()
    first = raw_first.lstrip("#").strip()
    looks_like_intro = (
        first.endswith(":")
        and ("explanation" in first.lower() or "here" in first.lower()[:6])
    )
    is_own_heading = raw_first.startswith("#")
    same_as_heading = bool(heading_name) and first.lower() == heading_name.strip().lower()
    if looks_like_intro or is_own_heading or same_as_heading:
        return "\n".join(lines[1:]).lstrip()
    return text


# ------------------------------------------------------------------ guides ---

def guide_sort_key(name):
    """Dictionary-style ordering key for a section heading."""
    s = (name or "").strip().lower()
    s = re.sub(r"^[^0-9a-z]+", "", s)      # leading quotes, asterisks, etc.
    for article in _SORT_ARTICLES:
        if s.startswith(article):
            s = s[len(article):]
            break
    return s


def build_note_sections(rows, clean_answerline):
    """Turn note rows into markdown sections, one per note.

    Each row needs `notes_content`, `answer_text` and `difficulty`. The note's
    full text is carried over verbatim -- nothing is summarised or truncated --
    under a heading taken from its answerline.

    `clean_answerline` is passed in rather than imported so this module holds
    no opinion about which copy of the matcher is live; see the de-duplication
    note in web/README.md section 6.
    """
    sections = []
    for row in rows:
        content = row["notes_content"]
        name = clean_answerline(
            row["answer_text"] or derive_title_from_content(content)) or "Untitled"
        heading = f"## {name}"
        if row["difficulty"] is not None:
            heading += f"  *(Difficulty {row['difficulty']})*"
        body = strip_intro_line(content, name)
        sections.append(f"{heading}\n\n{body.strip()}")
    return sections


def split_guide_sections(content):
    """Split a rendered guide back into (title_line, [sections]).

    The inverse of the join in `build_note_sections`, so an append can re-sort
    the whole guide instead of tacking an unsorted tail onto the end of it.
    """
    text = (content or "").strip()
    title_line = ""
    if text.startswith("# "):
        parts = text.split("\n", 1)
        title_line = parts[0].strip()
        text = parts[1].strip() if len(parts) > 1 else ""
    sections = [s.strip() for s in re.split(r"\n\s*---\s*\n", text) if s.strip()]
    return title_line, sections


def section_heading_name(section):
    """The answerline a section is filed under, for sorting."""
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("##"):
            name = line.lstrip("#").strip()
            return re.sub(r"\s*\*\(Difficulty[^)]*\)\*\s*$", "", name).strip()
    return section.strip()[:80]


# ------------------------------------------------------------------ export ---
# Ported from merged_api.py's `_renest_note_markdown` / `build_notes_markdown`
# -- pure text assembly, no model involved, so it lives here rather than in
# routes/export.py the way build_note_sections above lives here rather than
# in routes/notebook.py.

def _deepest_heading_level(content):
    """The largest `#` run used in `content`, ignoring fenced code."""
    deepest, fenced = 0, False
    for line in (content or "").split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = re.match(r"^(#{1,6})\s+\S", line)
        if m:
            deepest = max(deepest, len(m.group(1)))
    return deepest


def _shift_headings(content, shift):
    """`content` with every heading moved `shift` levels deeper, capped at 6.

    Fenced code is left alone: a ``` block can legitimately contain a line
    starting with #, and that is code, not a heading.
    """
    if shift <= 0:
        return content
    out, fenced = [], False
    for line in (content or "").split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        m = None if fenced else re.match(r"^(#{1,6})(\s+)(.*)$", line)
        if m:
            line = "#" * min(6, len(m.group(1)) + shift) + m.group(2) + m.group(3)
        out.append(line)
    return "\n".join(out)


def _export_entry(row, clean_answerline):
    """One `notebook_notes` row as a `### heading` section of an export file.

    An exported entry sits under a `## Your Study Guides` / `## Question
    Notes` group heading. A guide's own content starts at `##` -- the level
    `build_note_sections` above already writes its per-question sections at
    -- which in an export would read as a *sibling* of the group heading
    instead of as that guide's contents. So headings inside the entry move
    down by however much still leaves every level distinct: never a flat
    clamp to 6, which used to collapse `#####` and `######` onto the same
    level and make a sub-point read as a sibling of its own parent -- the
    exact thing this shift exists to prevent.
    """
    content = row["notes_content"] or ""
    lines = content.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    # The entry gets its own heading below; a leading "# Title" line in the
    # content would just repeat it.
    if i < len(lines) and re.match(r"^#\s+\S", lines[i]):
        del lines[i]
    content = "\n".join(lines)

    shift = max(0, min(2, 6 - _deepest_heading_level(content)))
    body = _shift_headings(content, shift).strip()

    name = (row["title"] or "").strip() or clean_answerline(row["answer_text"] or "") or None
    head = name or f"Untitled ({str(row['created_at'])[:10]})"
    if row["difficulty"] is not None:
        head += f" (difficulty {row['difficulty']})"
    return f"### {head}\n\n{body}\n\n"


def build_export_markdown(notes, heading, clean_answerline):
    """Guides first, then question notes, as one markdown document.

    Shared by the whole-shelf export and the selected-rows export, so the two
    paths can't drift into describing the same data two different ways.
    `notes` rows need `notes_content`, `title`, `answer_text`, `difficulty`,
    `is_merged`, `created_at`; `heading` is the document's own `# ` title.
    """
    guides = [n for n in notes if n["is_merged"]]
    singles = [n for n in notes if not n["is_merged"]]

    counted = []
    if guides:
        counted.append(f"{len(guides)} study guide{'' if len(guides) == 1 else 's'}")
    if singles:
        counted.append(f"{len(singles)} question note{'' if len(singles) == 1 else 's'}")
    today = datetime.now().strftime("%Y-%m-%d")
    md = (f"# {heading}\n\n"
          f"Exported from ForgeQB on {today}. Contains {' and '.join(counted)}.\n\n")

    # Each group heading only appears when it has something under it, the same
    # rule the notebook column itself follows.
    if guides:
        md += "---\n\n## Your Study Guides\n\n"
        md += "".join(_export_entry(n, clean_answerline) for n in guides)
    if singles:
        md += "---\n\n## Question Notes\n\n"
        md += "".join(_export_entry(n, clean_answerline) for n in singles)
    return md


def sort_guide_sections(sections):
    """Order sections A-Z by answerline, like a dictionary."""
    return sorted(sections, key=lambda s: guide_sort_key(section_heading_name(s)))
