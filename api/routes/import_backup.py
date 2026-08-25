"""One-time import of a desktop account's pre-migration local history.

Before the desktop<->cloud linking effort, every desktop install kept its
progress in a local SQLite file (`%APPDATA%\\ForgeQB\\quizbowl.db`). Answers
logged from that point on write straight to this same Postgres database (see
`forge_backend/cloud.py`), but nothing carried the *older* rows over -- a
player who used the desktop app for months before signing into an account has
that whole history stranded on their machine, invisible to the web app and to
a fresh desktop install on another computer.

This route is the other half of that: `forge_backend` reads its own local
SQLite tables directly (it always could -- they are just unused by the live
code paths now) and POSTs them here, over the same authenticated HTTP path
every other desktop route uses. **This process never touches Postgres
directly and never will**; the desktop backend forwards as the signed-in
user, same as everywhere else in `cloud.py`.

**Runs at most once per account.** Guarded by a `user_settings` row
(`key='local_data_imported'`), checked first and set last, so a re-launch of
the desktop app or a repeated click of the import button is a no-op rather
than a second copy of every row. There is no dual-write period to reconcile
here (the desktop fully cut over, it did not keep writing locally after the
switch), so there is nothing to de-duplicate *within* one run -- only across
runs, which the flag handles.

**A row naming a question this database does not have is dropped, not
rejected.** The local and cloud question sets are known to differ by a
handful of rows (blank answers / placeholders that never made the cloud copy)
-- see `web/tools/export_to_postgres.py`. A foreign key that cannot resolve
would otherwise fail the whole batch for one bad row; skipping just that row
keeps the rest of a genuine account's history from being held hostage by it.
"""

from flask import Blueprint, g, jsonify, request

import db
from auth import require_user

bp = Blueprint("import_backup", __name__, url_prefix="/api/import")

IMPORT_FLAG_KEY = "local_data_imported"


def _valid_question_ids(conn, ids):
    """Which of these ids actually exist in `public.questions`."""
    ids = sorted({int(i) for i in ids if isinstance(i, (int, float)) and i is not None})
    if not ids:
        return set()
    rows = conn.execute(
        "select id from public.questions where id = any(%s)", (ids,)).fetchall()
    return {row["id"] for row in rows}


@bp.get("/local-backup/status")
@require_user
def status():
    with db.user_tx(g.user_id) as conn:
        row = conn.execute(
            "select value from public.user_settings "
            "where user_id = %s and key = %s", (g.user_id, IMPORT_FLAG_KEY)).fetchone()
    return jsonify({"alreadyImported": row is not None})


@bp.post("/local-backup")
@require_user
def import_local_backup():
    payload = request.get_json(silent=True) or {}

    with db.user_tx(g.user_id) as conn:
        already = conn.execute(
            "select 1 from public.user_settings "
            "where user_id = %s and key = %s", (g.user_id, IMPORT_FLAG_KEY)).fetchone()
        if already:
            return jsonify({"alreadyImported": True, "imported": {}})

        user_id = g.user_id
        counts = {}

        # ---------------------------------------------------------- user_stats --
        rows = payload.get("user_stats") or []
        wanted_ids = [r.get("question_id") for r in rows]
        valid_ids = _valid_question_ids(conn, wanted_ids)
        n = 0
        for r in rows:
            qid = r.get("question_id")
            conn.execute(
                """insert into public.user_stats
                       (user_id, session_id, question_id, category, subcategory,
                        difficulty, outcome, celerity, submission_time_ms,
                        scored_offline, user_answer, created_at)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           coalesce(%s::timestamptz, now()))""",
                (user_id, r.get("session_id") or "imported",
                 qid if qid in valid_ids else None,
                 r.get("category"), r.get("subcategory"), r.get("difficulty"),
                 r.get("outcome"), r.get("celerity"), r.get("submission_time_ms"),
                 None if r.get("scored_offline") is None else bool(r.get("scored_offline")),
                 r.get("user_answer"), r.get("timestamp")))
            n += 1
        counts["user_stats"] = n

        # ------------------------------------------------------- review_queue --
        rows = payload.get("review_queue") or []
        valid_ids = _valid_question_ids(conn, [r.get("question_id") for r in rows])
        n = 0
        for r in rows:
            qid = r.get("question_id")
            if qid not in valid_ids:
                continue  # question_id is NOT NULL here; unlike user_stats, skip outright
            conn.execute(
                """insert into public.review_queue
                       (user_id, question_id, source, added_at, attempts,
                        correct_streak, total_correct, last_seen, learned_at,
                        sm2_reps, sm2_ef, sm2_interval, sm2_due)
                   values (%s, %s, %s, coalesce(%s::timestamptz, now()), %s, %s, %s,
                           %s::timestamptz, %s::timestamptz, %s, %s, %s, %s::timestamptz)
                   on conflict (user_id, question_id) do nothing""",
                (user_id, qid, r.get("source") or "missed", r.get("added_at"),
                 r.get("attempts") or 0, r.get("correct_streak") or 0,
                 r.get("total_correct") or 0, r.get("last_seen"), r.get("learned_at"),
                 r.get("sm2_reps") or 0, r.get("sm2_ef") or 2.5,
                 r.get("sm2_interval") or 0, r.get("sm2_due")))
            n += 1
        counts["review_queue"] = n

        # ----------------------------------------------------- review_answers --
        rows = payload.get("review_answers") or []
        valid_ids = _valid_question_ids(conn, [r.get("question_id") for r in rows])
        n = 0
        for r in rows:
            qid = r.get("question_id")
            if qid not in valid_ids:
                continue
            conn.execute(
                """insert into public.review_answers
                       (user_id, question_id, user_answer, was_correct, celerity,
                        answered_at)
                   values (%s, %s, %s, %s, %s, coalesce(%s::timestamptz, now()))""",
                (user_id, qid, r.get("user_answer"), bool(r.get("was_correct")),
                 r.get("celerity"), r.get("answered_at")))
            n += 1
        counts["review_answers"] = n

        # ----------------------------------------------------- notebook_notes --
        rows = payload.get("notebook_notes") or []
        valid_ids = _valid_question_ids(conn, [r.get("source_question_id") for r in rows])
        n = 0
        for r in rows:
            sqid = r.get("source_question_id")
            conn.execute(
                """insert into public.notebook_notes
                       (user_id, notes_content, category, subcategory, answer_text,
                        difficulty, source_question_id, title, is_merged, created_at)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, coalesce(%s::timestamptz, now()))""",
                (user_id, r.get("notes_content") or "", r.get("category"),
                 r.get("subcategory"), r.get("answer_text"), r.get("difficulty"),
                 sqid if sqid in valid_ids else None, r.get("title"),
                 bool(r.get("is_merged")), r.get("timestamp")))
            n += 1
        counts["notebook_notes"] = n

        # ---------------------------------------------------------- flashcards --
        rows = payload.get("flashcards") or []
        valid_ids = _valid_question_ids(conn, [r.get("source_question_id") for r in rows])
        n = 0
        for r in rows:
            sqid = r.get("source_question_id")
            conn.execute(
                """insert into public.flashcards
                       (user_id, term, definition, category, source_question_id,
                        created_at)
                   values (%s, %s, %s, %s, %s, coalesce(%s::timestamptz, now()))""",
                (user_id, r.get("term") or "", r.get("definition") or "",
                 r.get("category"), sqid if sqid in valid_ids else None,
                 r.get("timestamp")))
            n += 1
        counts["flashcards"] = n

        # ---------------------------------------------------------- user_clues --
        rows = payload.get("user_clues") or []
        valid_ids = _valid_question_ids(conn, [r.get("source_question_id") for r in rows])
        n = 0
        for r in rows:
            sqid = r.get("source_question_id")
            conn.execute(
                """insert into public.user_clues
                       (user_id, clue_text, answer_text, category,
                        source_question_id, created_at)
                   values (%s, %s, %s, %s, %s, coalesce(%s::timestamptz, now()))""",
                (user_id, r.get("clue_text") or "", r.get("answer_text") or "",
                 r.get("category"), sqid if sqid in valid_ids else None,
                 r.get("timestamp")))
            n += 1
        counts["user_clues"] = n

        # ------------------------------------------------------ adaptive_sessions --
        rows = payload.get("adaptive_sessions") or []
        n = 0
        for r in rows:
            conn.execute(
                """insert into public.adaptive_sessions
                       (user_id, session_id, category, questions_answered,
                        correct_answers, start_difficulty, end_difficulty,
                        started_at, ended_at)
                   values (%s, %s, %s, %s, %s, %s, %s, %s::timestamptz,
                           coalesce(%s::timestamptz, now()))""",
                (user_id, r.get("session_id"), r.get("category") or "",
                 r.get("questions_answered") or 0, r.get("correct_answers") or 0,
                 r.get("start_difficulty"), r.get("end_difficulty"),
                 r.get("started_at"), r.get("ended_at")))
            n += 1
        counts["adaptive_sessions"] = n

        # ------------------------------------------------- category_user_state --
        # `on conflict do nothing`: this is the one table a post-migration
        # answer could already have written for real (every question served or
        # scored since the cutover updates it -- see routes/answers.py). A
        # stale local snapshot must never overwrite live skill state.
        rows = payload.get("category_user_state") or []
        n = 0
        for r in rows:
            user_data = r.get("user_data")
            if not user_data:
                continue
            conn.execute(
                """insert into public.category_user_state
                       (user_id, category, user_data, start_difficulty, last_updated)
                   values (%s, %s, %s::jsonb, %s, coalesce(%s::timestamptz, now()))
                   on conflict (user_id, category) do nothing""",
                (user_id, r.get("category"), user_data, r.get("start_difficulty"),
                 r.get("last_updated")))
            n += 1
        counts["category_user_state"] = n

        conn.execute(
            """insert into public.user_settings (user_id, key, value)
               values (%s, %s, 'v1')
               on conflict (user_id, key) do update set value = excluded.value""",
            (user_id, IMPORT_FLAG_KEY))

    return jsonify({"alreadyImported": False, "imported": counts})
