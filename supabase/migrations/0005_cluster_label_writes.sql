-- Let signed-in users contribute AI-generated topic-cluster names.
--
-- `cluster_labels` (0001_content.sql) is shared content with a read-only
-- policy, which was right when nothing wrote it. Knowledge Depth changes that:
-- the names come from Gemini, they cost a request to produce, and they are
-- derived entirely from the shared question set -- so one player's naming run
-- is a correct answer for every other player, and caching it per user would
-- mean paying for the same six names once per account forever.
--
-- Insert only, deliberately. There is no update policy and no delete policy,
-- so:
--
--   * a label that already exists cannot be rewritten by a later caller,
--     which is what stops one account replacing everyone else's names -- first
--     correct answer wins, and vandalising the shared table is not reachable
--     through this policy;
--   * `on conflict do nothing` is the only sane way to write it, which is also
--     the raceproof one: two players opening the panel at the same second both
--     insert, one wins, neither errors.
--
-- The `with check` ties a row to a cluster that actually exists in the question
-- set, so this cannot be used to write arbitrary rows into shared content --
-- the (subcategory, cluster_id) pair has to name a real cluster.
--
-- **Only AI names are ever cached.** The fallback label (a few representative
-- answers, used when a player has no Gemini key) is rebuilt per request
-- instead. That is what makes insert-only workable rather than a trap: the
-- desktop caches its fallback with `source='examples'` and then needs an
-- UPDATE path to replace it with a real name later, and a cached fallback that
-- never gets upgraded is a wart this avoids by not writing it down at all.

drop policy if exists cluster_labels_insert on public.cluster_labels;
create policy cluster_labels_insert on public.cluster_labels
    for insert to authenticated
    with check (
        source = 'ai'
        and exists (
            select 1 from public.questions q
             where q.subcategory = cluster_labels.subcategory
               and q.cluster_label = cluster_labels.cluster_id::integer
        )
    );
