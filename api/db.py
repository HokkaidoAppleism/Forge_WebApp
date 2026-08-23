"""Database access, always inside a transaction that knows whose it is.

Two things happen on every request that touches user data, and the second one
is the one worth reading twice:

  1. The connection switches to the `authenticated` role for the duration of
     the transaction. The role the pool logs in as can see every row in every
     table; `authenticated` sees only what a policy lets it. Without this line
     the policies in 0002_user_data.sql are written, enabled, and never
     consulted -- which is indistinguishable from working right up until the
     day a query forgets its WHERE clause.

  2. The verified user id is put on the transaction as `request.jwt.claims`,
     which is where Supabase's `auth.uid()` reads from. That is what the
     policies compare each row against.

Both are transaction-local (`set_config(..., true)`), so they unwind when the
transaction ends and cannot leak onto the next request that borrows the same
pooled connection.

Application code still writes `where user_id = %s` on top of this. The belt is
RLS; the braces are the WHERE clause. Either alone would be enough on a good
day, and the point is not to depend on a good day.
"""

import json
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import config

pool = ConnectionPool(
    config.DATABASE_URL,
    min_size=1,
    max_size=8,
    kwargs={"row_factory": dict_row},
    open=False,
)


def open_pool():
    pool.open()
    pool.wait(timeout=15)


def close_pool():
    pool.close()


@contextmanager
def user_tx(user_id):
    """A transaction scoped to one user, with RLS actively enforcing it."""
    with pool.connection() as conn:
        with conn.transaction():
            # Claims first, role second: after the role switch this connection
            # is deliberately less privileged, so do the setup while it still
            # has the privileges to do it.
            conn.execute(
                "select set_config('request.jwt.claims', %s, true)",
                (json.dumps({"sub": user_id, "role": "authenticated"}),),
            )
            conn.execute("select set_config('role', 'authenticated', true)")
            yield conn


@contextmanager
def content_tx():
    """A transaction for the shared question set.

    Still runs as `authenticated` rather than as the pool's own role. The
    question tables grant select and nothing else, so a bug in a content query
    cannot write to them however it is worded.
    """
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute("select set_config('role', 'authenticated', true)")
            yield conn
