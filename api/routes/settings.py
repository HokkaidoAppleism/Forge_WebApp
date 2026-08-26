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
    """Whether this account has a Gemini key saved, and which one -- and, for
    the desktop app specifically, the key itself.

    A web page never gets the plaintext back: anything this endpoint hands
    back, a borrowed session or an XSS bug in *this* app would also ask for,
    so a browser call only ever gets a four-character hint. The desktop's
    `GET /api/key` used to return the stored key straight from its own local
    config file, which is a reasonable thing for a local Electron window to
    do with its own machine's file -- and Aaron asked for that back after the
    key moved to this shared account-wide store, so the account brought to
    desktop shows the key it was typed into on the web, not just a
    confirmation.

    **Read this honestly, not as a real access-control boundary**: the
    `X-ForgeQB-Client: desktop` header is set only by `forge_backend/cloud.py`,
    never by this app's own browser code (`frontend/src/api.js`) -- so it
    stops the *ordinary* web flow from ever seeing the plaintext, which is
    the actual goal. It does **not** stop someone who already holds a valid
    access token from adding that header themselves and reading the key over
    curl; nothing about a bearer token lets the server tell "the compiled
    desktop app" apart from "any HTTP client holding the same token." That is
    the tradeoff Aaron accepted: the key is exactly as protected as the
    account's session token is, no more, the same way any other data behind
    that token already is.
    """
    is_desktop = request.headers.get("X-ForgeQB-Client") == "desktop"
    with db.user_tx(g.user_id) as conn:
        described = secrets_store.describe_gemini_key(conn, g.user_id)
        if is_desktop and described["configured"]:
            described["apiKey"] = secrets_store.load_gemini_key(conn, g.user_id)
        return jsonify(described)


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
