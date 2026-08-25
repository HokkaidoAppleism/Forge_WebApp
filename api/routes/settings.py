"""The Review Settings panel: read and write the three review thresholds.

The desktop keeps these in `app_settings`, a table with one row per key and no
owner, because a desktop database has exactly one player in it. Here they are
`user_settings` rows keyed by `(user_id, key)` -- so the write is an upsert
against the caller's own rows, and RLS makes a request for somebody else's
thresholds impossible rather than merely unlikely.

The clamping and the defaults live in `review_settings.py` next to the code
that *uses* the numbers, so the panel and the scorer cannot end up disagreeing
about what "learned" means.
"""

from flask import Blueprint, g, jsonify, request

import ai
import db
import review_settings
import secrets_store
from auth import require_user

bp = Blueprint("settings", __name__, url_prefix="/api/settings")


@bp.get("/review")
@require_user
def read_review():
    with db.user_tx(g.user_id) as conn:
        return jsonify(review_settings.load(conn, g.user_id))


@bp.post("/review")
@require_user
def write_review():
    settings, error = review_settings.clamp(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400

    with db.user_tx(g.user_id) as conn:
        # One statement for all three, not a round trip each: the keys and the
        # values arrive as two arrays and Postgres zips them into rows.
        # `excluded` is the row that was proposed, so this reads "write it, or
        # overwrite mine".
        conn.execute(
            """insert into public.user_settings (user_id, key, value)
               select %s, k, v from unnest(%s::text[], %s::text[]) as pair(k, v)
               on conflict (user_id, key)
               do update set value = excluded.value, updated_at = now()""",
            (g.user_id, list(settings), [str(v) for v in settings.values()]))

    # Echo what was stored rather than what was sent: the clamp may have moved
    # it, and a panel that keeps showing the rejected number looks saved.
    return jsonify({"saved": True, **settings})


@bp.get("/username")
@require_user
def read_username():
    with db.user_tx(g.user_id) as conn:
        row = conn.execute(
            "select value from public.user_settings "
            "where user_id = %s and key = 'username'", (g.user_id,)).fetchone()
    return jsonify({"username": row["value"] if row else None})


@bp.post("/username")
@require_user
def write_username():
    """Set or clear the display name shown instead of the account's email.

    An empty name clears it rather than storing one -- there is no "unset"
    value for a text column, and a stray empty-string row would still read as
    "has a username" to `read_username` above.
    """
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    if len(username) > 32:
        return jsonify({"error": "Usernames are at most 32 characters."}), 400
    if any(ord(c) < 32 for c in username):
        return jsonify({"error": "Usernames can't contain control characters."}), 400

    with db.user_tx(g.user_id) as conn:
        if username:
            conn.execute(
                """insert into public.user_settings (user_id, key, value)
                   values (%s, 'username', %s)
                   on conflict (user_id, key)
                   do update set value = excluded.value, updated_at = now()""",
                (g.user_id, username))
        else:
            conn.execute(
                "delete from public.user_settings "
                "where user_id = %s and key = 'username'", (g.user_id,))

    return jsonify({"username": username or None})


@bp.get("/ai-key")
@require_user
def read_ai_key():
    """Whether this account has a Gemini key saved, and which one -- never the
    key itself.

    The desktop's `GET /api/key` returns the stored key outright. That is
    reasonable when the caller is a local Electron window reading its own
    machine's config file, and it is a credential-disclosure endpoint the
    moment the caller is a web page: anything the API will hand back, a
    borrowed session or an XSS bug will also ask for. So this returns a
    four-character hint, which is enough to recognise a key and not enough to
    use one.
    """
    with db.user_tx(g.user_id) as conn:
        return jsonify(secrets_store.describe_gemini_key(conn, g.user_id))


@bp.post("/ai-key")
@require_user
def write_ai_key():
    """Save this account's own Gemini key, after checking it actually works.

    Verified against Google before it is stored, rather than accepting it and
    failing later inside whichever AI feature the player tried first. A key
    that is wrong shows up here, next to the box they typed it into, instead
    of as "study guide generation failed" a day later.
    """
    payload = request.get_json(silent=True) or {}
    key = (payload.get("apiKey") or "").strip()
    if not key:
        return jsonify({"error": "Paste your Gemini API key."}), 400
    if len(key) > 400:
        return jsonify({"error": "That does not look like an API key."}), 400

    working, problem = ai.verify_key(key)
    if not working:
        return jsonify({"error": problem}), 400

    try:
        with db.user_tx(g.user_id) as conn:
            secrets_store.save_gemini_key(conn, g.user_id, key)
            saved = secrets_store.describe_gemini_key(conn, g.user_id)
    except secrets_store.SecretsUnavailable as e:
        # The server cannot encrypt, so it must not store. Storing in the
        # clear "just this once" is how a plaintext key column happens.
        return jsonify({"error": str(e)}), 503

    return jsonify({"saved": True, **saved})


@bp.post("/ai-key/delete")
@require_user
def delete_ai_key():
    with db.user_tx(g.user_id) as conn:
        removed = secrets_store.clear_gemini_key(conn, g.user_id)
    return jsonify({"removed": removed, "configured": False, "hint": None})
