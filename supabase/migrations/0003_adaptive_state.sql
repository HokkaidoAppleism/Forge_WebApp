-- Adaptive Learning, made stateless.
--
-- The desktop build keeps a live `user` object per session in a module-level
-- dict (`user_sessions` in merged_api.py). That cannot work on a web server for
-- three separate reasons, any one of which is fatal:
--
--   * gunicorn runs several worker processes, so the request that answers a
--     question is routinely not the process that served it;
--   * a restart or a redeploy drops every session in progress;
--   * the dict only grows -- one entry per session per user, never evicted.
--
-- The desktop code already half-knows this. `ensure_session()` exists precisely
-- because restarting the backend used to strand every session, and it rebuilds
-- the object from `category_user_state`. The web fix is to stop treating that
-- as the recovery path and make it the only path: never hold the object at all,
-- rebuild it per request, write it back in the same transaction as the answer.
--
-- Two pieces of that object were never persisted, because within one desktop
-- process they never needed to be. Rebuilding every request means they do.

-- The explore branch's skill estimate. recommend_question() takes an epsilon
-- branch about 10% of the time and draws off `random_skill` rather than any
-- cluster's skill; user.update_stats() advances it whenever cluster_id is -1.
-- Unpersisted, every explore question would be drawn off the starting skill
-- forever, so the one branch meant to widen the search would never learn.
alter table public.category_user_state
    add column if not exists random_skill double precision;

-- The last few question ids served, which get_rec_question() excludes so a
-- cluster does not hand back the same tossup twice in a row. It is a
-- deque(maxlen=5) in memory. Unpersisted, every request would start with an
-- empty exclusion list and the repeat-suppression would silently do nothing.
alter table public.category_user_state
    add column if not exists recently_seen jsonb not null default '[]'::jsonb;

-- How many questions this subject has been served, for the session summary.
alter table public.category_user_state
    add column if not exists questions_served integer not null default 0;
