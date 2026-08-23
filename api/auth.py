"""Who is calling, decided from a signature and nothing else.

The single rule this module exists to enforce: **the caller's identity comes
from the verified token, never from the request**. No route reads a user id out
of a JSON body, a query string or a header, because any of those can be typed
by hand. `g.user_id` is set here, from the `sub` claim of a JWT whose signature
Supabase's own key material has vouched for, and that is the only user id the
rest of the API can see.

That sounds obvious and it is the most commonly skipped step in a hand-rolled
API: an endpoint that accepts `{"userId": ...}` alongside a valid login is a
complete authorisation bypass, and it looks like ordinary working code in a
review because every request the app itself makes sends the right value.

Supabase signs access tokens one of two ways depending on the project's age:

  * asymmetric (ES256 / RS256) -- the current default. The public half is
    published at /auth/v1/.well-known/jwks.json, so verification needs no
    secret at all and the private key never leaves Supabase.
  * HS256 with a shared secret -- the legacy scheme. Verifying means holding a
    key that can also *mint* tokens, which is why it is worth migrating off.

Both are handled. The algorithm is taken from the token header only to choose
the code path; it is never trusted as permission to skip verification.
"""

import functools

import jwt
from flask import g, jsonify, request
from jwt import PyJWKClient

import config

_jwks_client = PyJWKClient(config.JWKS_URL, cache_keys=True, lifespan=600)

_ASYMMETRIC = ("ES256", "RS256")

# Supabase stamps `iat` from its own clock and this server checks it against
# its own, so the two have to agree to the second or a token is rejected as
# "not yet valid" the instant it is minted. They do not agree to the second:
# this was found with the machine running the API exactly **one second**
# behind, which failed every sign-in with "Could not verify that session" --
# a message that points at the token when the fault is the clock.
#
# Thirty seconds either way, which is what the JWT spec means by "a small
# leeway to account for clock skew". The cost is that an expired token stays
# usable for thirty seconds past `exp`; the alternative is an API that stops
# working whenever the host drifts, which is not a security property, just an
# outage. Real protection against a stolen token is its one-hour lifetime and
# RLS, not the last half-minute of it.
CLOCK_SKEW_SECONDS = 30


class AuthError(Exception):
    def __init__(self, message, status=401):
        super().__init__(message)
        self.message = message
        self.status = status


def _bearer_token():
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Sign in to use this.")
    return token.strip()


def _decode(token):
    try:
        alg = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError:
        raise AuthError("That session token is malformed.")

    if alg in _ASYMMETRIC:
        try:
            key = _jwks_client.get_signing_key_from_jwt(token).key
        except Exception:
            # A key rotation the cache has not caught up with, or Supabase
            # being unreachable. Either way this request cannot be trusted.
            raise AuthError("Could not verify that session. Try signing in again.")
        algorithms = list(_ASYMMETRIC)
    elif alg == "HS256":
        if not config.SUPABASE_JWT_SECRET:
            raise AuthError(
                "This token is HS256-signed but SUPABASE_JWT_SECRET is not set.",
                status=500)
        key = config.SUPABASE_JWT_SECRET
        algorithms = ["HS256"]
    else:
        # Includes alg "none", which is the classic forged-token trick.
        raise AuthError("Unsupported token signature.")

    try:
        return jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=config.JWT_AUDIENCE,
            issuer=config.JWT_ISSUER,
            options={"require": ["exp", "sub", "aud", "iss"]},
            leeway=CLOCK_SKEW_SECONDS,
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("Your session expired. Sign in again.")
    except jwt.PyJWTError:
        raise AuthError("Could not verify that session. Try signing in again.")


def require_user(view):
    """Gate a route on a valid session and publish the caller as g.user_id."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        try:
            claims = _decode(_bearer_token())
        except AuthError as e:
            return jsonify({"error": e.message}), e.status

        # Supabase issues these for signed-in users; anon-key tokens carry
        # role "anon" and must not reach a per-user table.
        if claims.get("role") != "authenticated":
            return jsonify({"error": "Sign in to use this."}), 401

        g.user_id = claims["sub"]
        g.user_email = claims.get("email")
        return view(*args, **kwargs)

    return wrapped
