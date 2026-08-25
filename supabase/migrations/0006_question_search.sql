-- Text search for the question browser (GET /api/questions/browse).
--
-- The browser lets you search 169,056 answerlines by substring. Without an
-- index that is a sequential scan of a 194 MB table: measured at ~1.9 s for a
-- rare term, and -- unlike the desktop's SQLite copy, where narrowing to one
-- category first brought it to single-digit milliseconds -- adding a category
-- filter here did NOT help (~2.4 s), because the category index and the LIKE
-- cannot both be used and the planner still scans what the filter leaves.
--
-- A trigram GIN index is what makes `ilike '%term%'` indexable at all. B-tree
-- indexes only serve anchored prefixes (`'term%'`), which is the wrong shape
-- for this: people search for a word in the middle of an answerline, not the
-- start of one.
--
-- Measured on the real table after this migration: ~1.9 s -> ~60-130 ms,
-- for 23 MB of index (database went 205 MB -> 228 MB, comfortably inside the
-- 500 MB free tier). The desktop build declined the equivalent fix because
-- SQLite's FTS5 would have meant a full rebuild and a few hundred MB; in
-- Postgres it is one index and ten seconds to build, so the tradeoff lands
-- the other way.
--
-- `answer` only, deliberately. Indexing `question` too would cover the body
-- text of every tossup -- far more trigrams, for a search nobody asked for:
-- the browser is for finding "that Zoroastrianism tossup" by its answerline.

create extension if not exists pg_trgm;

create index if not exists questions_answer_trgm_idx
    on public.questions using gin (answer gin_trgm_ops);
