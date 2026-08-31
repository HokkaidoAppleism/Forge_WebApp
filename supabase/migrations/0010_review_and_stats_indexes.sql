-- Three indexes for the queries that turned out to be the load-time cost
-- centers once request-count and payload-size fixes were exhausted elsewhere:
-- what's left is the database work itself.

-- --------------------------------------------------------- review paging ---

-- `/api/review/queue` (the desktop's whole-queue walk behind /reviewQueue and
-- /browseQuestions, and the web's Missed Questions list) reads:
--
--     where rq.user_id = %s ... order by rq.added_at desc limit %s offset %s
--
-- Nothing indexes `added_at`. The primary key leads with `question_id`, and
-- the partial `review_queue_due_idx` (below) leads with `sm2_due` under a
-- `learned_at is null` filter that a `status=all` page doesn't even apply --
-- so this order by forces a full read of the user's queue and a sort on every
-- single page, for every desktop browse-page turn and every web Missed
-- Questions open.
create index if not exists review_queue_added_idx
    on public.review_queue (user_id, added_at desc);

-- ------------------------------------------------------- what's due next ---

-- `/api/review/next` used to order by `is_due desc, sm2_due asc nulls first,
-- last_seen asc nulls first, question_id` -- see that route's own updated
-- docstring for the proof that `is_due desc` is redundant (it is a computed
-- boolean that is true exactly when `sm2_due is null or sm2_due <= now()`,
-- and ascending order already groups every such row ahead of every later one
-- in the same relative order). The route no longer sorts on it; this widens
-- the index to match the ORDER BY that's actually left, so the row this route
-- serves comes from an index scan that stops at the first match instead of a
-- sort over the user's whole unlearned queue -- run on every single tossup
-- served in Adaptive Learning's review mode and the Missed Questions reader.
drop index if exists review_queue_due_idx;
create index if not exists review_queue_due_idx
    on public.review_queue (user_id, sm2_due nulls first, last_seen nulls first, question_id)
    where learned_at is null;

-- --------------------------------------------------------- stats filters ---

-- Seven of the nine profile stat panels filter through `_scope()`'s
--
--     and (category = %s or subcategory = %s)
--
-- against `user_stats`. `user_stats_category_idx (user_id, category)` covers
-- half the OR; nothing covers `subcategory`, so the OR can't become a
-- BitmapOr and the planner scans the user's whole answer history instead --
-- once per panel, on every category- or subcategory-filtered profile view.
create index if not exists user_stats_subcategory_idx
    on public.user_stats (user_id, subcategory);
