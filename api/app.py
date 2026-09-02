"""The web API.

Structured as an app factory plus one blueprint per area, rather than as the
3,316-line single module the desktop backend grew into. That file works, and it
is also the reason a route can quietly stop being called for a fortnight
without anyone noticing -- there is nowhere for a route to be missing *from*.
Here, `routes/review.py` either has an endpoint or it does not.

Run it in development with:

    cd web/api
    python app.py

and in production behind a real WSGI server (`gunicorn 'app:create_app()'`),
never with Flask's own, which is single-threaded and says so on startup.
"""

import threading
import traceback

from flask import Flask, jsonify
from flask_cors import CORS

import adaptive
import config
import db
from routes import questions as questions_routes
from routes import stats as stats_routes
from routes.adaptive import bp as adaptive_bp
from routes.ai import bp as ai_bp
from routes.answers import bp as answers_bp
from routes.export import bp as export_bp
from routes.import_backup import bp as import_backup_bp
from routes.notebook import bp as notebook_bp
from routes.questions import bp as questions_bp
from routes.review import bp as review_bp
from routes.settings import bp as settings_bp
from routes.stats import bp as stats_bp


def create_app():
    app = Flask(__name__)

    # Named origins only. A wildcard here would let any page on the internet
    # call this API with a signed-in user's token if it could get hold of one,
    # and "*" is not permitted alongside credentials anyway.
    #
    # "null" is added unconditionally, not through CORS_ORIGINS: it is what
    # Chromium sends as the literal `Origin` header for a page loaded over
    # `file://`, which is how the Electron desktop client loads its window --
    # an application fact, not a per-deployment setting, so it does not belong
    # in a config value someone has to remember to add per environment.
    #
    # This is a real widening, worth spelling out rather than adding quietly:
    # "null" is not unique to Electron. A sandboxed iframe, a redirect chain,
    # or a data: URI can all send the same literal origin, so this line also
    # accepts requests from any of those, anywhere. What keeps that bounded is
    # that CORS here only gates whether a browser's JS may *read* a response --
    # it is not this API's auth boundary. Every route still requires a valid
    # Supabase bearer token (auth.py's @require_user), carried in a header,
    # never a cookie, so a page that can send a null-origin request still
    # cannot do anything without a token it has no way to obtain. Widening
    # named-origin CORS is a real loosening; it is an acceptable one only
    # because authorization never lived in CORS to begin with.
    CORS(
        app,
        resources={r"/api/*": {"origins": config.CORS_ORIGINS + ["null"]}},
        allow_headers=["Authorization", "Content-Type"],
        methods=["GET", "POST", "OPTIONS"],
        # A cross-origin fetch hides every response header from the caller's
        # JS by default except a fixed "safe" set -- Content-Disposition is
        # not on it. Without this, api.js's download() has a response body
        # but no filename to save it under; see routes/export.py.
        expose_headers=["Content-Disposition"],
        max_age=3600,
    )

    for blueprint in (questions_bp, answers_bp, review_bp, stats_bp,
                      adaptive_bp, notebook_bp, settings_bp, ai_bp, export_bp,
                      import_backup_bp):
        app.register_blueprint(blueprint)

    @app.get("/api/health")
    def health():
        """Liveness, and nothing about the deployment.

        No version string, no database host, no library list -- a health check
        is the one endpoint guaranteed to be reachable without signing in, so
        whatever it prints is public.
        """
        return jsonify({"ok": True})

    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"error": "No such endpoint."}), 404

    @app.errorhandler(Exception)
    def unhandled(error):
        # The message goes to the log; the client gets a sentence. A stack
        # trace in a JSON response tells an attacker the file layout, the
        # library versions and often the SQL.
        traceback.print_exc()
        return jsonify({"error": "Something went wrong on our end."}), 500

    db.open_pool()
    _warm_catalogue()
    return app


def _warm_catalogue():
    """Pay for a few expensive, shared, read-only queries at boot, not on
    whichever player's click happens to land first.

    `adaptive.category_groups` groups all ~185k question rows by
    (category, subcategory), and no index covers that pair -- Postgres sorts the
    whole table and spills to disk (measured: `external merge Disk: 6648kB`,
    ~10s cold, ~2s warm). It is memoised per process, so only one request ever
    pays it; the problem is *which* request. That request is whoever opens
    Adaptive Learning first after a deploy or a container wake, and they spend
    it looking at an empty subject picker.

    `stats.tier_labels` has the identical shape and was NOT warmed here
    originally -- found only because Ceiling, the one panel that calls it,
    kept lagging visibly behind every other stat panel across a session with
    several redeploys in a row, each one resetting its memo. Same fix, same
    reasoning: a sequential scan and sort of the whole `questions` table,
    cached per process, landing on whoever opens Ceiling first instead of at
    boot.

    Warming both here moves that cost to boot, where it overlaps with signing
    in and loading the reader instead of landing on a click. `category_groups`
    calls `cluster_counts` itself, so this fills three memos off two calls.

    On a daemon thread rather than inline: the Procfile allows a worker 30
    seconds to boot, and blocking that long on a query whose connection the
    pool may not have established yet is how a slow database turns a boot into
    a crash loop. A failure here is logged and dropped -- the request path
    still computes each of these on demand exactly as it did before, so the
    worst case is the old behaviour rather than a broken endpoint.

    `questions.filter_tree` is the same shape again, and the highest-traffic
    of the four: it fills the reader's own category/subcategory boxes, so its
    cold cost used to land on the *first sign-in* after a deploy, not a click
    into some specific panel. It shares migration 0009's index with
    `category_groups` above (both group on (category, subcategory)), so it
    may already be answering quickly -- warmed here anyway, since a cold
    first query beats no query even with an index behind it.
    `questions.year_bounds` rides along; it is one min/max, cheap on its own,
    but `/api/questions/filters` calls it right next to `filter_tree` and
    there is no reason to leave one half of that route's own cost unwarmed.

    The real fix is an index for each -- (category, subcategory) already has
    one (supabase/migrations/0009_category_subcategory.sql), (difficulty,
    set_name) is migration 0011. Both stay useful after they land, because a
    cold first query is still slower than no query, and neither migration is
    guaranteed to have been applied in every environment this boots in.
    """
    def warm():
        try:
            with db.content_tx() as conn:
                adaptive.category_groups(conn)
        except Exception:
            traceback.print_exc()
        try:
            with db.content_tx() as conn:
                stats_routes.tier_labels(conn)
        except Exception:
            traceback.print_exc()
        try:
            # Unlike the two above, these open their own connection each --
            # no conn parameter to pass.
            questions_routes.filter_tree()
            questions_routes.year_bounds()
        except Exception:
            traceback.print_exc()

    threading.Thread(target=warm, name="warm-catalogue", daemon=True).start()


# A plain module-level WSGI object, not just the factory above -- gunicorn's
# `--factory` flag (what the Procfile used to invoke this with) isn't
# supported by every gunicorn version, and the deployed one didn't have it:
# every request 502'd because the process never actually started. `app:app`
# works on every version there is, so that's what the Procfile calls now.
app = create_app()

if __name__ == "__main__":
    # threaded=True matters, not just tidiness: the reader fires two requests
    # on load, and Flask's dev server is single-threaded by default -- under
    # that default the second request intermittently fails outright rather
    # than queuing, which reads as a flaky frontend when it's actually this.
    app.run(host="127.0.0.1", port=5002, debug=True, threaded=True)
