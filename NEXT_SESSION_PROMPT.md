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

**Every feature that was ever "not done" on this port is now done:**

1. Schema (5 migrations) + 169,056 real questions loaded, two upstream data
   bugs found and fixed in the loader.
2. The core practice loop — buzz, server-side scoring, review queue, SM-2.
3. Adaptive Learning, rebuilt stateless (the desktop's in-memory-dict design
   can't survive a real web server; this rebuilds skill state from the
   database on every request instead).
4. The notebook — guides, notes, clues, flashcards. Full CRUD, backend and
   frontend.
5. ~~5 of 6~~ **All 6 stats panels**, drawn as real SVG charts instead of the
   desktop's images. Knowledge Depth (the sixth) landed this session — see
   below.
6. The real ForgeQB UI, ported from the desktop's HTML/CSS.
7. Records and Review Settings pages.
8. `POST /api/stats/reset` — Reset All Stats.
9. The review list as its own page.
10. ~~AI features — 3 of 4~~ **All four AI features, done.** Per-user
    encrypted Gemini keys (each account brings its own — verified: plaintext
    never touches an API response, never stored unencrypted, RLS-isolated).
    Explanations, flashcard generation, study-guide generation, and now
    Knowledge Depth's cluster naming.
11. Export — flashcards as CSV (Anki-importable as-is), guides and notes as
    one Markdown document, whole-shelf or selected-subset for both.
12. **Voice Mode** — reads the tossup aloud via the Web Speech API instead of
    revealing it word by word. No server-side component at all; ported into
    `web/frontend/src/voice.js`. Landed this session alongside Knowledge
    Depth — see below.

### Knowledge Depth, from an earlier session this handoff still covers

`GET /api/stats/knowledge-depth` surfaces the recommender's own per-cluster
skill rating (`category_user_state.user_data`) with AI-generated topic names.
**Earlier handoffs said this was blocked on "a per-cluster accuracy panel
that doesn't exist yet" — that was wrong.** The desktop's own
`get_knowledge_depth` was never accuracy, it's the recommender's skill
number, and the web port has had that model since Adaptive Learning was
built. The only missing piece was the route. Full writeup, including two real
desktop bugs found and *not* reproduced (a silently-dropped-multi-category
session, and a jsonb parsing footgun this port's own first draft hit and
fixed), in CHANGES.md §5 under "Knowledge Depth: the sixth stats panel, and
the last AI feature" — **read it before touching `web/api/clusters.py` or
`web/api/adaptive.py`'s `cluster_skills`.**

One thing worth knowing before you query `cluster_labels` directly: it is
**shared, global content**, not per-user — the first account to name a
cluster caches it for everyone, via an insert-only RLS policy
(`0005_cluster_label_writes.sql`) that only accepts `source = 'ai'` rows tied
to a real cluster. A throwaway test account with no Gemini key will still see
real AI-named labels for any cluster someone else has already named for real.
That is correct behaviour, not test pollution — don't "fix" it by clearing
`cluster_labels`, which would erase other players' real, paid-for names.

### Voice Mode

`web/frontend/src/voice.js`, ported from `electron-app/renderer.js`'s Voice
Mode wholesale -- pure Web Speech API, no backend involved. Reads the tossup
aloud (`speechSynthesis`) instead of revealing it word by word; `main.js`
still owns `wordIndex`/`words`, `voice.js` just reports position back through
callbacks (`onWord`/`onEnd`/`onError`/`onFallback`) rather than reaching into
globals the way the desktop version does. Full writeup, including why
`tick()` is now also the fallback ticker Voice Mode uses on platforms that
never fire `onboundary`, in CHANGES.md §5 under "Voice Mode" — **read it
before touching `tick()`, `buzz()`, `abandonTossup()`, or the pause handler
in `main.js`, all four of which Voice Mode now hooks into.**

Worth knowing before testing it here: this dev environment's headless
Chromium has `speechSynthesis` but never fires `onboundary`, so every
verification run here exercised the **fallback** path (`onFallback` →
estimate position with the timer while audio keeps "playing"), not the
primary `onboundary`-driven one. Both are real code paths in `voice.js` and
both matter, but only one has had a live check on this machine — worth a
real device/browser with actual TTS if that distinction ever needs settling.

**Not done:** picking somewhere to actually deploy. That is the only item
left on this port.

## Two infrastructure bugs worth knowing before you start a dev server

Both make "it's definitely running but nothing works" happen for reasons
that have nothing to do with your code:

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
3. **A CORS error on preflight can mean "stale process," not "CORS is
   misconfigured."** `frontend/.env.local`'s `VITE_API_URL` is shared state
   another session can repoint at any moment; if it now points at a port
   whose Flask process has been running since before your latest code
   change, `OPTIONS` on a route you just added 404s, the browser reports it
   as a CORS failure (correctly — a 404 has no CORS headers), and it reads
   like a config bug that isn't one. Check `curl -s http://127.0.0.1:PORT/api/health`
   is answering with *current* code, not just that it's answering at all —
   if the port's been up a while, restart it against the current `app.py`
   before chasing anything else. This happened at least twice this session
   alone; expect it again.

## Running it — check before starting anything new

Multiple concurrent sessions have already caused real collisions: orphaned
Flask processes piling up on ports, and one session's `preview_start` trying
to claim a port another session's dev server already owned.

1. Check what's already running before starting anything:
   ```bash
   netstat -ano | grep LISTENING | grep -E ":(51[0-9][0-9]|5002)"
   curl -s http://127.0.0.1:PORT/api/health
   ```
   If a healthy instance exists, use it — but see infra bug 3 above before
   trusting "healthy" means "current."
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
5. **If the Browser pane won't composite a screenshot** ("the Browser pane is
   not displayed"), coordinate-based clicks silently miss too — `left_click`
   on a `ref` still resolves to pixel coordinates under the hood. Driving the
   page via `element.click()` / `element.requestSubmit()` through
   `javascript_tool` works around it; verify outcomes through the actual
   network requests and DOM state (`classList.contains('hidden')`,
   `textContent`) rather than a screenshot in that case.

Testing methodology, unchanged across every session: sign up a throwaway
account via Supabase's `/auth/v1/signup`; if email confirmation is on,
confirm it directly (`update auth.users set email_confirmed_at = now() where
id = ...`); sign in via `/auth/v1/token?grant_type=password` for a real JWT;
drive the API with curl for backend checks, but **prefer real browser clicks
over calling internal JS functions for anything UI** — every real bug found
this project (a markdown escaping bug, an adaptive session-id collision, two
infra bugs above, and this session's jsonb/multi-category bugs in Knowledge
Depth) was found by using the running app or seeding real data and checking
the real response, not by reading the code and assuming it was right. Delete
the test account and every row it touched afterward — **except
`cluster_labels`**, which is shared content and must never be cleared by a
test cleanup script.

## What's next

The one thing left:

1. **Deploy it somewhere real.** Needs a host with a persistent connection
   pool (`psycopg_pool`), not serverless-per-request. Run under gunicorn
   (already in `requirements.txt`), not the dev server — that alone avoids
   needing to remember `threaded=True` by hand. Frontend is a trivial static
   build anywhere.

Not blocking, not forgotten:

2. **A friend (`PeersonJit` on GitHub) may want collaborator access** to
   `github.com/airjan-airlines/Forge_Group` — Aaron doesn't own that repo
   (Arjun does), so either Arjun adds him directly, or Aaron checks whether
   his own access level lets him (Settings → Collaborators shows up only if
   so). Not yet resolved as of this handoff.
3. **`web/` now also lives at its own remote**, `github.com/HokkaidoAppleism/Forge_WebApp`
   (Aaron's own repo, separate git history from `Forge_Group` — see `web/.gitignore`,
   added when it was first pushed). It is a second, independent `.git` nested inside
   this checkout; `git add`/`git status` run from `Forge_Group`'s own root do not see
   into it, and vice versa. Push there (`cd web && git push`) when `web/`-only work is
   ready to share, not through the outer repo. Export and Knowledge Depth are
   pushed as of this handoff (`c713866`); **check `git log` / `git status` in
   `web/` before assuming Voice Mode is too** — check before trusting a claim,
   including this one, per the top of this file.

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
- **`cluster_labels` is shared, global content, insert-only, `source='ai'`
  only.** No update path, no delete path, fallback labels never cached. See
  "Knowledge Depth" above and in CHANGES.md §5 before adding either — the
  insert-only design is specifically what the no-fallback-caching choice
  makes safe.
- **Voice Mode is client-only, and stays that way.** It's the Web Speech API
  reading text the browser already has; there is nothing for a server to do.
  Don't go looking for a Voice Mode route or table that doesn't exist.
