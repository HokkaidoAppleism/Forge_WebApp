# ForgeQB web port — handoff

Paste this whole file as your first message in a new session to pick up where
this one left off.

**Multiple sessions have worked on this concurrently, in parallel, on the
same files.** That is apparently normal for this project now, not a mistake.
If you're picking this up, expect the filesystem to be ahead of whatever any
one summary says — check before trusting a claim, including this one.

## What this is

Arjun (owner of the Forge_Group / ForgeQB desktop app) asked for the app as a
website: Supabase + Postgres, careful about auth, the database N+1 problem,
and frontend/backend structure. This is that port, in `web/` inside the
desktop repo (`C:\Users\aron7\OneDrive\Desktop\Forge_Group`). Desktop code
(`forge_backend/`, `electron-app/`) is untouched.

**Read `CHANGES.md` §5 ("Web port") in full before doing anything.** It is
the authoritative, detailed changelog — every feature, every bug found, how
each was verified. This file is a map to that, not a replacement.

## State as of 2026-08-23 — everything below actually run, not just written

Against Aaron's real Supabase project (`fnlrmalglfrooxuzaywm`), with real
throwaway accounts, real HTTP requests, and real browser clicks. Test
accounts and their rows are deleted after each session — the project should
currently hold exactly Aaron's one real account. **Keep that true.**

**Done:**
1. Schema (4 migrations) + 169,056 real questions loaded, two upstream data
   bugs found and fixed in the loader.
2. The core practice loop — buzz, server-side scoring, review queue, SM-2.
3. Adaptive Learning, rebuilt stateless (the desktop's in-memory-dict design
   can't survive a real web server; this rebuilds skill state from the
   database on every request instead).
4. The notebook — guides, notes, clues, flashcards. Full CRUD, backend and
   frontend.
5. 5 of 6 stats panels, drawn as real SVG charts instead of the desktop's
   images.
6. The real ForgeQB UI, ported from the desktop's HTML/CSS.
7. Records and Review Settings pages (the two header icons that had nothing
   behind them).
8. `POST /api/stats/reset` — Reset All Stats, now a real endpoint.
9. The review list as its own page.
10. **AI features — 3 of 4.** Per-user encrypted Gemini keys (each account
    brings its own — verified: plaintext never touches an API response,
    never stored unencrypted, RLS-isolated). Explanations, flashcard
    generation, and study-guide generation are all built and verified
    end-to-end through the real UI. **Knowledge Depth (cluster naming) is
    the one AI feature not built** — deliberately: it needs its own
    per-cluster-accuracy panel first (nothing in `panels.py` computes that
    yet), and naming clusters nobody can see the accuracy of yet would be
    building the easy half of a feature before the hard half exists.

**Not done:** Knowledge Depth (see above), export (CSV/Anki/Markdown
downloads — data is served, download mechanism isn't), and picking somewhere
to actually deploy.

## Two infrastructure bugs worth knowing before you start a dev server

Found this session, both make "it's definitely running but nothing works"
happen for reasons that have nothing to do with your code:

1. **Flask's dev server is single-threaded by default.** The reader fires
   two requests on load; under `threaded=False` (the default) the second one
   intermittently fails outright rather than queuing. Always start it with
   `threaded=True`:
   ```bash
   python -c "from app import create_app; create_app().run(host='127.0.0.1', port=PORT, debug=False, threaded=True)"
   ```
2. **`localhost` and `127.0.0.1` are not interchangeable.** Flask bound to
   `host='127.0.0.1'` does not listen on `::1`, and this machine's browser
   resolves `localhost` to `::1` first — so `VITE_API_URL=http://localhost:PORT`
   silently produces "Failed to fetch" on every request even though the
   server is up and correctly configured. **Use `127.0.0.1` in
   `VITE_API_URL`, not `localhost`.**

## Running it — check before starting anything new

Multiple concurrent sessions have already caused real collisions: orphaned
Flask processes piling up on ports, and one session's `preview_start` trying
to claim a port another session's dev server already owned.

1. Check what's already running before starting anything:
   ```bash
   netstat -ano | grep LISTENING | grep -E ":(51[0-9][0-9]|5002)"
   curl -s http://127.0.0.1:PORT/api/health
   ```
   If a healthy instance exists, use it.
2. If you need a fresh one, **pick an unused port** rather than fighting for
   one that's stuck or owned by another session. If `preview_start` refuses a
   port because another chat's server owns it, don't try to reclaim it — just
   run Vite manually on a free port (`npm run dev -- --port N --strictPort`)
   and open it with `preview_start({url: "http://localhost:N"})`.
3. Keep `web/api/.env`'s `CORS_ORIGINS` and `web/frontend/.env.local`'s
   `VITE_API_URL` in sync with whatever ports you land on. **Both files are
   only read at process startup** — restart both the API and Vite after
   editing either.
4. `.claude/launch.json` lives in the session's working directory (which for
   this project has been `CRMS_DATA`, not `Forge_Group`) and has entries for
   both this app (`forgeqb-app-web`) and Aaron's separate marketing site
   (`forgeqb-web`, a different project entirely). **Never touch the
   `forgeqb-web` entry.** Check the browser tab title if unsure which app
   you're looking at — this one is "Forge QB".

Testing methodology, unchanged across every session: sign up a throwaway
account via Supabase's `/auth/v1/signup`; if email confirmation is on,
confirm it directly (`update auth.users set email_confirmed_at = now() where
id = ...`); sign in via `/auth/v1/token?grant_type=password` for a real JWT;
drive the API with curl for backend checks, but **prefer real browser clicks
over calling internal JS functions for anything UI** — every real bug found
this project (a markdown escaping bug, an adaptive session-id collision, both
infra bugs above) was found by using the running app, not by reading the
code. Delete the test account and every row it touched afterward.

## What's next

1. **Knowledge Depth's missing half: a per-cluster accuracy panel.** Once
   that exists, wiring `GeminiGetter.name_topic_clusters` (already ported
   into `web/api/ai.py` reasoning, just needs the route) to name the clusters
   is small.
2. **Export** — CSV/Anki/Markdown downloads. Data's all there; only the
   browser download mechanism is missing (different problem than the
   desktop's file-save dialogs).
3. **Deploy it somewhere real.** Needs a host with a persistent connection
   pool (`psycopg_pool`), not serverless-per-request. Run under gunicorn
   (already in `requirements.txt`), not the dev server — that alone avoids
   needing to remember `threaded=True` by hand. Frontend is a trivial static
   build anywhere.
4. **A friend (`PeersonJit` on GitHub) may want collaborator access** to
   `github.com/airjan-airlines/Forge_Group` — Aaron doesn't own that repo
   (Arjun does), so either Arjun adds him directly, or Aaron checks whether
   his own access level lets him (Settings → Collaborators shows up only if
   so). Not yet resolved as of this handoff.

## Things not to re-litigate

- **Vanilla JS, not React.** Decided and built on throughout.
- The stateless Adaptive Learning design.
- **Per-user Gemini keys, not one shared server key.** Decided specifically
  because it sidesteps the cost/rate-limiting problem a shared key would
  create — matches the desktop's existing model exactly.
- Duplicating `answerline.py` and `extract_flashcard_json` instead of
  importing them from `forge_backend` — both are **known, flagged debt**
  (README §5), not oversights: both live in `merged_api.py`, which opens
  Flask and sqlite at import time, so importing either would drag the whole
  desktop backend into the web server. `adaptive.py`'s recommender and
  `ai.py`'s Gemini prompts are the counter-examples — those import cleanly
  and so were *not* copied. The fix, when it happens, is lifting the copied
  functions into a shared module both builds import — not re-deciding
  whether copying was right.
- No markdown library for the notebook — the grammar is small, fixed, and
  fully known (defined by what `notebook.py` emits). Extend `markdown.js` if
  a future format needs more; don't reach for a dependency.
- Whether the stats panels should return images — no, numbers plus a written
  finding; the browser draws.
- Flashcard generation and saving are two separate steps (draft, then pick
  which to keep); study-guide generation is one step (generate-and-save).
  This is deliberate, not inconsistent — see "The AI features, three of
  four" in CHANGES.md §5 for why.
