"""Copy the question set out of the desktop SQLite database into Postgres.

Content only. Nothing in here touches a user's stats, notes or review queue --
those live on the desktop install and belong to whoever is sitting at it, and
there is no sensible way to decide which web account they would become.

Run the two migrations first, then:

    python web/tools/export_to_postgres.py --database-url "postgresql://..."

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
                    # question and answer are NOT NULL on the Postgres side.
                    # A row missing either is not a question; report it rather
                    # than letting COPY abort the whole load on row 140,000.
                    if not row[1] or not row[2]:
                        skipped += 1
                        continue
                    row = tuple(_clean_text(v) for v in row[:-1]) + (
                        _clean_cluster_label(row[-1]),)
                    copy.write_row(row)
                    copied += 1
                print(f"  {copied:,} / {total:,}", end="\r", flush=True)

    print(f"  {copied:,} copied, {skipped:,} skipped "
          f"(no question or no answer), {time.time() - started:.1f}s")


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
        with cur.copy(
            "copy public.cluster_labels (subcategory, cluster_id, label, source) "
            "from stdin"
        ) as copy:
            for row in rows:
                copy.write_row(row)
    print(f"cluster_labels: {len(rows):,} copied")


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
    args = parser.parse_args()

    if not args.database_url:
        sys.exit("Pass --database-url or set DATABASE_URL.")
    if not os.path.exists(args.sqlite):
        sys.exit(f"No SQLite database at {args.sqlite}")

    print(f"reading  {args.sqlite}")
    sqlite_conn = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)

    with psycopg.connect(args.database_url) as pg_conn:
        existing = pg_conn.execute(
            "select count(*) from public.questions").fetchone()[0]
        if existing:
            sys.exit(f"public.questions already holds {existing:,} rows. "
                     "Truncate it first if you meant to reload.")

        copy_questions(sqlite_conn, pg_conn)
        copy_cluster_labels(sqlite_conn, pg_conn)
        pg_conn.commit()

        print("\nafter load:")
        report(pg_conn)

    sqlite_conn.close()


if __name__ == "__main__":
    main()
