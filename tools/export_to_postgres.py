"""Copy the question set out of the desktop SQLite database into Postgres.

Content only. Nothing in here touches a user's stats, notes or review queue --
those live on the desktop install and belong to whoever is sitting at it, and
there is no sensible way to decide which web account they would become.

For a first load, run the migrations first, then:

    python web/tools/export_to_postgres.py --database-url "postgresql://..."

To refresh an already-loaded table -- add new questions, update cluster
labels after a re-clustering run -- add `--incremental`. That upserts on `id`
instead of a fresh COPY, so every foreign key from user data stays valid and
the live site is never without questions. Six tables reference
`public.questions(id)`, so TRUNCATE is not an option once anyone has played.

The connection string is the one Supabase calls "session pooler" or "direct
connection" (Project Settings -> Database). It must be a role that can write
public.questions, which the anon and authenticated roles deliberately cannot --
use the postgres role for this one-time load and nothing else.

vector_embedding is not copied; see the note at the top of 0001_content.sql.
"""

import argparse
import os
import platform
import sqlite3
import struct
import sys
import time

try:
    import psycopg
except ImportError:
    sys.exit("psycopg is not installed. Run: pip install 'psycopg[binary]'")


QUESTION_COLUMNS = (
    "id", "question", "answer", "category", "subcategory",
    "set_name", "set_year", "packet_number", "question_number",
    "difficulty", "cluster_label",
)


def default_sqlite_path():
    """Where the desktop app keeps the live database on each platform."""
    system = platform.system()
    if system == "Windows":
        return os.path.join(os.environ["APPDATA"], "ForgeQB", "quizbowl.db")
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/ForgeQB/quizbowl.db")
    return os.path.expanduser("~/.config/ForgeQB/quizbowl.db")


def _clean_cluster_label(value):
    """Most rows hold a plain int. A minority (4,003 on the source database
    checked while building this) hold an 8-byte blob instead -- somewhere in
    the clustering pipeline a numpy int64 was written with .tobytes() into
    this column rather than as a normal int, the pattern vector_embedding uses
    on purpose. Recoverable without loss: every blob found was exactly 8 bytes
    and decoded to a value in the same range (-1 to 14) real cluster ids use,
    so it is unpacked as little-endian int64 rather than dropped.
    """
    if isinstance(value, bytes):
        if len(value) == 8:
            return struct.unpack('<q', value)[0]
        return None          # not the known shape; don't guess
    return value


def _clean_text(value):
    """Postgres text cannot hold a NUL byte at all (one real row on the source
    database carries one, mid-answerline) -- COPY aborts the whole batch on
    the first one it meets rather than skipping the row."""
    if isinstance(value, str) and '\x00' in value:
        return value.replace('\x00', '')
    return value


def _clean_row(row):
    """A sqlite question row, cleaned for Postgres, or None if unusable.

    `question` and `answer` are NOT NULL on the Postgres side; a row missing
    either is not a question and is reported rather than aborting the batch.
    """
    if not row[1] or not row[2]:
        return None
    return tuple(_clean_text(v) for v in row[:-1]) + (
        _clean_cluster_label(row[-1]),)


def copy_questions(sqlite_conn, pg_conn, batch=5000):
    total = sqlite_conn.execute("select count(*) from questions").fetchone()[0]
    print(f"questions in sqlite: {total:,}")

    rows = sqlite_conn.execute(
        f"select {', '.join(QUESTION_COLUMNS)} from questions order by id")

    copied = skipped = 0
    started = time.time()
    with pg_conn.cursor() as cur:
        with cur.copy(
            f"copy public.questions ({', '.join(QUESTION_COLUMNS)}) from stdin"
        ) as copy:
            while True:
                chunk = rows.fetchmany(batch)
                if not chunk:
                    break
                for row in chunk:
                    cleaned = _clean_row(row)
                    if cleaned is None:
                        skipped += 1
                        continue
                    copy.write_row(cleaned)
                    copied += 1
                print(f"  {copied:,} / {total:,}", end="\r", flush=True)

    print(f"  {copied:,} copied, {skipped:,} skipped "
          f"(no question or no answer), {time.time() - started:.1f}s")


def upsert_questions(sqlite_conn, pg_conn, batch=5000, merge_span=20000):
    """Add new questions and update changed ones, keyed on id.

    COPY every row into a staging table, then merge it into `public.questions`
    in id-range slices. A per-row `executemany` over a connection to another
    data centre is ~56ms a round trip and 185k of those never finishes; a
    single `INSERT ... SELECT` over all of them hits Supabase's
    `statement_timeout` on the index maintenance. Slicing by id keeps each
    merge statement small and lets progress show.

    `set_year`/`packet_number`/`question_number` are in the update list too,
    not just `cluster_label` -- a re-ingest can correct a field on an old row
    (--repair does exactly that), and this is the only route those reach the
    website.
    """
    total = sqlite_conn.execute("select count(*) from questions").fetchone()[0]
    lo, hi = sqlite_conn.execute("select min(id), max(id) from questions").fetchone()
    print(f"questions in sqlite: {total:,}  (staged upsert on id, {lo:,}..{hi:,})")

    cols = ", ".join(QUESTION_COLUMNS)
    updates = ", ".join(f"{c} = excluded.{c}"
                        for c in QUESTION_COLUMNS if c != "id")
    started = time.time()

    with pg_conn.cursor() as cur:
        # Not ON COMMIT DROP: the merge below is several statements and we do
        # not want the staging table vanishing under an autocommit. Dropped by
        # hand at the end.
        cur.execute("drop table if exists _stage_questions")
        cur.execute("create temporary table _stage_questions "
                    "(like public.questions including defaults)")

        rows = sqlite_conn.execute(f"select {cols} from questions order by id")
        staged = skipped = 0
        with cur.copy(f"copy _stage_questions ({cols}) from stdin") as copy:
            while True:
                chunk = rows.fetchmany(batch)
                if not chunk:
                    break
                for row in chunk:
                    cleaned = _clean_row(row)
                    if cleaned is None:
                        skipped += 1
                        continue
                    copy.write_row(cleaned)
                    staged += 1
                print(f"  staged {staged:,} / {total:,}", end="\r", flush=True)
        print(f"  staged {staged:,}, {skipped:,} skipped; merging in slices...")

        merged = 0
        start = lo
        while start <= hi:
            end = start + merge_span - 1
            cur.execute(
                f"insert into public.questions ({cols}) "
                f"select {cols} from _stage_questions "
                f"where id between %s and %s "
                f"on conflict (id) do update set {updates}",
                (start, end))
            merged += cur.rowcount
            pg_conn.commit()
            print(f"  merged up to id {min(end, hi):,}  ({merged:,} rows)", flush=True)
            start = end + 1

        cur.execute("drop table _stage_questions")
        pg_conn.commit()

    print(f"  {merged:,} rows added or updated, {time.time() - started:.1f}s")


def copy_cluster_labels(sqlite_conn, pg_conn):
    try:
        rows = sqlite_conn.execute(
            "select subcategory, cluster_id, label, source from cluster_labels"
        ).fetchall()
    except sqlite3.OperationalError:
        print("cluster_labels: no such table locally, nothing to copy")
        return

    if not rows:
        print("cluster_labels: empty, nothing to copy")
        return

    with pg_conn.cursor() as cur:
        # Upsert, not COPY: on a refresh the table is not empty, and a
        # re-clustering run legitimately changes a label for an existing
        # (subcategory, cluster_id).
        cur.executemany(
            "insert into public.cluster_labels "
            "(subcategory, cluster_id, label, source) values (%s, %s, %s, %s) "
            "on conflict (subcategory, cluster_id) do update set "
            "label = excluded.label, source = excluded.source",
            rows)
    print(f"cluster_labels: {len(rows):,} upserted")


def report(pg_conn):
    with pg_conn.cursor() as cur:
        for label, sql in (
            ("questions", "select count(*) from public.questions"),
            ("with cluster_label",
             "select count(*) from public.questions where cluster_label is not null"),
            ("cluster_labels", "select count(*) from public.cluster_labels"),
        ):
            print(f"  {label:<20} {cur.execute(sql).fetchone()[0]:,}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--sqlite", default=default_sqlite_path())
    parser.add_argument("--incremental", action="store_true",
                        help="upsert on id instead of a fresh load; use this "
                             "to refresh an already-populated table")
    args = parser.parse_args()

    if not args.database_url:
        sys.exit("Pass --database-url or set DATABASE_URL.")
    if not os.path.exists(args.sqlite):
        sys.exit(f"No SQLite database at {args.sqlite}")

    print(f"reading  {args.sqlite}")
    sqlite_conn = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)

    with psycopg.connect(args.database_url) as pg_conn:
        # The default is 2 min on Supabase; a slice-merge statement should stay
        # well under that, but index maintenance on a big table is exactly the
        # thing that occasionally does not. This is a one-time admin load.
        pg_conn.execute("set statement_timeout = '15min'")

        existing = pg_conn.execute(
            "select count(*) from public.questions").fetchone()[0]

        if args.incremental:
            print(f"public.questions holds {existing:,} rows; upserting.\n")
            upsert_questions(sqlite_conn, pg_conn)
        else:
            if existing:
                sys.exit(f"public.questions already holds {existing:,} rows. "
                         "Pass --incremental to refresh it, or truncate it "
                         "first for a clean reload.")
            copy_questions(sqlite_conn, pg_conn)

        copy_cluster_labels(sqlite_conn, pg_conn)
        pg_conn.commit()

        print("\nafter load:")
        report(pg_conn)

    sqlite_conn.close()


if __name__ == "__main__":
    main()
