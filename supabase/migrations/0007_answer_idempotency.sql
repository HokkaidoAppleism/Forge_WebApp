-- A server-side backstop against the same answer being recorded twice.
--
-- `web/frontend/src/main.js`'s `finish()` already guards against a double
-- click or double Enter -- its own comment there describes the exact bug
-- that guard was written to fix: "the desktop build [without it] wrote two
-- user_stats rows, two review attempts and -10 points for one neg." That
-- comment is proof the failure mode is real, not hypothetical -- and the
-- server has never had a backstop of its own for it. A network-level retry,
-- two tabs signed into the same account mid-buzz, or the client guard simply
-- being wrong some day all bypass a client-only guard.
--
-- Confirmed live: POSTing the identical /api/answers body twice currently
-- writes two user_stats rows (lifetime totals inflate) while review_queue
-- stays correctly deduplicated by its own primary key -- user_stats had no
-- equivalent.
--
-- `client_answer_id` is nullable and the index is partial (`where ... is not
-- null`) so this is backward compatible: a caller that never sends one (an
-- old client, or the desktop's /api/answers/declare, which is a different,
-- self-reported path with its own considerations) is unaffected.

alter table public.user_stats
    add column if not exists client_answer_id uuid;

create unique index if not exists user_stats_client_answer_idx
    on public.user_stats (user_id, client_answer_id)
    where client_answer_id is not null;
