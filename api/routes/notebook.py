"""The notebook: notes, study guides, flashcards and saved clues.

This is the CRUD half of the app, and it is mostly ordinary -- which is worth
saying, because the three places it is *not* ordinary are the three places the
desktop build has already been bitten:

**Where an item is filed is decided by the server.** `category` arrives in the
request and is used only as a fallback. What actually gets stored is read off
the question row (see notebook.canonical_category), because an adaptive session
sends a subcategory where the notebook expects a category, and every item saved
during one landed on a shelf that does not exist.

**An id in the URL is not permission to touch that row.** Every statement here
carries `user_id = %s` on top of RLS, and a write that matches nothing is a
404, never a success. The desktop build had no owner to check and its
update/delete endpoints reported success on a row that was not there, which is
how edited notes came back unedited after a reload.

**Delete-originals happens in the transaction that writes the guide.** Merging
notes into a guide and deleting the sources are one statement's worth of intent
and must not be two requests: a failure in between destroys notes with no guide
to show for them.

Deletes are POSTs rather than DELETEs, matching the rest of the API and the
CORS method list in app.py. Nothing is gained by the verb here and a preflight
is avoided.
"""

from flask import Blueprint, g, jsonify, request

import db
import notebook
from answerline import clean_answerline
from auth import require_user

bp = Blueprint("notebook", __name__, url_prefix="/api/notebook")

MAX_FLASHCARDS_PER_SAVE = 50


def _category_filter():
    """The requested shelf, or None for "everything"."""
    value = (request.args.get("category") or "").strip()
    return None if not value or value.lower() == "all" else value


def _int_ids(raw):
    """Coerce a client-supplied id list, or raise ValueError.

    Done up front so a bad value cannot reach a query, and so a list with one
    unusable entry fails loudly instead of silently operating on the rest.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("Select at least one item.")
    return [int(n) for n in raw]


# ------------------------------------------------------------------ shelves ---

@bp.get("/categories")
@require_user
def categories():
    """Every shelf with something on it, and how much.

    One grouped query over the three tables rather than a count per category
    per table -- the notebook hub renders nine or ten tiles, and the loop
    version is thirty round trips that get slower as the notebook fills up.
    Same shape of mistake as the N+1 in the desktop cluster labeller.
    """
    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            """select category,
                      sum(notes)::int      as notes,
                      sum(flashcards)::int as flashcards,
                      sum(clues)::int      as clues
                 from (
                       select category, count(*) as notes, 0 as flashcards, 0 as clues
                         from public.notebook_notes
                        where user_id = %s and category is not null
                     group by category
                      union all
                       select category, 0, count(*), 0
                         from public.flashcards
                        where user_id = %s and category is not null
                     group by category
                      union all
                       select category, 0, 0, count(*)
                         from public.user_clues
                        where user_id = %s and category is not null
                     group by category
                      ) shelves
             group by category
             order by category""",
            (g.user_id, g.user_id, g.user_id)).fetchall()

    return jsonify({"categories": [dict(r) for r in rows]})


# -------------------------------------------------------------------- notes ---

@bp.get("/notes")
@require_user
def list_notes():
    """Notes on one shelf, guides first then newest first.

    Guides lead because they are what the shelf is *for* once it has any --
    a guide is the rolled-up version of the notes under it, and burying it
    under forty per-question entries hides the useful one.
    """
    category = _category_filter()
    subcategory = (request.args.get("subcategory") or "").strip()

    clauses, params = ["user_id = %s"], [g.user_id]
    if category:
        clauses.append("category = %s")
        params.append(category)
    if subcategory:
        clauses.append("subcategory = %s")
        params.append(subcategory)

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"""select id, notes_content, category, subcategory, answer_text,
                       difficulty, title, is_merged, source_question_id, created_at
                  from public.notebook_notes
                 where {' and '.join(clauses)}
              order by is_merged desc, created_at desc""",
            params).fetchall()

    return jsonify({"notes": [dict(r) for r in rows]})


@bp.get("/notes/<int:note_id>")
@require_user
def one_note(note_id):
    with db.user_tx(g.user_id) as conn:
        row = conn.execute(
            """select id, notes_content, category, subcategory, answer_text,
                      difficulty, title, is_merged, source_question_id, created_at
                 from public.notebook_notes
                where id = %s and user_id = %s""",
            (note_id, g.user_id)).fetchone()

    if row is None:
        return jsonify({"error": "No note with that id."}), 404
    return jsonify(dict(row))


@bp.post("/notes")
@require_user
def save_note():
    payload = request.get_json(silent=True) or {}
    content = payload.get("content") or payload.get("notesContent") or ""
    source_question_id = payload.get("sourceQuestionId")

    # .strip(), because "   " and "\n\n" are truthy: a note that exists, takes
    # up a row in the notebook, and shows nothing when it is opened. A note is
    # its content; there is no such thing as a blank one worth keeping.
    if not str(content).strip():
        return jsonify({"error": "A note needs some content."}), 400
    if source_question_id is not None and not isinstance(source_question_id, int):
        return jsonify({"error": "sourceQuestionId must be a question id."}), 400

    answer_text = payload.get("answerText")
    # The reader normally passes the answerline; when it does not, work it out
    # now rather than leaving the note to be titled by its opening sentence.
    if not str(answer_text or "").strip():
        derived = notebook.derive_title_from_content(content)
        answer_text = None if notebook.looks_like_intro_sentence(derived) else derived
    answer_text = clean_answerline(answer_text) or None

    difficulty = payload.get("difficulty")
    if not isinstance(difficulty, int):
        difficulty = None

    with db.user_tx(g.user_id) as conn:
        category, subcategory = notebook.canonical_category(
            conn, source_question_id, payload.get("category"))
        if not category:
            return jsonify({"error": "A category is required."}), 400

        row = conn.execute(
            """insert into public.notebook_notes
                   (user_id, notes_content, category, subcategory, answer_text,
                    difficulty, source_question_id)
               values (%s, %s, %s, %s, %s, %s, %s)
            returning id, category, subcategory, answer_text""",
            (g.user_id, content, category, subcategory, answer_text,
             difficulty, source_question_id)).fetchone()

    return jsonify(dict(row)), 201


@bp.post("/notes/<int:note_id>")
@require_user
def update_note(note_id):
    """Edit a note's text, or rename a guide."""
    payload = request.get_json(silent=True) or {}
    content = payload.get("content")
    title = payload.get("title")

    if content is None and title is None:
        return jsonify({"error": "Nothing to update."}), 400
    if content is not None and not str(content).strip():
        return jsonify({"error": "A note needs some content."}), 400

    sets, params = [], []
    if content is not None:
        sets.append("notes_content = %s")
        params.append(content)
    if title is not None:
        sets.append("title = %s")
        params.append(str(title).strip() or None)

    with db.user_tx(g.user_id) as conn:
        row = conn.execute(
            f"""update public.notebook_notes set {', '.join(sets)}
                 where id = %s and user_id = %s
             returning id""",
            params + [note_id, g.user_id]).fetchone()

    # No row means it is not there or it is not yours, and those are the same
    # answer -- telling one from the other confirms the existence of somebody
    # else's note.
    if row is None:
        return jsonify({"error": "No note with that id."}), 404
    return jsonify({"id": note_id, "updated": True})


@bp.post("/notes/<int:note_id>/delete")
@require_user
def delete_note(note_id):
    with db.user_tx(g.user_id) as conn:
        row = conn.execute(
            "delete from public.notebook_notes where id = %s and user_id = %s "
            "returning id", (note_id, g.user_id)).fetchone()

    if row is None:
        return jsonify({"error": "No note with that id."}), 404
    return jsonify({"id": note_id, "deleted": True})


# ------------------------------------------------------------------- guides ---

@bp.post("/guides")
@require_user
def merge_notes():
    """Roll several per-question notes up into one named study guide.

    Sections are sorted A-Z by answerline so the guide reads as a reference
    list rather than as the order the questions happened to come up in.
    """
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    delete_originals = bool(payload.get("deleteOriginals"))

    if not title:
        return jsonify({"error": "A name for the guide is required."}), 400
    try:
        note_ids = _int_ids(payload.get("noteIds"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid note id in selection."}), 400
    if len(note_ids) < 2:
        return jsonify({"error": "Select at least two notes to merge."}), 400

    with db.user_tx(g.user_id) as conn:
        # `order by id`: without it the row order for `id = any(...)` is
        # whatever Postgres finds convenient, and `sources[0]` below decides
        # which shelf the guide lands on when the selection spans more than
        # one. Merging the same notes twice could file the guide differently
        # each time, for no reason the player could see. Ordering by id makes
        # it the oldest selected note's category, every time.
        rows = conn.execute(
            """select id, notes_content, answer_text, difficulty, category, is_merged
                 from public.notebook_notes
                where id = any(%s) and user_id = %s
             order by id""",
            (note_ids, g.user_id)).fetchall()
        if not rows:
            return jsonify({"error": "None of those notes exist."}), 404

        # Never fold a guide into another guide -- it would duplicate every
        # section the inner one contains.
        sources = [r for r in rows if not r["is_merged"]]
        if len(sources) < 2:
            return jsonify({
                "error": "Select at least two per-question notes "
                         "(existing guides can't be merged)."}), 400

        sections = notebook.sort_guide_sections(
            notebook.build_note_sections(sources, clean_answerline))
        content = f"# {title}\n\n" + notebook.SECTION_RULE.join(sections)

        category, subcategory = notebook.canonical_category(
            conn, None, payload.get("category") or sources[0]["category"])

        guide = conn.execute(
            """insert into public.notebook_notes
                   (user_id, notes_content, category, subcategory, title, is_merged)
               values (%s, %s, %s, %s, %s, true)
            returning id""",
            (g.user_id, content, category, subcategory, title)).fetchone()

        deleted = 0
        if delete_originals:
            # Same transaction as the insert. Two requests here means a guide
            # that never got written and notes that are already gone.
            deleted = len(conn.execute(
                "delete from public.notebook_notes "
                "where id = any(%s) and user_id = %s returning id",
                ([r["id"] for r in sources], g.user_id)).fetchall())

    return jsonify({"id": guide["id"], "title": title, "category": category,
                    "sections": len(sections), "deletedOriginals": deleted}), 201


@bp.post("/guides/<int:guide_id>/append")
@require_user
def append_to_guide(guide_id):
    """Add more per-question notes to a guide that already exists.

    Guides are meant to grow: you find another tossup on the same theme weeks
    later and want it filed with the rest without rebuilding anything. The
    whole guide is re-sorted rather than gaining an unsorted tail, which is
    only possible because a rendered guide can be split back into its sections
    (notebook.split_guide_sections).
    """
    payload = request.get_json(silent=True) or {}
    delete_originals = bool(payload.get("deleteOriginals"))
    try:
        note_ids = _int_ids(payload.get("noteIds"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid note id in selection."}), 400

    with db.user_tx(g.user_id) as conn:
        guide = conn.execute(
            "select id, notes_content, title, is_merged from public.notebook_notes "
            "where id = %s and user_id = %s", (guide_id, g.user_id)).fetchone()
        if guide is None:
            return jsonify({"error": "That guide no longer exists."}), 404
        if not guide["is_merged"]:
            return jsonify({"error": "That note isn't a study guide."}), 400

        rows = conn.execute(
            """select id, notes_content, answer_text, difficulty, is_merged
                 from public.notebook_notes
                where id = any(%s) and user_id = %s and id <> %s""",
            (note_ids, g.user_id, guide_id)).fetchall()
        sources = [r for r in rows if not r["is_merged"]]
        if not sources:
            return jsonify({
                "error": "Select at least one per-question note to add."}), 400

        title_line, existing = notebook.split_guide_sections(guide["notes_content"])
        merged = notebook.sort_guide_sections(
            existing + notebook.build_note_sections(sources, clean_answerline))
        body = notebook.SECTION_RULE.join(merged)
        content = f"{title_line}\n\n{body}" if title_line else body

        conn.execute(
            "update public.notebook_notes set notes_content = %s "
            "where id = %s and user_id = %s", (content, guide_id, g.user_id))

        deleted = 0
        if delete_originals:
            deleted = len(conn.execute(
                "delete from public.notebook_notes "
                "where id = any(%s) and user_id = %s returning id",
                ([r["id"] for r in sources], g.user_id)).fetchall())

    return jsonify({"id": guide_id, "title": guide["title"],
                    "added": len(sources), "sections": len(merged),
                    "deletedOriginals": deleted})


# --------------------------------------------------------------- flashcards ---

@bp.get("/flashcards")
@require_user
def list_flashcards():
    """A shelf's cards, each carrying the tossup it came from.

    The join is what lets the notebook group cards by their source question and
    label each group with an answerline and a difficulty. LEFT JOIN, not JOIN:
    a card whose question id is missing must still appear -- it lands in an
    "other cards" group rather than vanishing from the notebook.

    One join, not a lookup per card. This is the review-queue N+1 in a
    different costume.
    """
    category = _category_filter()
    clauses, params = ["f.user_id = %s"], [g.user_id]
    if category:
        clauses.append("f.category = %s")
        params.append(category)

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"""select f.id, f.term, f.definition, f.category, f.created_at,
                       f.source_question_id, q.difficulty as source_difficulty,
                       q.answer as source_answer, q.subcategory as source_subcategory
                  from public.flashcards f
             left join public.questions q on q.id = f.source_question_id
                 where {' and '.join(clauses)}
              order by f.created_at desc""",
            params).fetchall()

    cards = []
    for row in rows:
        card = dict(row)
        # Packet directives ("[prompt on X]") are noise in a group heading --
        # the same cleanup the note titles get.
        card["source_answer"] = (clean_answerline(row["source_answer"])
                                 if row["source_answer"] else None)
        cards.append(card)

    return jsonify({"flashcards": cards})


@bp.post("/flashcards")
@require_user
def save_flashcards():
    """Save a batch of cards, and say how many did not make it.

    Cards with nothing on one side are dropped. They come from an AI response
    parsed into term/definition pairs, so a truncated generation yields cards
    with a term and no definition -- which get saved, counted, shown and
    exported while teaching nothing. A half-card is not a card.

    The response carries counts rather than a bare success: "saved" over a
    batch that quietly dropped three of eight is the app saying something that
    is not true.
    """
    payload = request.get_json(silent=True) or {}
    raw = payload.get("flashcards")
    source_question_id = payload.get("sourceQuestionId")

    if not isinstance(raw, list) or not raw:
        return jsonify({"error": "No flashcards provided."}), 400
    if len(raw) > MAX_FLASHCARDS_PER_SAVE:
        return jsonify({
            "error": f"Save at most {MAX_FLASHCARDS_PER_SAVE} cards at a time."}), 400
    if source_question_id is not None and not isinstance(source_question_id, int):
        return jsonify({"error": "sourceQuestionId must be a question id."}), 400

    usable = [c for c in raw
              if isinstance(c, dict)
              and str(c.get("term") or "").strip()
              and str(c.get("definition") or "").strip()]
    skipped = len(raw) - len(usable)

    # Every card being blank means the generation failed, rather than that the
    # user asked to save nothing.
    if not usable:
        return jsonify({
            "error": "Those flashcards came back empty. Try generating them again."}), 502

    with db.user_tx(g.user_id) as conn:
        category, _ = notebook.canonical_category(
            conn, source_question_id, payload.get("category"))
        # executemany, not a loop of round trips: a generation is eight to
        # twelve cards and each one is otherwise its own trip to the database.
        conn.cursor().executemany(
            """insert into public.flashcards
                   (user_id, term, definition, category, source_question_id)
               values (%s, %s, %s, %s, %s)""",
            [(g.user_id, c["term"].strip(), c["definition"].strip(),
              category, source_question_id) for c in usable])

    return jsonify({"saved": len(usable), "skipped": skipped,
                    "category": category}), 201


@bp.post("/flashcards/<int:card_id>/delete")
@require_user
def delete_flashcard(card_id):
    with db.user_tx(g.user_id) as conn:
        row = conn.execute(
            "delete from public.flashcards where id = %s and user_id = %s "
            "returning id", (card_id, g.user_id)).fetchone()

    if row is None:
        return jsonify({"error": "No flashcard with that id."}), 404
    return jsonify({"id": card_id, "deleted": True})


@bp.post("/flashcards/delete-all")
@require_user
def delete_all_flashcards():
    """Empty one shelf, or the whole set with an explicit "all".

    The category is matched exactly. The desktop build matches it case-folded
    with underscores turned back into spaces, because its UI sends a slugified
    name ("american_literature") for a value stored display-cased -- a client
    bug worked around in the server. This client sends the stored name, so the
    workaround is not ported; the count comes back either way, which is how a
    mismatch shows up as "deleted 0" instead of as a silent success.
    """
    return _delete_all("flashcards")


# -------------------------------------------------------------------- clues ---

@bp.get("/clues")
@require_user
def list_clues():
    """Saved clues, grouped by the answer they point at."""
    category = _category_filter()
    clauses, params = ["user_id = %s"], [g.user_id]
    if category:
        clauses.append("category = %s")
        params.append(category)

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"""select id, clue_text, answer_text, category,
                       source_question_id, created_at
                  from public.user_clues
                 where {' and '.join(clauses)}
              order by answer_text, created_at""",
            params).fetchall()

    return jsonify({"clues": [dict(r) for r in rows]})


@bp.post("/clues")
@require_user
def save_clue():
    payload = request.get_json(silent=True) or {}
    clue_text = payload.get("clueText") or ""
    source_question_id = payload.get("sourceQuestionId")

    if not str(clue_text).strip():
        return jsonify({"error": "A clue needs some text."}), 400
    if source_question_id is not None and not isinstance(source_question_id, int):
        return jsonify({"error": "sourceQuestionId must be a question id."}), 400

    answer_text = clean_answerline(payload.get("answerText")) or ""

    with db.user_tx(g.user_id) as conn:
        # Resolved off the question row like everything else here. This writer
        # was the one the desktop fix missed, so a clue highlighted during an
        # adaptive session was filed under a subcategory -- and both of its
        # readers match the category exactly, which made the clue invisible to
        # the notebook and to guide generation at once. Saved, and unreachable.
        category, _ = notebook.canonical_category(
            conn, source_question_id, payload.get("category"))

        # The answerline comes off the question too when the client did not
        # send one, and on the web it usually cannot: clues are worth saving
        # the moment you spot them, which is mid-tossup, and the reader is not
        # told the answer until the question has been answered. Deriving it
        # here is what keeps those clues filed under the answer they point at
        # instead of piling up under "Unattributed" -- the grouping is the
        # whole reason saved clues are useful rather than a list of sentences.
        if not answer_text and source_question_id is not None:
            row = conn.execute(
                "select answer from public.questions where id = %s",
                (source_question_id,)).fetchone()
            if row:
                answer_text = clean_answerline(row["answer"]) or ""

        row = conn.execute(
            """insert into public.user_clues
                   (user_id, clue_text, answer_text, category, source_question_id)
               values (%s, %s, %s, %s, %s)
            returning id, category""",
            (g.user_id, str(clue_text).strip(), answer_text, category,
             source_question_id)).fetchone()

    return jsonify(dict(row)), 201


@bp.post("/clues/<int:clue_id>/delete")
@require_user
def delete_clue(clue_id):
    with db.user_tx(g.user_id) as conn:
        row = conn.execute(
            "delete from public.user_clues where id = %s and user_id = %s "
            "returning id", (clue_id, g.user_id)).fetchone()

    if row is None:
        return jsonify({"error": "No clue with that id."}), 404
    return jsonify({"id": clue_id, "deleted": True})


@bp.post("/clues/delete-all")
@require_user
def delete_all_clues():
    return _delete_all("user_clues")


def _delete_all(table):
    """Shared body for the two bulk deletes. See delete_all_flashcards."""
    payload = request.get_json(silent=True) or {}
    category = (payload.get("category") or "").strip()

    if not category:
        # Not defaulted to "everything": a missing field is a client bug, and
        # the cost of guessing wrong here is somebody's whole notebook.
        return jsonify({"error": "A category is required (or \"all\")."}), 400

    clauses, params = ["user_id = %s"], [g.user_id]
    if category.lower() != "all":
        clauses.append("category = %s")
        params.append(category)

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"delete from public.{table} where {' and '.join(clauses)} returning id",
            params).fetchall()

    return jsonify({"deleted": len(rows)})
