"""Downloading the notebook: flashcards as CSV, guides and notes as Markdown.

The desktop's `/exportFlashcards` and `/exportNotes` (plus their
selected-subset variants) already serve this data; what was missing on the
web was never the data, only the browser's download mechanism -- a save-file
dialog isn't a thing a page can open, so the file has to travel as a response
body the frontend turns into a download itself (see `api.js`'s `download()`).

Every query here is scoped to `user_id = %s` on top of RLS, the same rule
`routes/notebook.py` follows for every read: an id in the request is not
permission to read that row, and this is the one place in the app where "that
row" leaves the database as a file.
"""

import csv
import io

from flask import Blueprint, g, jsonify, request

import db
import notebook
from answerline import clean_answerline
from auth import require_user

bp = Blueprint("export", __name__, url_prefix="/api/notebook/export")


def _csv_response(rows, filename):
    buf = io.StringIO()
    writer = csv.writer(buf)
    # "front"/"back", matching Anki's plain two-column import format -- the
    # desktop's own header, kept so a file exported here still imports the
    # same way.
    writer.writerow(["front", "back"])
    writer.writerows(rows)
    return buf.getvalue(), 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }


def _markdown_response(content, filename):
    return content, 200, {
        "Content-Type": "text/markdown; charset=utf-8",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }


def _int_ids(raw):
    if not isinstance(raw, list) or not raw:
        raise ValueError("no ids")
    return [int(n) for n in raw]


# --------------------------------------------------------------- flashcards ---

@bp.get("/flashcards")
@require_user
def export_flashcards():
    """Every flashcard on one shelf, as CSV. `category=all` for the whole set."""
    category = (request.args.get("category") or "").strip()
    if not category:
        return jsonify({"error": "A category is required (or \"all\")."}), 400

    clauses, params = ["user_id = %s"], [g.user_id]
    if category.lower() != "all":
        clauses.append("category = %s")
        params.append(category)

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"select term, definition from public.flashcards "
            f"where {' and '.join(clauses)} order by created_at",
            params).fetchall()

    if not rows:
        return jsonify({"error": f"No flashcards found for \"{category}\"."}), 404

    filename = f"flashcards_{category.replace(' ', '_')}.csv"
    return _csv_response([(r["term"], r["definition"]) for r in rows], filename)


@bp.post("/flashcards")
@require_user
def export_selected_flashcards():
    """Only the flashcards the player picked, as CSV.

    Same reason the notebook UI offers a per-card Save from an AI draft: a
    shelf export is all-or-nothing, and trimming it shouldn't mean deleting
    the cards you meant to keep.
    """
    payload = request.get_json(silent=True) or {}
    try:
        ids = _int_ids(payload.get("ids"))
    except (TypeError, ValueError):
        return jsonify({"error": "Select at least one flashcard."}), 400

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            "select term, definition from public.flashcards "
            "where id = any(%s) and user_id = %s order by created_at",
            (ids, g.user_id)).fetchall()

    if not rows:
        return jsonify({"error": "None of the selected flashcards were found."}), 404

    return _csv_response([(r["term"], r["definition"]) for r in rows],
                          "flashcards_selected.csv")


# -------------------------------------------------------------------- notes ---

_NOTE_COLUMNS = "notes_content, title, answer_text, difficulty, is_merged, created_at"


@bp.get("/notes")
@require_user
def export_notes():
    """Every guide and note on one shelf, as one Markdown document."""
    category = (request.args.get("category") or "").strip()
    if not category:
        return jsonify({"error": "A category is required (or \"all\")."}), 400

    clauses, params = ["user_id = %s"], [g.user_id]
    if category.lower() != "all":
        clauses.append("category = %s")
        params.append(category)

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"select {_NOTE_COLUMNS} from public.notebook_notes "
            f"where {' and '.join(clauses)} "
            f"order by is_merged desc, created_at desc",
            params).fetchall()

    if not rows:
        return jsonify({"error": f"No notes found for \"{category}\"."}), 404

    heading = ("All study guides and notes" if category.lower() == "all"
               else f"{category} — study guides and notes")
    md = notebook.build_export_markdown(rows, heading, clean_answerline)
    filename = f"notes_{category.replace(' ', '_')}.md"
    return _markdown_response(md, filename)


@bp.post("/notes")
@require_user
def export_selected_notes():
    """Only the guides and notes the player ticked, as one Markdown document.

    IDs can span both `is_merged` states in one request -- a guide and a
    per-question note tick the same checkbox in the notebook -- so unlike the
    shelf export above, nothing here filters on it.
    """
    payload = request.get_json(silent=True) or {}
    try:
        ids = _int_ids(payload.get("ids"))
    except (TypeError, ValueError):
        return jsonify({"error": "Select at least one note."}), 400

    with db.user_tx(g.user_id) as conn:
        rows = conn.execute(
            f"select {_NOTE_COLUMNS}, category from public.notebook_notes "
            f"where id = any(%s) and user_id = %s "
            f"order by is_merged desc, created_at desc",
            (ids, g.user_id)).fetchall()

    if not rows:
        return jsonify({"error": "None of the selected notes were found."}), 404

    # A selection happens inside one open shelf in the UI, but nothing
    # enforces that server-side, so a stale id from a shelf the player has
    # since left would otherwise mix subjects into one file with no warning.
    categories = sorted({r["category"] for r in rows if r["category"]})
    heading = (f"{categories[0]} — selected study guides and notes"
               if len(categories) == 1 else "Selected study guides and notes")

    md = notebook.build_export_markdown(rows, heading, clean_answerline)
    return _markdown_response(md, "notes_selected.md")
