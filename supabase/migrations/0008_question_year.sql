-- A year-range filter on the reader (a two-handled slider, like qbreader's).
--
-- `set_year` was already stored and returned; nothing ever filtered on it.
-- The reader's other filters each have an index with `rand_key` trailing, so
-- the random-question query stays an index seek rather than a sort even with
-- the filter applied (see 0001_content.sql's own note on that shape). A year
-- range needs the same treatment or `set_year between $1 and $2 and rand_key
-- >= $3 order by rand_key limit 1` degrades to a scan.
--
-- Kept a plain b-tree on `(set_year, rand_key)` rather than anything cleverer:
-- a `between` on the leading column then an ordered read of the second is
-- exactly what a composite b-tree is for.

create index if not exists questions_year_rand_idx
    on public.questions (set_year, rand_key);
