-- Per-user Gemini API keys.
--
-- The desktop build asks each player for their own key and keeps it in
-- `config.json` next to the app -- one machine, one user, a plain file. The
-- web port keeps the same model (each player brings their own key, so nobody
-- is spending anybody else's quota) but a server holding *many* people's keys
-- is a different kind of object than a config file holding one, and this
-- table is written accordingly.
--
-- **The key is stored encrypted, not as text.** RLS below already stops one
-- account reading another's row, and that is the wrong thing to rely on
-- alone: it protects the row from other *users*, not from a leaked backup, a
-- misconfigured read replica, or anyone who ends up holding a copy of the
-- database. The ciphertext here is useless without SECRETS_ENCRYPTION_KEY,
-- which lives in the server's environment and never in Postgres -- so a
-- database compromise alone does not hand over anybody's Google billing.
--
-- `key_hint` is the last four characters, in the clear on purpose. It is what
-- lets the settings panel say "…f8Ka is saved" so a player can tell which key
-- they entered without the server ever showing it back to them. Four
-- characters is not enough to reconstruct a key and is enough to recognise
-- one.
--
-- Note the deliberate omission: there is no column holding the key in a form
-- the API can return to a browser, and no endpoint does. The desktop's
-- `GET /api/key` hands the stored key straight back to its renderer, which is
-- reasonable when the renderer and the config file are on the same machine
-- and is a credential-disclosure endpoint the moment that renderer is a web
-- page.

create table if not exists public.user_secrets (
    user_id             uuid primary key references auth.users (id) on delete cascade,
    gemini_key_cipher   text,
    gemini_key_hint     text,
    updated_at          timestamptz not null default now()
);

-- Same policy shape as every other per-user table (see 0002_user_data.sql):
-- you may touch a row if and only if it is yours. The frontend holds the
-- anon key and can reach PostgREST directly, so this is what stands between
-- one account and another's row -- and what it would hand over on a bad day
-- is ciphertext.
alter table public.user_secrets enable row level security;
drop policy if exists user_secrets_owner on public.user_secrets;
create policy user_secrets_owner on public.user_secrets
    for all to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());
