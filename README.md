# ForgeQB on the web — baseline

Two things live in this folder. The first is an answer to Arjun's question
about where the machine learning actually is. The second is a working skeleton
of ForgeQB as a website: Supabase for accounts, Postgres for everything the
desktop build keeps in SQLite, and a Flask API that scores buzzes rather than
trusting the page to score its own.

Nothing here changes the desktop app. `forge_backend/` and `electron-app/` are
untouched.

---

## 1. Is the machine learning in the repo?

Mostly yes. Four pieces, all present:

| File | What it does |
| --- | --- |
| `forge_backend/utilities/compute_all_embed.py` | Runs `all-mpnet-base-v2` (sentence-transformers) over every question from 2016 on, storing a 768-dimension float32 vector per row |
| `forge_backend/utilities/cluster_all.py` | UMAP down to 5–50 dimensions, then HDBSCAN, per subcategory. Picks its hyperparameters with a 20-draw `ParameterSampler` search, scored by noise fraction, taking the first setting that yields more than 10 clusters |
| `forge_backend/rec_logic/recommender.py` | Picks the next question: softmax over `1 − skill` per cluster, so weak clusters come up more, with an ε = 0.1 explore branch |
| `forge_backend/rec_logic/user.py` | The skill update. Logistic expected-score against question difficulty, nudged by K = 0.5 and scaled by celerity, clamped to 0–10 — Elo in shape, with a speed term |

What that produced, measured on the live database rather than assumed:

```
questions                    169,099
with an embedding             94,648   (exactly the set_year >= 2016 rows)
with a cluster_label          85,518   (76,327 of them non-noise)
embedding size             3,072 bytes  ->  768 dimensions, float32
embeddings on disk             277 MB   of the 569 MB file
```

**Two things are not in the repo, and one of them matters.**

*The ingest is missing.* Nothing in `forge_backend/` writes to the `questions`
table. The only qbreader calls anywhere are `random-tossup` (unused legacy) and
`check-answer` (answer scoring). Whatever built this database ran outside the
repo and was never checked in — so a refresh is currently impossible without
it. Worth asking Arjun for it before anyone writes a replacement: reproducing
the schema by hand across 629 sets, with `set_name`, `set_year`,
`packet_number`, `question_number`, `difficulty` and `subcategory` all
consistent, is a lot of work to redo badly.

*The dependencies are missing too.* `requirements.txt` lists ten packages and
none of them are the ML ones — no `torch`, `sentence-transformers`, `hdbscan`,
`umap-learn` or `joblib`. Both scripts fail on import from a clean checkout.
That is a one-line fix and it is worth making, because right now the pipeline
reads as absent when it is only unrunnable.

There are no trained model weights in the repo either, and there should not be:
`all-mpnet-base-v2` is downloaded from HuggingFace at run time, and the actual
output of the pipeline is the `cluster_label` column, which ships inside the
database on the private `vbeta-data` release tag.

**One thing worth a look while you are in there.** The cluster counts per
subcategory are wildly uneven:

```
European History  493      American History   46
World History     461      World Literature   20
British Literature 365     American Literature 17
Auditory Fine Arts 358     Ancient History    15
...                        Mythology          12
```

`CATEGORY_CLUSTERS` in `merged_api.py` matches these exactly, so nothing is
broken — but a subject split 493 ways and a subject split 12 ways are not the
same model, and "Knowledge Depth" reads them as though they were. That is
almost certainly the hyperparameter search taking the first setting over 10
clusters rather than the best one. Not urgent; worth knowing.

---

## 2. What the baseline covers

One complete loop, end to end, with real accounts:

sign up → sign in → get a tossup → watch it read → buzz → answer → the server
scores it → a neg files itself into your review list → drill the review list on
SM-2 → see your lifetime numbers and your practice streak.

That is deliberately narrow. It is the slice that forces every hard decision a
website version has to make — multi-user data, real auth, server-side scoring,
a question set that has to fit in a hosted database — and once those are
settled the rest of the desktop app is porting rather than designing.

**All of it has been run against a real Supabase project**, not reasoned about:
both migrations applied, 169,056 questions loaded, a real account signed up and
signed in, a tossup served, a neg and a power both scored, the review queue
filled and drilled, and the stats read back. The checks worth naming:

- A request with no token, and one with a forged token, are both **401**.
- Signed in as user A, `select count(*) from user_stats` returns A's rows.
  Signed in as user B, the same query on the same table returns **0**. That is
  RLS, with no `where` clause involved.
- An attempt to `insert` a row owned by *another* user was **refused** by the
  `with check` clause — the specific attack that clause exists to stop.
- A question is served with **no `answer` field**; the answerline only comes
  back after a guess is committed.
- An answer submitted at 22:28 US Central filed under `2026-08-22` while UTC
  had already rolled over to the 23rd — the timezone handling doing its job.
- SM-2 moved `sm2_ef` 2.5 → 1.96 on a neg and scheduled it a day out, which is
  the published behaviour for a grade-1 answer.

The test account and its rows were deleted afterwards; the question set was
left in place.

```
web/
  supabase/migrations/
    0001_content.sql        the question set, shared and read-only
    0002_user_data.sql      every per-user table, plus the RLS policies
  tools/
    export_to_postgres.py   one-time load out of the desktop SQLite file
  api/
    app.py                  app factory, CORS, error handling
    auth.py                 JWT verification -> g.user_id
    db.py                   connection pool, RLS-scoped transactions
    config.py  clock.py  scoring.py  answerline.py
    adaptive.py             stateless adaptive learning core
    notebook.py             filing rules and guide assembly
    panels.py               the five stats panels: shaping and wording
    review_settings.py      the three review thresholds, read and clamped once
    secrets_store.py        per-user Gemini keys, encrypted at rest
    ai.py                   a GeminiGetter built per request, from the caller's own key
    routes/questions.py  answers.py  review.py  stats.py  adaptive.py
           notebook.py  settings.py
  frontend/
    index.html              the shell, ported from electron-app/index.html
    tailwind.config.js      the desktop's palette, copied not re-picked
    postcss.config.js
    src/main.js             the reader, and screen switching
    src/profile.js          the five panels, drawn
    src/notebook.js         guides, notes, clues, flashcards
    src/markdown.js         the small guide renderer notebook.js needs
    src/records.js          the records book
    src/reviewSettings.js   the review thresholds, and the queue's counts
    src/reviewList.js       the review queue, browsable
    src/api.js  src/supabase.js  src/style.css
```

---

## 3. Running it

**Supabase.** Make a project, then run `0001_content.sql` and
`0002_user_data.sql` in the SQL editor, in that order.

**Email confirmation is on by default.** A new account cannot sign in until it
clicks the link Supabase emails it. That is the right default for a real site;
for local testing it is a nuisance, and it is switched off under
*Authentication → Providers → Email → Confirm email*.

**Load the questions.** From the repo root, with the desktop app closed:

```bash
python web/tools/export_to_postgres.py --database-url "postgresql://postgres.PROJECT:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres"
```

Around 135 MB, and about 16 seconds. It skips `vector_embedding` — see the note at
the top of `0001_content.sql`, but the short version is that it is 277 MB of a
500 MB free tier for a column no request reads. Embeddings only exist so
`cluster_all.py` can re-derive `cluster_label` offline, which keeps happening
against the SQLite file; only the result comes here.

**API:**

```bash
cd web/api
pip install -r requirements.txt
cp .env.example .env      # then fill it in
python app.py
```

**Frontend, in a second terminal:**

```bash
cd web/frontend
npm install
cp .env.example .env.local   # then fill it in
npm run dev
```

`npm install` now pulls Tailwind and PostCSS as build dependencies. If a dev
server was already running when they arrived, restart it — Vite reads
`postcss.config.js` once at startup, so an existing process keeps serving the
stylesheet with the `@tailwind` directives still in it and the whole page
renders unstyled.

Open http://localhost:5173. Sign up, confirm the email, sign in.

The desktop app is unaffected and still runs the way it always has — backend on
port 5001, Electron on top. The web API uses 5002 so both can run at once.

---

## 4. The three things Arjun flagged

### Auth

The rule the whole API is built around: **the caller's identity comes from a
verified signature, never from the request.** No route reads a user id out of a
body, a query string or a header. `auth.py` sets `g.user_id` from the `sub`
claim of a JWT that Supabase's own key material has vouched for, and that is
the only user id any route can see.

This is worth stating flatly because it is the step that gets skipped. An
endpoint that accepts `{"userId": ...}` next to a valid login is a total
authorisation bypass, and it reviews as completely ordinary code — every
request the app itself makes sends the correct value, so it works perfectly
right up until someone changes one character in a console.

What that principle turned into here:

- **Signatures are checked, both schemes.** Supabase signs either ES256/RS256
  (verified against the published JWKS, no secret needed) or legacy HS256
  (shared secret). Both are handled. The algorithm named in the token header
  selects the code path and is never accepted as permission to skip the check —
  `alg: none` is rejected outright.
- **Expiry, audience and issuer are all required claims.** A token that is
  valid but issued by a different project is not a login here.
- **Row level security is on every user table, and it is actually reachable.**
  Policies that are written but never consulted are the usual outcome, because
  the role the server logs in as can see everything. `db.user_tx()` switches to
  the `authenticated` role for the length of the transaction and puts the
  verified user id on it as `request.jwt.claims`, which is where `auth.uid()`
  reads from. That switch is the load-bearing line; without it the policies are
  decoration. Application code still writes `where user_id = %s` on top. Two
  locks, because either one alone is fine on a good day.
- **The write policies carry `with check`, not just `using`.** Without it an
  account can update a row it owns and change `user_id` to somebody else's on
  the way past — the check runs against the row as it will be, not as it was.
- **The scoreboard moved to the server.** The desktop renderer decides whether
  a buzz was right, whether it was in power, and what it is worth, then posts
  the verdict. On the web that is `POST {"outcome":"power"}` in a console for
  +15 points on a question nobody read. The page now sends what it *observed*
  — which question, how many words had been shown, what was typed — and the
  server derives the rest. The answer is not even sent with the question; it
  comes back with the result.
- **CORS names its origins.** No wildcard.
- **The Gemini key is server-side.** The desktop build asks each user for their
  own and keeps it in `config.json`. On the web there is one key, it belongs to
  whoever runs the server, and it must never reach a browser.
- **Errors say one sentence.** A stack trace in a JSON body hands over the file
  layout, the library versions and usually the SQL.

One thing that confuses people and is not a bug: the anon key in
`web/frontend/.env.example` ends up inside the built JavaScript and is meant
to. It identifies the project; it authorises nothing. What keeps one account
out of another's rows is RLS. The `service_role` key is the opposite — it
bypasses RLS completely and belongs on a server or nowhere.

### The N+1 problem

The shape, in the review list, which is where it is easiest to fall into:

```python
rows = select * from review_queue where user_id = ...      # 1 query
for row in rows:
    row.question = select * from questions where id = ...  # + 1 each
    row.history  = select * from review_answers where ...  # + 1 each
```

25 rows a page is 51 round trips instead of 3, and the count grows with the
page rather than staying flat. It is invisible in development — the database is
on the same machine and each trip costs microseconds — and it is the entire
latency budget once Postgres is in another data centre. Both loops are
collapsed in `routes/review.py`: the question comes from a join, and every
history on the page is fetched in one query keyed by `= any(%s)` and grouped in
Python. Same thing in `routes/questions.py`, where the category picker is one
grouped query rather than a count per category.

**There is one in the desktop code**, at `stats_manager.py:1829` — the cluster
labeller loops over unnamed clusters and runs a `SELECT answer FROM questions
WHERE subcategory = ? AND cluster_label = ?` for each. It is bounded by the
`cluster_labels` cache so it only bites on a cold profile, and a subject with
493 clusters is a lot of trips through it. One query with `cluster_label IN
(...)`, grouped in Python, would do.

Two related habits the API sticks to:

- **No network call inside a transaction.** Checking a guess means asking
  qbreader, which gets six seconds. Holding a Postgres transaction open across
  that pins a pooled connection and its row locks for the whole wait, and eight
  in a row is the entire pool. `routes/answers.py` reads, then checks, then
  writes.
- **No `count(*)` for paging.** The review list fetches one row past the page
  and reports whether it came back. The total is the expensive part and nothing
  on screen needs it.

### Frontend and backend structure

The desktop backend is one 3,316-line module and the renderer is one 5,968-line
script. Both work. They are also why a route can quietly have no caller for a
fortnight without anyone noticing — there is nowhere for it to be missing
*from*.

Here the boundary is drawn at what each side is allowed to know:

- **The backend owns every decision that counts.** Scoring, the SM-2 schedule,
  which day an answer belongs to, what the acceptable answers are. If getting
  it wrong would corrupt data or hand out points, it is server-side.
- **The frontend owns presentation and timing.** How fast words appear, what
  the keyboard does, what the buttons say. If getting it wrong is a cosmetic
  annoyance, it is client-side.
- **One transport, one place.** `src/api.js` is the only file that calls the
  API, so the `Authorization` header is attached once. The desktop renderer has
  42 scattered `fetch` calls; the way that fails is that one of them forgets.
- **`response.ok` is checked.** `fetch` only rejects on a network error, so a
  404 arrives looking exactly like success — which on the desktop build meant
  edited notes reported as saved and then discarded on reload.
- **Blueprint per area on the server.** `routes/review.py` either has an
  endpoint or it does not.
- **Timezone travels with the request.** The desktop build asks the operating
  system what day it is, because the machine running the code is the machine
  the player is sitting at. A server has no such thing. This codebase has
  already paid for three UTC-versus-local mistakes — the review queue serving
  one question forever, 22 of 52 progress rows filed under the wrong date, 30
  of 53 flashcards grouped under a day nobody played — so the browser sends its
  IANA zone and `clock.py` is the only place that reads it.

---

## 5. Three data problems the load turned up

None of these break anything, all three are in the desktop database, and the
loader handles them — but they are real and worth Arjun seeing, because two of
them point at bugs upstream rather than at bad luck.

**4,003 rows store `cluster_label` as an 8-byte blob instead of an integer.**
Somewhere in the clustering pipeline a numpy `int64` was written with
`.tobytes()` into this column — the pattern `vector_embedding` uses on purpose,
leaking into a column that wants a plain number. SQLite does not care, since it
types values rather than columns, which is why this has sat there unnoticed;
Postgres refuses it outright. Nothing is lost: every one is exactly 8 bytes and
decodes as little-endian int64 to a value between -1 and 14, which is the range
real cluster ids use. `_clean_cluster_label` in the loader unpacks them, and the
count afterwards matches the SQLite count exactly (85,518 rows with a label).
**The fix belongs in `cluster_all.py`** — `int(labs)` is already there on line
147, so it is likely an older run that wrote these.

**One row has a NUL byte inside its answerline** (id 32882, a Circe question).
Postgres text cannot hold one at all, and `COPY` aborts the entire batch on the
first it meets rather than skipping the row, so this one row would have failed
the whole 169,099-row load. Stripped on the way through.

**43 rows have no question text or no answer.** They are not questions. The
loader skips them and says how many, which is why the Postgres count is 169,056
rather than 169,099.

---

## 6. What is not built yet

Honest list. None of it is blocked; it is scope.

**Ported, but a copy that must be de-duplicated before merge.**
`api/answerline.py` is `clean_answerline` and the answer matcher lifted out of
`merged_api.py` verbatim. Two copies of a matcher that fiddly will drift, and
the drift shows up as the web build scoring an answer differently from the
desktop one. The right shape is a shared `forge_backend/answerline.py` that
both import — left as a copy only so this baseline could be reviewed without
editing files on the desktop branch.

**Built.** Adaptive Learning — `GET /api/adaptive/categories`,
`GET /api/adaptive/question`, `POST /api/adaptive/end`, plus the skill update
folded into `POST /api/answers`. The recommender and skill model
(`rec_logic/recommender.py`, `rec_logic/user.py`) import unchanged from
`forge_backend` — pure numpy, no SQLite in them, so this is the one piece of
the desktop backend reused directly rather than ported. What had to be
rewritten is where the model *lives*: the desktop keeps one live `user` object
per session in a module-level dict, which cannot survive a server with more
than one worker process, let alone a restart. `web/api/adaptive.py` rebuilds
the object from `category_user_state` on every request and writes it back in
the same transaction as the answer — see `0003_adaptive_state.sql` for why,
and the two fields (`random_skill`, `recently_seen`) the desktop never had to
persist because its process never restarted mid-session.

Two design points worth flagging for review:

- **The cluster a skill update is filed against comes from the question row,
  never from the client.** `POST /api/answers` takes an optional `adaptive`
  object, and the only field trusted from it is `restoreKey` — which
  selection's model to touch, the user's own pick. The cluster id and
  subcategory that decide *which number moves* are read back off the question
  that was actually served. Tested: a request claiming `clusterId: 999999`
  updated the real cluster from the question row and the forged id was
  silently ignored.
- **`CATEGORY_CLUSTERS` (hardcoded in `merged_api.py`) became a query**, gated
  at >10 real clusters — `cluster_all.py`'s own acceptance bar, borrowed rather
  than invented, since that script already discards anything with fewer. On
  the current database this reproduces the desktop's 19 subjects exactly, plus
  one it's missing: Other Fine Arts, which has a genuine 16-cluster model the
  hardcoded dict never got a line added for.

Verified against the real database: a 6-question session with no repeated
questions and `recently_seen` correctly capped at 5; state read back correctly
by a brand-new Python process (as close to "the server restarted" as a script
can demonstrate); the exhausted-cluster fallback (redo_rec's masked-cluster
path) survives on Mythology, the smallest real subject at 12 clusters, without
the `zero-size array` crash that path exists to prevent; and every route 401s
with no token.

**Built.** The notebook — notes, study guides, flashcards and saved clues.
`web/api/routes/notebook.py` (sixteen endpoints under `/api/notebook`) over
`web/api/notebook.py`, which holds the two parts that are not CRUD: deciding
which shelf a saved item belongs on, and assembling several notes into one
guide. Everything the desktop does here that needs a model — generating a note,
generating flashcards, explaining a clue — is still waiting on the server-side
Gemini key; everything that only needs text is ported, guide building included,
because merging notes into a sorted reference list never involved a model.

Three things carried over deliberately rather than rewritten:

- **The shelf is decided by the server, from the question row.** Adaptive
  Learning picks by *sub*category and hands "Chemistry" back as `category`, so
  every writer that believed it filed the item under a name that is not a
  notebook category — which split Chemistry flashcards off the Science shelf
  and minted a phantom tile per subcategory. `notebook.canonical_category` is
  the desktop's fix, and here it is the same rule the scoring and the adaptive
  cluster update already follow: the client says which question, the server
  says what that question is. A save posting `category: "Trash"` against a real
  Literature tossup stores Literature.
- **An id in the URL is not permission to touch that row.** Every statement
  carries `user_id = %s` on top of RLS and returns the id it changed, so a
  write that matched nothing is a 404 rather than a success. The desktop had no
  owner to check and reported success on rows that were not there, which is how
  an edited note came back unedited after a reload.
- **Merging notes and deleting the originals are one transaction.** Two
  requests there means a guide that failed to write and notes that are already
  gone.

One thing not carried over: the desktop matches a category case-folded with
underscores turned back into spaces, because its UI sends `american_literature`
for a value stored `American Literature`. That is a client bug patched in the
server. This client sends the stored name, so the match is exact — and the bulk
deletes return a count, which is how a mismatch shows up as "deleted 0" instead
of as a silent success.

Verified against the real database, two throwaway accounts, over HTTP: 48
checks, including a subcategory save landing on its parent shelf, a forged
category ignored, a whitespace-only note refused, half-finished flashcards
counted rather than saved, a guide re-sorted A-Z on append rather than growing
an unsorted tail, guides refusing to nest, and account B getting 404 on every
one of account A's ids while A's rows survived B's `delete-all`. The accounts
and their rows were deleted afterwards.

**Built.** Five of the six stats panels — Where You Buzz, Ceiling, Neg Autopsy,
Retention, Progress Over Time. `web/api/panels.py` (the reasoning and the
wording, pure functions over already-aggregated rows) under five endpoints in
`web/api/routes/stats.py`.

The desktop renders every one of these as a matplotlib PNG and returns base64
inside a JSON body. That is the half that does not port: a browser can draw.
So the routes return numbers and a written finding, and the picture is the
page's problem. The reasoning ports unchanged — which bands are worth
comparing, where the ceiling is, which axis the negs actually track, and the
sentence that says so.

Three of the five changed query shape on the way over, all for the same
reason — work that belongs in the database was being done in Python:

| Panel | Desktop | Here |
| --- | --- | --- |
| Where You Buzz | one query per band, four round trips for four numbers | one grouped query |
| Neg Autopsy | selects every qualifying row of `user_stats` and buckets it in a loop | grouped in Postgres; at most 11 difficulties × 4 bands comes back |
| Ceiling's tier labels | groups all 169,056 questions per request | ranked in SQL, cached per process |

That cache is the only one in the API, and it is safe for the reason nothing
else is: it comes from the shared read-only question set, so it is identical
for every user. Caching anything *per user* in module state is exactly what
stops the desktop's adaptive session surviving a second worker.

The minimum-sample gates are the load-bearing part of all five, and they are
ported as-is. A band, level, subject or day below its gate is shown but never
compared, never called your best or worst, and never used to reach a verdict.
Two buzzes at 100% is not a strength, and a panel that says it is teaches the
wrong lesson confidently.

Verified by **diffing against the desktop implementation on identical data**:
`stats_manager.py` loaded directly, run against a SQLite fixture holding the
same rows the web account was seeded with, and its output compared field by
field with the JSON the API returns. All five match exactly, including the
written findings, under a category filter, a subcategory filter, a session
filter, and both filters at once. 51 checks in total — also that every route
401s without a token, that a fresh account gets `hasData: false` and a sentence
rather than an error, that a malformed `?month=` falls back instead of
failing, and that a second account sees none of the first's answers. The only
fields excluded from the diff are Ceiling's tournament names, which come from
the real 169k question set on one side and a twelve-row fixture on the other.

**Built.** The reader, looking like ForgeQB. `web/frontend/index.html` is the
desktop shell ported over — header, sidebar, the three stacked cards — and
`src/main.js` is the reader behind it.

Tailwind is a build step here rather than the vendored runtime compiler the
desktop ships, which is what Tailwind is actually for: the output is a
stylesheet holding only the classes this app uses (10.5 kB), and no compiler
reaches the user. The palette in `tailwind.config.js` is copied from the
`tailwind.config` block at the top of `electron-app/index.html` rather than
re-picked by eye — those six browns are what makes the app recognisable, and a
port that invents its own is a different product doing the same job.

What the reader now does that the bare version did not:

- **The powermark.** Everything up to the `(*)` is bold-italic, the mark itself
  is dimmed and spaced, and the toggle is a class on `<body>` so it applies to
  the tossup already on screen rather than only to the next one. The marking
  survives the end-of-tossup reveal, which is the moment it is most worth
  having and the moment the desktop used to lose it.
- **Both countdowns.** Five seconds of dead time to buzz after the read runs
  out, ten to answer after buzzing, each on a wall-clock deadline rather than a
  tick count so a backgrounded tab comes back with the right answer. A timeout
  submits whatever is in the box, which scores a neg — what a real timeout
  costs.
- **The full filter set.** Category, subcategory (hidden rather than shown
  empty for the categories that genuinely have none) and difficulty, all
  multi-select, all fed from `/api/questions/filters`.
- **Reading speed, pause, and the desktop's shortcuts** — S, N, P, and space or
  B to buzz. Shortcuts stand down while you are typing an answer.
- **The stats strip**: P/T/N, tossups heard, points, celerity, and the streak
  line, refreshed after every answer and scoped to the category filter.

Driven in a real browser against the live database rather than eyeballed: sign
in, a tossup served and read word by word, the powermark toggled off and back
on with the computed weight checked either way (800/italic → 400/normal), a
buzz at word 115 scored a neg by the server, the whole tossup revealed with the
power still marked, "Add to Missed" filing it, review mode serving it back with
the "scheduled ahead of time" notice, pause holding the read and resume moving
it on, space buzzing while `s` typed into the answer box did not, an answer
timeout scoring a second neg, the subcategory box appearing for Science and
hiding for Mythology, and a full read timing out into a recorded pass. No
console errors; the production build succeeds. The test account was deleted
afterwards.

**Built.** The profile page, and with it the five panels actually drawn.
`web/frontend/src/profile.js`, behind the profile button in the header.

Charts are hand-built SVG rather than a charting library. Five charts is not
enough to earn a dependency, the shapes are simple — bars, a grid, a pair of
lines — and inline SVG inherits the page's own colours instead of needing a
theme adapter to be told about them.

**The minimum-sample gate is drawn, not merely carried.** Every panel hatches
and dims the buckets below its gate and says so in a legend under the chart.
The API already refuses to compare those buckets or reach a verdict off them;
drawing them identically to the solid ones would hand that judgement back to
the reader after the server had deliberately taken it away. A 100% neg rate
built on two buzzes must not read as the hottest cell on the grid.

Three drawing decisions worth keeping:

- **Where You Buzz runs its bars from a zero line**, not from the left edge.
  The whole point of the panel is that a buzz point can be worth *negative*
  points, and a bar chart with an implicit zero at the axis hides exactly that.
- **Ceiling uses bars, not a line.** Difficulties you have never played are
  gaps in the data, and a line would draw straight through them as though the
  number in between were known. The 50% gridline is dashed because it is the
  one that means something — the ceiling is defined as the hardest level still
  above it.
- **Progress breaks its line wherever a day was missed.** The API sends unplayed
  days with a null accuracy rather than a zero for exactly this reason, and
  joining across them would draw a fortnight away as a slide down to nothing.
  Celerity shares the same frame on purpose: buzzing earlier while converting
  less is a shape only visible when the two are drawn against each other.

Progress Over Time sits below the picker on its own rather than inside it,
following the desktop — it is the one panel "Reset Stats" does not touch, so
putting it in the same picker would imply it resets too.

Driven in a real browser against the live database, on a seeded account: all
four picker panels drawing with the right shapes and the server's finding
alongside; the hatching landing on exactly the buckets under the gate (the
2-answer difficulty level, the 1-review subject, four thin grid cells); the
Ceiling finding naming a real tournament tier; Progress paging back a month
with the "Later" step disabling itself at the newest and the under-gate day
rendering hollow; the category filter cutting 60 tossups to Literature's 12
and resetting the month with it; opening the profile mid-tossup abandoning the
read rather than leaving a timer running behind the page; and the reader's
shortcuts standing down while the profile is open. No console errors,
production build clean, test account deleted.

**Built.** Records and Review Settings, the two header icons that had nothing
behind them. `GET /api/adaptive/sessions` and
`POST /api/adaptive/sessions/<id>/delete` with `src/records.js` on top; `GET`
and `POST /api/settings/review` plus `GET /api/review/counts` with
`src/reviewSettings.js`.

Three decisions worth keeping:

- **A record does not draw its own charts.** The desktop's records page renders
  a second copy of the profile panels scoped to one session. Here, *View stats*
  opens the profile itself with a session filter, because the profile already
  draws those panels and `routes/stats.py` already answers them per session.
- **Only the panels that can be scoped are offered.** Where You Buzz, Ceiling
  and Neg Autopsy read `user_stats`, and every row of it carries the sitting it
  came from. Retention reads the review queue and Progress Over Time reads the
  practice calendar; a question still being relearned and a day already played
  both outlive the sitting that produced them, so neither can be narrowed to
  one. They come off the page under a session filter, with a banner saying so,
  rather than quietly answering from the whole account under a heading that
  says "one session". `GET /api/stats/summary?session=` returns `null` for the
  streak and the review counts for the same reason.
- **Deleting a record deletes the record.** The answers behind it stay in
  `user_stats` and no lifetime number moves; the confirmation says that,
  because "delete this session" could reasonably mean either. Wiping the
  answers is Reset Stats, which still does not exist.

The thresholds live in `user_settings` keyed by `(user_id, key)` — the
desktop's ownerless `app_settings` table with an owner in front of it — and
`api/review_settings.py` holds the defaults, the reader and the clamp so that
`routes/answers.py` and `routes/review.py` cannot end up disagreeing about what
"learned" means.

Building the panel exposed two things that were already wrong. `stuckAfterMissed`
was storable and completely unused: the queue had no stuck count, no stuck flag
and no `status=stuck` filter, so one of the three thresholds governed nothing.
And `minCelerity` defaulted to `0.0` here against the desktop's `0.25`, so a
correct answer on the giveaway clue advanced a question toward Learned on the
web and not on the desktop — a silent divergence, since both builds mark
questions Learned and only the speed differed. Both are fixed; the defaults now
come from one dict that matches `REVIEW_DEFAULTS` in `merged_api.py` verbatim.

A third bug turned up underneath all of it and belongs in §4's auth notes:
**JWT verification had no allowance for clock skew.** Supabase stamps `iat`
from its own clock, `auth.py` checked it against this machine's, and with the
machine one second behind, PyJWT rejected every freshly minted token as
`ImmatureSignatureError` — reported to the user as "could not verify that
session", which points at the token rather than at the clock. `auth.py` now
passes `leeway=CLOCK_SKEW_SECONDS` (30). A token stays usable for thirty
seconds past `exp` as a result, which is the trade the JWT spec assumes; the
protection against a stolen token is its one-hour lifetime and RLS, not the
last half-minute of it. Worth knowing before this is deployed on a host whose
clock drifts, because the failure looks exactly like an auth bug.

**Built.** `POST /api/stats/reset` — the last profile-page button without a web
endpoint. One statement, `delete from public.user_stats where user_id = %s`,
inside the caller's own RLS-scoped transaction, matching
`reset_all_stats()` in the desktop's `stats_manager.py` exactly: `progress_daily`,
the review queue, the notebook and Adaptive Learning's skill model
(`category_user_state`) all survive, for the same reasons the desktop leaves
them alone — Progress Over Time is a record of how someone changed, not a
stat; a neg does not stop being unlearned because the counter that logged it
reset; and a skill model is not a scoreboard. The frontend trades the
desktop's plain `confirm()` for a dedicated modal that spells out exactly what
clears and what survives, and hides the button entirely while the profile is
scoped to one saved Adaptive Learning session — resetting the account's
lifetime stats does not mean anything from inside one sitting.

Verified with 16 backend checks (seeded rows in all five affected tables,
confirmed the reset took exactly `user_stats` and left the other four at their
exact pre-reset counts) and a full browser pass: opened the modal, cancelled
and confirmed nothing changed, then confirmed for real and watched the
profile's lifetime tiles and the reader's stat strip both drop to zero live,
with the review queue's three questions still there afterward.

**Built.** The review list, `web/frontend/src/reviewList.js` over the
existing `GET /api/review/queue` — the endpoint has served paged rows with a
status filter and a `stuck`/`timesMissed` flag on each one since the queue
itself was built; nothing rendered it. "See all missed questions" in the
Review Settings panel opens it now, matching the desktop's button of that
name. One backend change: `GET /api/review/queue` takes an optional
`?category=`, matching either level the same way the stats panels already do
— the one filter it was missing to be a browsing page rather than a status
dump.

Deliberately narrower than the desktop's "Browse All Questions", which pages
the whole 169,099-question set by status (unseen included) with a text
search on top — real scope on its own, not attempted here. This page shows
what is actually in the queue: a few hundred rows at the outside, not 169k.

Two things worth keeping: a subcategory that repeats its own category
(Mythology, Geography, Philosophy) is shown once, not twice, the same dedup
the desktop's category picker already applies; and removing a question says
what survives it — the confirmation names the answer history that goes with
it and says the row comes back automatically on the next neg, since a player
removing one by hand needs to know it is a reset, not a permanent exemption.

Verified with 14 backend checks (status and category composing correctly
against real question rows in two real categories) and a full browser pass:
all four status tabs and the category filter narrowing the list and
composing with each other, the dedup rule and the Stuck/Due badges both
confirmed against real seeded data, and removing a stuck row updating both
the page's own summary tiles and the Review Settings panel's counts live,
without a reload.

**Built.** Per-user AI keys — the storage foundation every AI feature needs
before it can call Gemini at all. Aaron decided each account brings its own
key (`web/supabase/migrations/0004_user_secrets.sql`), matching the desktop's
model rather than one server key shared by everyone: no shared-cost risk to
him, at the cost of real signup friction, accepted knowingly.

The key is stored **encrypted**, not as text — `secrets_store.py` uses Fernet
with `SECRETS_ENCRYPTION_KEY` held only in the server's environment, never in
Postgres, so RLS (which stops one account reading another's row) and the
encryption key (which stops a leaked database being a leaked set of
everyone's Google billing) each have to fail for the other to matter.
`GET /api/settings/ai-key` returns a four-character hint and never the key
itself — the desktop's `GET /api/key` hands the whole thing back to its
renderer, fine for a local window and a credential-disclosure endpoint the
moment it is a web page. `POST` verifies a key against Google
(`ai.verify_key`, a cheap `models.list()` call) before storing it, so a wrong
key is caught where it was typed rather than surfacing later as an
unexplained AI failure.

`web/api/ai.py` builds a `GeminiGetter` **per request**, from the calling
account's own key — the desktop keeps one shared instance with a key
reassigned in place, which is one race away from billing the wrong account on
a server with more than one request in flight. The prompts and retry logic
import from `forge_backend/geminiGetter.py` unmodified, the same
reuse-not-copy choice `adaptive.py` makes with the recommender.

Two real bugs found verifying this against the live Gemini API rather than
assuming the client library behaves: `genai.Client(...).models.list()` as a
one-liner can be garbage-collected mid-call and die with "client has been
closed" — intermittent, and only reproduced under the server's own timing;
fixed by binding the client to a name first. And the test harness's own
`purge()` matched accounts by exact-case email while Supabase lowercases them
on signup, so a "purged: True" could leave the row behind — caught only
because a post-session account count came back wrong.

32 checks verified this against the live project: the full save/read/delete
flow with a real key, confirming at every step the plaintext never appears in
a response and the database column holds ciphertext; and a second pass
attacking RLS directly through PostgREST with two real accounts (select,
select-by-id, patch, insert-claiming-another's-`user_id`, delete — all
refused).

**Not started.**

- The notebook's *export* endpoints — `/exportFlashcards`, `/exportNotes` and
  their selected-subset variants, which write CSV, Anki and Markdown files on
  the desktop. The data behind them is served now; what is missing is the file
  building, and on the web a download is a different mechanism anyway.
- **Knowledge Depth**, the sixth stats panel. It is the only one that needs a
  model: the clusters it reports are numbered, and `get_cluster_names` asks
  Gemini what to call each one. Without the key it would be a chart of
  "cluster 7", so it waits with the rest of the AI features. The N+1 in its
  labeller is described above.
- The AI features themselves — explanations, generated study guides, generated
  flashcards, Knowledge Depth's cluster naming. The blocker underneath all
  four (which key, and whose) is resolved and built — see "Per-user AI keys"
  above — but none of the four features has an endpoint yet, and neither does
  the Settings panel a player would use to paste their key in.
- Voice Mode, and keyboard shortcuts beyond the desktop's S/N/P/A and
  space/B.
- Rate limiting on anything. `POST /api/answers` calls an external API and
  writes three tables, and right now nothing stops it being called in a loop.
- Tests. There are none, here or in the desktop build.

**Decisions still open.**

- Whether this frontend is the real one or whether it should fold into
  `forgeqb_web`, which is already React on Vercel. This one is deliberately
  plain vanilla JS — closest to the existing renderer, easiest to read against
  it, and cheap to throw away.
- Whether the question set stays in Postgres or moves to object storage with
  only an index in the database. 135 MB is comfortable on the free tier today
  and it stops being comfortable once the 2024–25 gap in the set is filled.
