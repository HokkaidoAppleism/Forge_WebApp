"""Per-user API keys, encrypted at rest.

Each player brings their own Gemini key, the same model the desktop build
uses -- nobody spends anybody else's quota, and the server never becomes a
single billable account for everyone. What changes on the web is where the
key lives: `config.json` next to a desktop app is one person's key on their
own machine; a table on a server is many people's keys somewhere none of them
can see.

So the key is encrypted before it is written and decrypted only in the
request that is about to call Gemini with it. RLS already keeps one account
out of another's row (0004_user_secrets.sql), and that is the wrong thing to
rely on alone: it protects the row from other *users*, not from a leaked
backup or anyone who ends up holding a copy of the database. Fernet ciphertext
is useless without `SECRETS_ENCRYPTION_KEY`, which lives in the server
environment and never in Postgres, so the two would have to leak together.

**Nothing here returns a decrypted key to a caller outside the API.** `hint()`
is what the settings panel gets: the last four characters, enough to
recognise which key is saved and not enough to reconstruct it. The desktop's
`GET /api/key` hands the whole key back to its renderer, which is fine when
that renderer is a local window and is a credential-disclosure endpoint the
moment it is a web page.
"""

from cryptography.fernet import Fernet, InvalidToken

import config


class SecretsUnavailable(RuntimeError):
    """Raised when the server has no encryption key configured.

    Deliberately fatal to the *request* rather than to startup: an API with no
    SECRETS_ENCRYPTION_KEY can still serve every non-AI route perfectly well,
    and refusing to boot over it would take the whole app down for a feature
    most requests never touch.
    """


def _cipher():
    if not config.SECRETS_ENCRYPTION_KEY:
        raise SecretsUnavailable(
            "This server cannot store API keys: SECRETS_ENCRYPTION_KEY is not set.")
    try:
        return Fernet(config.SECRETS_ENCRYPTION_KEY)
    except (ValueError, TypeError) as e:
        raise SecretsUnavailable(
            "SECRETS_ENCRYPTION_KEY is not a valid Fernet key.") from e


def encrypt(plaintext):
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext):
    """The stored key, or None if it cannot be read.

    None rather than an exception on `InvalidToken`, because the realistic
    cause is a rotated or mistyped `SECRETS_ENCRYPTION_KEY` rather than an
    attack -- and the honest response to "we can no longer read your saved
    key" is to behave exactly as though no key were saved, which every caller
    already handles, rather than to 500.
    """
    if not ciphertext:
        return None
    try:
        return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


def hint(plaintext):
    """The last four characters, for "…f8Ka is saved" in the settings panel."""
    return plaintext[-4:] if plaintext and len(plaintext) >= 4 else "????"


def load_gemini_key(conn, user_id):
    """This user's Gemini key in the clear, or None if they have not set one."""
    row = conn.execute(
        "select gemini_key_cipher from public.user_secrets where user_id = %s",
        (user_id,)).fetchone()
    return decrypt(row["gemini_key_cipher"]) if row else None


def save_gemini_key(conn, user_id, plaintext):
    conn.execute(
        """insert into public.user_secrets
               (user_id, gemini_key_cipher, gemini_key_hint, updated_at)
           values (%s, %s, %s, now())
           on conflict (user_id) do update set
               gemini_key_cipher = excluded.gemini_key_cipher,
               gemini_key_hint   = excluded.gemini_key_hint,
               updated_at        = now()""",
        (user_id, encrypt(plaintext), hint(plaintext)))


def clear_gemini_key(conn, user_id):
    """Returns whether there was anything to clear."""
    return conn.execute(
        "delete from public.user_secrets where user_id = %s returning user_id",
        (user_id,)).fetchone() is not None


def describe_gemini_key(conn, user_id):
    """What the settings panel is allowed to know: that a key exists, and
    which one it is -- never the key itself."""
    row = conn.execute(
        "select gemini_key_hint, updated_at from public.user_secrets "
        "where user_id = %s and gemini_key_cipher is not null",
        (user_id,)).fetchone()
    if row is None:
        return {"configured": False, "hint": None, "updatedAt": None}
    return {
        "configured": True,
        "hint": row["gemini_key_hint"],
        "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
    }
