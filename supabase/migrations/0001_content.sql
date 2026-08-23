-- Shared content: the question set and the AI-generated names for the
-- recommender's topic clusters. Every user reads the same rows and nobody
-- writes them from the app, so these tables carry no user_id.
--
-- vector_embedding is deliberately absent. It is 277 MB of the 569 MB SQLite
-- file (94,648 rows x 3072 bytes) and no request path reads it -- embeddings
-- exist only so cluster_all.py can re-derive cluster_label offline. Shipping
-- them would put the database over Supabase's 500 MB free tier for a column
-- nothing queries. The clustering pipeline keeps running against the SQLite
-- file; only its output (cluster_label) comes here.

create table if not exists public.questions (
    id              bigint primary key,
    question        text not null,
    answer          text not null,
    category        text,
    subcategory     text,
    set_name        text,
    set_year        integer,
    packet_number   integer,
    question_number integer,
    difficulty      smallint,
    cluster_label   integer,

    -- A fixed random value per row, so "give me a random question" is an index
    -- seek instead of a sort. `order by random()` reads all 169,099 rows every
    -- time; `where rand_key >= $1 order by rand_key limit 1` reads one.
    rand_key        double precision not null default random()
);

-- The reader's filters. Mirrors the three single-column indexes the SQLite
-- build added, each with rand_key trailing so the random pick stays a seek
-- once a filter is applied.
create index if not exists questions_category_rand_idx
    on public.questions (category, rand_key);
create index if not exists questions_subcategory_rand_idx
    on public.questions (subcategory, rand_key);
create index if not exists questions_difficulty_rand_idx
    on public.questions (difficulty, rand_key);
create index if not exists questions_rand_idx
    on public.questions (rand_key);

-- Adaptive Learning's lookup, which filters on all three at once. Column order
-- is load bearing and is the same lesson as idx_questions_rec in the SQLite
-- build: the two equality columns lead, the difficulty range comes last, or
-- the index can only be used up to the range column.
create index if not exists questions_rec_idx
    on public.questions (subcategory, cluster_label, difficulty, rand_key);

create table if not exists public.cluster_labels (
    subcategory text not null,
    cluster_id  text not null,
    label       text not null,
    source      text not null default 'ai',
    created_at  timestamptz not null default now(),
    primary key (subcategory, cluster_id)
);

-- RLS is on even though there is nothing private here. A table with RLS
-- disabled is readable by anyone holding the anon key, and leaving one table
-- open teaches the habit of leaving them open. Read is granted explicitly to
-- signed-in users; no policy grants insert, update or delete, so the app's own
-- credentials cannot rewrite the question set even by accident.
alter table public.questions      enable row level security;
alter table public.cluster_labels enable row level security;

drop policy if exists questions_read on public.questions;
create policy questions_read on public.questions
    for select to authenticated using (true);

drop policy if exists cluster_labels_read on public.cluster_labels;
create policy cluster_labels_read on public.cluster_labels
    for select to authenticated using (true);
