"""Settings, read once at import and checked loudly.

Everything here comes from the environment. Nothing is defaulted to a working
value, because a secret with a fallback is a secret that ships.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _required(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy web/api/.env.example to web/api/.env "
            "and fill it in.")
    return value


# https://<project-ref>.supabase.co
SUPABASE_URL = _required("SUPABASE_URL").rstrip("/")

# Postgres connection string. Use the *pooler* string in anything that scales
# out -- Supabase's direct connection has a low connection cap and a serverless
# deployment will exhaust it.
DATABASE_URL = _required("DATABASE_URL")

# Optional. Only needed if the project still signs JWTs with the legacy shared
# secret (Project Settings -> API -> JWT Settings). Projects created with
# asymmetric signing keys verify through JWKS and do not need this at all.
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET")

# Browsers that may call this API. A wildcard here would let any page on the
# internet make credentialed calls on a signed-in user's behalf.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

# Encrypts the per-user Gemini keys in `user_secrets` (see secrets_store.py).
# A Fernet key: generate one with
#     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#
# This never goes in Postgres. The whole point of encrypting the column is
# that a leaked database is not a leaked set of everyone's Google billing,
# and storing the key that opens it in the same database gives that up.
#
# Rotating it makes every stored key unreadable, which the API handles by
# behaving as though nobody had set one -- players re-enter theirs. That is a
# real cost of rotating, and it is the correct behaviour: the alternative is
# guessing.
SECRETS_ENCRYPTION_KEY = os.environ.get("SECRETS_ENCRYPTION_KEY")

# There is deliberately no server-wide Gemini key.
#
# Each player brings their own, stored encrypted per account (see
# secrets_store.py) -- the desktop's model, kept on purpose. A server-level
# key read as a *fallback* would quietly undo that: every player whose own key
# was missing would silently be spending the server operator's quota instead,
# and nothing on screen would say so. The failure mode of having no fallback
# is an honest "add your API key to use this"; the failure mode of having one
# is a surprise bill.

JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
JWT_ISSUER = f"{SUPABASE_URL}/auth/v1"
JWT_AUDIENCE = "authenticated"
