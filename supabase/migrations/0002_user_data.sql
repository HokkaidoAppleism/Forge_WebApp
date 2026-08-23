-- Per-user data. This is the whole multi-user conversion.
--
-- Every table here exists in the desktop build too, where it holds one
-- person's rows and needs no owner column. On the web the owner is the primary
-- key's first component, and row level security -- not application code -- is
-- what stops one account reading another's. Application filtering is still
-- written (see api/db.py), but it is the second lock, not the only one.
--
-- Three primary keys change shape as a direct result:
--
--   review_queue        question_id            -> (user_id, question_id)
--   progress_daily      (day, cat, subcat)     -> (user_id, day, cat, subcat)
--   category_user_state category               -> (user_id, category)
--
-- Each of those was unique per install and is only unique per person now. The
-- desktop app's global `app_settings` table splits the same way: the Learned
-- and Stuck thresholds belong to a player, not to an installation, so it
-- becomes user_settings.

-- ---------------------------------------------------------------- answers ---

create table if not exists public.user_stats (
    id                 bigint generated always as identity primary key,
    user_id            uuid not null references auth.users (id) on delete cascade,
    session_id         text not null,
    question_id        bigint references public.questions (id),
    category           text,
    subcategory        text,
    difficulty         smallint,
    outcome            text not null check (outcome in ('power', 'ten', 'neg', 'pass')),
    celerity           double precision,
    submission_time_ms integer,
    scored_offline     boolean,
    user_answer        text,
    created_at         timestamptz not null default now()
);

create index if not exists user_stats_user_idx     on public.user_stats (user_id, created_at desc);
create index if not exists user_stats_session_idx  on public.user_stats (user_id, session_id);
create index if not exists user_stats_category_idx on public.user_stats (user_id, category);

-- The permanent day-by-day record. Deliberately NOT cleared by "Reset Stats",
-- which only empties user_stats -- a record of how someone has changed over
-- months is the one thing a reset has no business erasing.
--
-- `day` is a date, not a timestamp, and the server decides it from the user's
-- own timezone rather than from now() (see api/routes/answers.py). The desktop
-- build used SQLite's DATE('now','localtime'), which is the machine's clock;
-- on a server that is whatever region it happens to run in, which would file
-- every evening session in the US under the following day.
create table if not exists public.progress_daily (
    user_id      uuid not null references auth.users (id) on delete cascade,
    day          date not null,
    category     text not null default '',
    subcategory  text not null default '',
    answers      integer not null default 0,
    correct      integer not null default 0,
    negs         integer not null default 0,
    points       integer not null default 0,
    celerity_sum double precision not null default 0,
    celerity_n   integer not null default 0,
    primary key (user_id, day, category, subcategory)
);

-- ----------------------------------------------------------------- review ---

create table if not exists public.review_queue (
    user_id        uuid not null references auth.users (id) on delete cascade,
    question_id    bigint not null references public.questions (id),
    source         text not null default 'missed',
    added_at       timestamptz not null default now(),
    attempts       integer not null default 0,
    correct_streak integer not null default 0,
    total_correct  integer not null default 0,
    last_seen      timestamptz,
    learned_at     timestamptz,
    sm2_reps       integer not null default 0,
    sm2_ef         double precision not null default 2.5,
    sm2_interval   integer not null default 0,
    sm2_due        timestamptz,
    primary key (user_id, question_id)
);

-- The drill's own ordering: due questions first, most overdue leading. A null
-- sm2_due reads as "due now", which is how rows added before SM-2 existed
-- migrate without disappearing, so nulls sort first.
create index if not exists review_queue_due_idx
    on public.review_queue (user_id, sm2_due nulls first)
    where learned_at is null;

-- What was actually typed, kept per question for as long as it stays in the
-- queue. Tied to the queue entry's lifetime rather than to user_stats: a
-- Reset Stats empties user_stats and leaves review_queue alone, so a history
-- read from there would vanish while the question stayed in the list.
create table if not exists public.review_answers (
    id          bigint generated always as identity primary key,
    user_id     uuid not null references auth.users (id) on delete cascade,
    question_id bigint not null references public.questions (id),
    user_answer text,
    was_correct boolean not null default false,
    celerity    double precision,
    answered_at timestamptz not null default now()
);

create index if not exists review_answers_lookup_idx
    on public.review_answers (user_id, question_id, answered_at);

-- ------------------------------------------------------------- notebook -----

create table if not exists public.notebook_notes (
    id                 bigint generated always as identity primary key,
    user_id            uuid not null references auth.users (id) on delete cascade,
    notes_content      text not null,
    category           text,
    subcategory        text,
    answer_text        text,
    difficulty         smallint,
    source_question_id bigint references public.questions (id),
    title              text,
    is_merged          boolean not null default false,
    created_at         timestamptz not null default now()
);

create index if not exists notebook_notes_user_idx
    on public.notebook_notes (user_id, category);

create table if not exists public.flashcards (
    id                 bigint generated always as identity primary key,
    user_id            uuid not null references auth.users (id) on delete cascade,
    term               text not null,
    definition         text not null,
    category           text,
    source_question_id bigint references public.questions (id),
    created_at         timestamptz not null default now()
);

create index if not exists flashcards_user_idx
    on public.flashcards (user_id, category);

create table if not exists public.user_clues (
    id                 bigint generated always as identity primary key,
    user_id            uuid not null references auth.users (id) on delete cascade,
    clue_text          text not null,
    answer_text        text not null default '',
    category           text,
    source_question_id bigint references public.questions (id),
    created_at         timestamptz not null default now()
);

create index if not exists user_clues_user_idx
    on public.user_clues (user_id, category);

-- ------------------------------------------------------------- adaptive -----

create table if not exists public.adaptive_sessions (
    id                 bigint generated always as identity primary key,
    user_id            uuid not null references auth.users (id) on delete cascade,
    session_id         text,
    category           text not null,
    questions_answered integer not null default 0,
    correct_answers    integer not null default 0,
    start_difficulty   double precision,
    end_difficulty     double precision,
    started_at         timestamptz,
    ended_at           timestamptz not null default now()
);

create index if not exists adaptive_sessions_user_idx
    on public.adaptive_sessions (user_id, ended_at desc);

-- The recommender's per-cluster skill model, one row per user per subject.
create table if not exists public.category_user_state (
    user_id          uuid not null references auth.users (id) on delete cascade,
    category         text not null,
    user_data        jsonb not null,
    start_difficulty double precision,
    last_updated     timestamptz not null default now(),
    primary key (user_id, category)
);

-- ------------------------------------------------------------- settings -----

create table if not exists public.user_settings (
    user_id    uuid not null references auth.users (id) on delete cascade,
    key        text not null,
    value      text,
    updated_at timestamptz not null default now(),
    primary key (user_id, key)
);

-- ------------------------------------------------------------------ RLS -----

-- One policy shape for every table: you may touch a row if and only if it is
-- yours. `auth.uid()` reads the `sub` claim off the verified JWT that the API
-- puts on the transaction, so a request that never proved who it is has a null
-- uid and matches nothing.
--
-- `with check` is on the write policy as well as `using`. Without it an
-- account could update a row it owns and set user_id to somebody else's on the
-- way past -- the check runs against the row as it will be, not as it was.
do $$
declare
    t text;
begin
    foreach t in array array[
        'user_stats', 'progress_daily', 'review_queue', 'review_answers',
        'notebook_notes', 'flashcards', 'user_clues', 'adaptive_sessions',
        'category_user_state', 'user_settings'
    ]
    loop
        execute format('alter table public.%I enable row level security', t);
        execute format('drop policy if exists %I on public.%I', t || '_owner', t);
        execute format(
            'create policy %I on public.%I for all to authenticated '
            'using (user_id = auth.uid()) with check (user_id = auth.uid())',
            t || '_owner', t);
    end loop;
end
$$;
