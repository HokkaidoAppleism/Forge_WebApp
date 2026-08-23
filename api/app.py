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

import traceback

from flask import Flask, jsonify
from flask_cors import CORS

import config
import db
from routes.adaptive import bp as adaptive_bp
from routes.ai import bp as ai_bp
from routes.answers import bp as answers_bp
from routes.export import bp as export_bp
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
    CORS(
        app,
        resources={r"/api/*": {"origins": config.CORS_ORIGINS}},
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
                      adaptive_bp, notebook_bp, settings_bp, ai_bp, export_bp):
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
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5002, debug=True)
