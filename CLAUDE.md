# Overtone

Campus dating built on **pairwise comparison** instead of swiping. You are shown
two people who look alike, photos only, and you pick one. Every pair returns
days later with full profiles, and that second, informed choice counts for more.

This file is the project's memory. The code records *what* was built; this
records **why**, and the traps that were paid for once already. Read it before
making architectural changes.

---

## Current state

Phases 00–04 are complete and committed. **243 tests passing**, lint clean,
migration round-trips, frontend builds, and the whole loop — pair, unlock,
request, reply — has been driven end to end in a browser at phone and laptop
width.

```
97fc571  Phase 04: the unlock and the inbox, on screen
368ad2c  Phase 04: the online preference model
9f298fd  Phase 04: message requests, and the end of the swipe-era match model
607c822  Phase 04: the unlock — affinity, Wilson gates and deliberate re-exposure
acb6c03  Add CLAUDE.md: project memory across sessions
1b0e95e  Phase 03: the pair view, plus the periodic sweeps
8b03b1b  Test the pair API over HTTP; share the job-drain and onboard helpers
4536e3d  Phase 03: the pair loop — schema, ratings, generation, serve/decide, API
209a285  Phase 03: Glicko-2 rating engine
1f1128c  Wire the frontend to real uploads; pin the Sarvam model
79d8643  Phase 02: uploads, job queue, worker and the model layer
0432137  Frontend: rebuild on the two-pole design system, responsive
a4fb2a4  Phase 01: identity, profile, prompts and campus access
1dc4684  Phase 00: foundation for the pairwise rework
f895bcc  Baseline: prototype as inherited, before the pairwise rework
```

### Architecture reference

The full design doc lives as a published artifact (v2.1):
<https://claude.ai/code/artifact/c7c5d44f-7ac7-41e0-9fef-2f4305bfbfdb>

**It now lags the implementation** — it was last written against phase 02, and
phases 03 and 04 have both shipped since. Where the two disagree, this file and
the code are right. It is still the best statement of the mechanic and the
long-range roadmap; it needs a pass to fold in the unlock numbers, the request
protocol and the preference model.

---

## How to run it

```sh
# Backend + worker deps
pip install -r requirements-dev.txt

# Database (SQLite locally, Supabase Postgres in production)
export DATABASE_URL="sqlite+aiosqlite:///./dev.db"
export JWT_SECRET_KEY="local-dev-secret-key-not-for-production"
export ENVIRONMENT=development ALLOWED_ORIGINS="*"

alembic upgrade head
python scripts/manage.py seed                     # 81 prompts, 38 genders, 30 sexualities
python scripts/manage.py scope-create --slug demo --name "Demo University" \
    --domain demo.edu --cap man=600 --cap woman=600
python scripts/seed_demo.py --scope demo          # 20 fake people, local SVG portraits

python -m uvicorn backend.app:app --port 8000     # API
npx vite --port 5173                              # frontend
python worker.py                                  # background jobs (optional locally)
```

Demo accounts: `ravi@demo.edu`, `aditi@demo.edu`, … password `overtone2026`.
Ten women and ten men, deliberately balanced: an unlock costs seven
comparisons of one person and a pair may only be shown once, so a viewer needs
at least eight candidates before any unlock is reachable. A lopsided pool
silently makes the whole mechanic impossible for whoever is on the short side
— twelve women and four men meant no woman could ever unlock anyone.

```sh
pytest                       # 243 tests, no network, no models needed
ruff check . && ruff format --check .
alembic check                # fails if models drifted from migrations
python worker.py --status    # which models this process would use (free, offline)
```

---

## Critical decisions, and why

These are the ones that cost real thought. Do not reverse any of them without
re-reading the reasoning.

### The mechanic

**Pairwise comparison, not swiping.** A swipe is an *absolute* judgement against
an invisible, drifting personal threshold — two users swiping right can mean
entirely different things. A forced choice between two similar people is
*relative*, and the similarity controls for everything they share, so the
difference vector points at what the viewer actually responded to. A handful of
pairs teaches more than a hundred swipes.

**Two rounds, two ratings.** Round 1 is a photo alone (`visual` rating, weight
**1.0**). Round 2 is the same pair, ≥48h later, with everything showing
(`profile` rating, weight **2.5**). Keeping them as separate ratings is what
lets the system tell "attractive face" from "wins people over once read". The
2.5 is the most important dial in the system — tune it on real data.

**Ratings are per `(subject, audience_segment, kind)`, never global.** A rating
only means something within the population that produced it. Someone visible to
both men and non-binary people carries two independent visual ratings.
Averaging across audiences would describe nobody.

**Ratings are never shown to anyone, ever.** Not as a number, not as a rank, not
reverse-engineerable from ordering or frequency. This is the line between a
preference engine and something that hurts people on a campus where everyone
knows each other. Decided explicitly; do not soften it.

### Why Glicko-2 over Elo

Elo carries one number and a fixed K-factor, so it cannot distinguish a rating
compared twice from one compared four hundred times. Glicko-2's **rating
deviation** solves three problems at once:

1. **Cold start** — a new profile enters wide and settles in ~10–15 comparisons,
   with no hand-tuned provisional period.
2. **Pair selection** — candidates match on *interval overlap* rather than point
   distance, which is also how an uncertain newcomer gets rated fast.
3. **The unlock threshold** — needs a confidence interval to be honest.

Verified against Glickman's own worked example to three decimals
(1464.06 / 151.52 / 0.05999). `backend/rating.py` is pure functions, no DB.

**Weighted comparisons are a deliberate extension** to the standard algorithm —
weight scales a comparison's contribution to both the information quantity and
the rating change, which is how Glickman's derivation naturally expresses it.

### The unlock

**A run of picks becomes evidence, and the Wilson interval decides when.** Each
decided pair is a Bernoulli trial about one person: shown against a visually
similar, similarly rated opponent, were they chosen? The pairing controls for
resemblance and rating, so the null hypothesis is a coin flip and a sustained
rate above it is a preference.

**Both ends of the interval are used.** The lower bound opens a question at
**0.70 / 90% confidence**; the upper bound closes one, so a record whose
optimistic end is already under the bar stops being asked about. There is
deliberately **no minimum-trials constant** — the interval sets its own floor.
At these settings:

| Record | Outcome |
|---|---|
| 7 straight picks | unlocks — the fastest possible |
| 6 straight picks | still learning |
| 12 of 13 | unlocks |
| even split | settles at 15 |
| 1 of 6 | settles |

`MAX_TRIALS = 25` is a backstop for a rate that hovers, not the mechanism.

**An unlock is permanent.** Counts keep accruing, but later evidence never
revokes it. Taking a revealed profile back would be worse than the occasional
unlock fresh evidence would not repeat.

**The round-2 weight stays out of the interval.** Ratings weight round 2 at
2.5; the confidence interval counts *trials*, and inflating one to 2.5 would
make it a claim about a sample size nobody took.

**Deliberate re-exposure is what makes the unlock reachable**, not a
recommendation feature. Seven comparisons of one specific person, arrived at by
chance out of a pool of hundreds, does not happen. Half the time
(`RE_EXPOSURE_RATE`) the anchor is drawn from this viewer's live hypotheses —
people picked at least once whose record has not resolved. The other half keeps
the pool from collapsing to whoever they liked first.

### Message requests

**An unlock is one person being certain, which is not two people agreeing.** So
it buys exactly **one** opening message, into a request inbox. A reply opens
the chat; until then the sender cannot send again, and the message route is not
a way around that. Declining is final in both directions.

**A mutual crossing skips the request entirely** — both crossed independently,
so there is nothing left to ask and the conversation opens with **no
initiator**, because nobody asked. Resolved in `record_decision`, not in the
route, so every path that records a decision gets it.

**One `Connection` row per pair, ever**, keyed on the unordered pair and stored
in the same canonical order the key is built from, so a row and its key cannot
disagree about who it is between.

### The preference model

**Trained only on round-1 decisions.** The difference between the chosen face
and the rejected one is unusually clean supervision — the two were selected to
look alike, so almost everything cancels. A round-2 pick may have been driven
by a prompt answer or a degree; folding that into a model over *face* vectors
would attribute it to a face. There is a test pinning this.

**The difference vector is normalised.** Its magnitude says how far apart the
two faces were, which the pairing already controls for, so the direction is
what carries information — and the learning rate stops depending on how tight
the band happened to be.

**The honest tension:** `preference.TILT` lets taste influence *who is shown*,
and exposure feeds ratings, so a person seen mostly by viewers already inclined
toward them is judged by a friendlier sample. The tilt is bounded, never
touches who wins a comparison, and **`TILT = 0` removes it outright** — which
has its own test, so the escape hatch is real. What the value should be is a
phase 07 question.

### The surface

**One ground for the whole app: `--sky`.** The palette was measured off Study
of Us at the start, and then the sky was used on the landing page only —
everything after sign-in dropped onto a pale neutral, so signing in looked
like arriving at a different product. Landing, auth, onboarding, the pair view
and the inbox now all sit on it.

What changes between them is *what sits on the sky*, not the colour:

| Screen | On the ground |
|---|---|
| Pair view | the two photographs, and nothing else |
| Inbox | white cards |
| Onboarding | one white card, because people type into it |

**Join and sign in are the deliberate exception** — they stay on the pale
neutral. It is the one place someone is deciding whether to be on a dating app
at all, and a quiet form asks less of them than a branded one. The sky is one
click away on either side, so it reads as a pause in the middle of the brand
rather than a gap in it. Do not "fix" this for consistency; it was tried on the
sky and taken back off.

**White is the display voice only.** White on the sky is **2.1:1** and fails
every contrast threshold, so it is used for the heading and its single
supporting line, where size and weight carry it. Anything small enough that it
has to be read takes `--on-sky-soft` — the deep pole, **5.5:1**. Both, plus a
deep-navy `--sky`, are redefined for dark mode.

**The heading names what it is showing** — "Which woman?", "Which man?",
"Which person?" — from `segment` on the pair response. That is the viewer's
own `interested_in` choice reflected back; it says nothing about either
subject, so round 1's photo-only rule is untouched.

### The campus gate

**The domain check is not the gate. The verification link is.** Refusing
anything that is not `@campus.edu` proves only that the person knows what the
campus domain is — anyone can type `someone@campus.edu`. What makes an account
mean "a student" is that a link arrived in a mailbox at that domain and
somebody opened it. This is the single identity anchor the whole product rests
on; everything else (caps, ban evasion costing something, a bounded
population) is downstream of it.

So: **the token is returned in the API response only when nothing was actually
delivered** (`mail.delivers()` is false). Handing it back with a real provider
configured would undo the entire point of sending it. And **production refuses
to start on the console mailer** — in that mode every signup mints a token that
reaches nobody, so the gate silently admits no one at all.

**SMTP, not one vendor's HTTP API.** It is the single interface SES, Postmark,
Resend, Mailgun, Google Workspace and a university's own relay all speak, and
a campus launch is exactly where the relay you are eventually allowed to use is
not the one you planned for.

**Delivery runs through the job queue**, so a provider that is briefly down
costs a retry rather than an account nobody can ever verify. The message is
built at enqueue time and carried whole in the payload — by send time the
account may be gone, and a link that cannot be regenerated beats one that
quietly stops being sent.

**Resend answers identically for an address that does not exist.** On one
campus, "does this person have an Overtone account" is a question about
somebody's private life.

### Safety

**A block is stored one way and read both ways.** Who blocked whom is worth
keeping, but every surface reads the union — pairing, connections, the inbox,
the socket. A one-way block leaves the blocked person still seeing and still
able to reach someone who has removed themselves, which is the exact situation
it exists for.

Blocking also has to do three things that are not obvious:

1. **Close the conversation.** A thread left alive but unreachable reads as a
   chat that silently stopped working.
2. **Forget the affinity.** An unlocked person who is blocked would otherwise
   sit there unlocked, ready to return the moment the block is lifted.
3. **Say nothing.** A refused sender gets the wording a stranger gets, being
   blocked is never listed, and a report's response carries no outcome.
   Confirming *who* blocked you is what turns a block into a provocation.

**Reporting blocks by default** and says so. Someone who needs one usually
wants the other, and two separate flows at the moment somebody is upset is how
people end up doing neither. Reports are kept after review: one dismissed
report means little, four from unrelated people is the pattern.

### Rate limits

**Address-keyed limits must stay loose, and this is the reason.** A university
is a handful of public addresses in front of thousands of students. Any
per-address limit tight enough to be a real brute-force defence locks out
everyone on campus wifi during the launch rush — the worst moment, and almost
undiagnosable from outside (everyone on wifi refused, everyone on mobile data
fine). So the tight limit is keyed on the **account**, where the attack is
actually aimed; per-address is set only to catch one machine spraying.

**`ratelimit.consume` commits, and must be the first write in a handler.** A
counter rolled back with the request it was counting does nothing on the path
that matters — failed logins are the whole brute-force case, and a failed
login rolls back. It warns if the session already has pending changes.

**Sliding window, honestly.** It turns "a whole fresh allowance at the
boundary" into "about one more request", not into nothing. The estimate
assumes the previous window's uses were spread evenly, so a burst packed into
its last moment is slightly under-counted. Documented and tested as that.

### Identity model

Three separate fields, and the separation is load-bearing:

| Field | Purpose |
|---|---|
| `gender_identity` | Expressive. Open list of 38 + custom text. **Never queried.** |
| `visible_as` | Operational. Multi-select over `{man, woman, nonbinary}` — whose searches you appear in. |
| `interested_in` | Operational. Multi-select — whose profiles you see. |

This is what lets the identity list stay open (including Hijra and Kinnar, which
matter for an Indian campus) without making the matching filter unwritable.
**Visibility is applied in both directions** — mutual, or people get judged by
an audience they never opted into.

### Model stack

| Axis | Choice | Why |
|---|---|---|
| Face | **ArcFace / InsightFace**, self-hosted | CLIP embeds *scene and style* — it calls two photos similar because both are outdoors in a blue shirt. ArcFace's distance metric literally *is* facial resemblance. Detection comes free, which is the quality gate. |
| Voice timbre | **ECAPA-TDNN**, self-hosted | Models the voice, not the words — works identically across English/Hindi/Telugu with no transcription at all. |
| Speech→text | **Sarvam `saarika:v2.5`** | Chosen on **Telugu**, the weakest-supported of the three everywhere. |
| Text | **BGE-M3**, self-hosted | Multilingual natively. |

**`saarika`, not `saaras`.** Sarvam's deprecation notice points at `saaras:v3`,
which is the *wrong model* — saaras **translates to English**, and translating
before embedding is exactly what BGE-M3 exists to avoid. The model name is in
config so the next deprecation is an env change, not a deploy.

**No translation step anywhere.** MT is lossy exactly where these users live
(code-mixed Hinglish, Telugu-English) and adds a cost and failure mode per
profile to reach a space the multilingual embedder already provides.

**Blend: face 0.70 / voice 0.15 / text 0.15**, with secondary terms applied only
*within* the eligible band — where face distance has run out of discriminating
power by construction.

### Pair generation

**Visual band is a percentile of the live pool, not a fixed distance.** Below a
floor there simply isn't anyone who both looks similar and is rated similarly,
so the band widens to the whole pool and random pairing falls out as the natural
limit — no flag to flip, no cliff.

| Pool size | Band |
|---|---|
| < 100 | 1.0 (bootstrap — no constraint) |
| 100–500 | 0.20 |
| 500–1500 | 0.05 (design operating point) |
| > 1500 | 0.02 |

These are **per audience segment**, not per campus. 400 students skewed 80/20
leaves one pool at 80 — still bootstrap.

**Anchor sampling is weighted toward low exposure and high deviation.** Without
it, ANN retrieval returns the same central profiles forever and the tail of the
campus is never seen.

**Scored in Python, not with a native ANN query.** Deliberate: the anchor
weighting and percentile band aren't expressible as one SQL query anyway, pool
sizes at a campus are small, and it keeps SQLite and Postgres behaving
identically in tests. Revisit around a few hundred thousand candidates in one
pool — far past a single-campus launch.

### Infrastructure

**Postgres-backed job queue, not Redis.** Jobs survive restarts and are
transactional with the rows they describe — an asset row and its processing job
commit together or not at all. Redis arrives in phase 05 when chat fanout needs
pub/sub and a queue is no longer the only reason for it.

**Media never transits the API.** Signed URL → browser PUTs straight to Supabase
→ confirm. Removes the class of failure where one large upload exhausts
application memory. Size and type are checked *both* when the URL is issued and
against what storage reports.

**Real models are opt-in outside production** (`USE_REAL_MODELS=true`). A dev
machine must never download gigabytes by surprise. Production always uses them
and fails hard at startup if one is missing.

### Access control

**Caps are per segment, never one headline number.** A single global cap fills
one side in a week and starves the other — the standard way a dating app dies
before it starts. Waitlist admission favours the under-filled segment.

**The cap must clear the pairing floor** (~500/segment). A cap below that
doesn't create desirable scarcity, it creates a product that can't form pairs.

**Rejoin-after-deletion** keeps only a salted email hash, expiring at 12 months,
purely so re-joining queues fairly. It is the one tension with the deletion
guarantee and is disclosed in plain words.

---

## Traps already paid for

Each of these cost real debugging time. Do not reintroduce them.

- **`sorted()` on `(distance, Candidate)` tuples crashes on an exact tie** — it
  falls through to comparing `Candidate`, which has no ordering. Always pass an
  explicit `key=`.
- **SQLite returns naive datetimes; Postgres returns aware ones.** The
  `UTCDateTime` type in `backend/database.py` normalises both directions. Every
  timestamp column must use it, or comparisons raise on one backend only.
- **Supabase's transaction pooler (pgbouncer) breaks asyncpg's prepared
  statement cache.** `settings.db_connect_args` disables it when it sees a
  pooler host. Removing that makes every query after the first fail.
- **`[hidden]` is beaten by any author `display` rule.** `.stack { display:flex }`
  silently un-hid every step of the onboarding form. `base.css` forces
  `[hidden] { display: none !important }`.
- **`audio_key` on a `PromptResponse` holds a `MediaAsset` id**, not a storage
  key, despite the name. It is validated for ownership and kind at write time —
  the round-2 reveal trusts it to resolve a playable URL.
- **`worker.py --status` used to download 2.2 GB** because the availability
  check *constructed* the model. Availability is now `importlib.util.find_spec`
  and touches nothing.
- **Alembic renders custom types without importing them.** `alembic/env.py` has
  a `render_item` hook mapping `UTCDateTime` → `sa.DateTime(timezone=True)`;
  pgvector columns need `import pgvector.sqlalchemy` patched into the generated
  file, plus `CREATE EXTENSION IF NOT EXISTS vector` guarded by dialect.
- **Migrations are squashed, not layered.** Pre-launch with no deployed data,
  each phase regenerates one initial migration. **Stop doing this the moment
  anything real is deployed.**
- **`rm -f` hides failures.** Three SQLite databases got committed because a
  cleanup `rm -f` silently failed against files a running uvicorn still held
  open, and `git add -A` swept them in. Check `git status` before staging.
  (Stopping uvicorn first is the actual fix — on Windows the delete fails
  loudly with "Device or resource busy", which is the good case.)
- **A declined connection has to stay *known*.** Filtering declined rows out of
  the inbox query entirely made the peer fall back into the "unlocked, go write
  to them" list — the app would spend the rest of the year suggesting someone
  write again to a person who said no. Fetch them, then leave them out of every
  group.
- **`grid-template-rows: auto 1fr auto` stretches a short conversation.** One
  message got a 60dvh log and a composer stranded at the bottom of a void. Size
  by content and cap with `max-height`.
- **Red is the decline colour.** It is also `.btn`'s default background, so any
  new positive call-to-action arrives red unless told otherwise. The unlock and
  the composer are explicitly blue.
- **Accent fills need `--on-accent`, not `#fff`.** The palette inverts between
  themes — dark red becomes light red — so hardcoded white heads for
  white-on-pale in dark mode.
- **Grid blowout: a `white-space: nowrap` element sets a floor under every
  track above it.** `.row__preview` made the whole inbox 522px wide inside a
  390px phone — `min-width: 0` on the flex child is not enough, because the
  *grid tracks* still size to min-content. Every grid in a column that
  contains truncating text needs `grid-template-columns: minmax(0, 1fr)`.
- **`.choice` also matches the loading skeleton.** A Playwright
  `wait_for_selector('.choice')` returns on the placeholder and the click
  lands on a `div` with no handler, which looks exactly like a broken feature.
  Target `button.choice`.
- **A keyframe defined in two stylesheets is decided by load order.** `.rise`
  existed in both `pairs.css` and `inbox.css` with different durations, so
  whichever `index.html` loaded last silently won for both. Shared motion now
  lives once, in `base.css`.
- **Demo accounts wear out.** A viewer sees each pair once, so repeated
  Playwright runs exhaust a pool and the next run looks like a broken app.
  Reseed before trusting a failure.

---

## Behaviours that look like bugs but aren't

- **A due round-2 waits behind any outstanding undecided round-1.** Falls out of
  the resume-first rule in `next_pair`. "Finish what's in front of you." Worth
  revisiting as a product call, but it is intentional.
- **`next_pair` called twice without a decision returns the *same* pairing.**
  Idempotency by design — a page reload must not orphan a served pair or be a
  way to dodge answering.
- **Primary photo is the earliest *uploaded* passing photo**, not whichever job
  finished first. The pair view needs one stable photo per person or the rating
  measures photo choice rather than the person.
- **An unlock creates no connection on its own.** One person being certain is
  not two people agreeing; the connection appears only when they write, or when
  the other person crosses too.
- **An unlocked record whose later evidence falls apart stays unlocked.**
  Permanence is the rule, not a missed downgrade — the counts underneath are
  still honest.
- **A viewer can only ever see a given pair once.** So a segment needs at least
  eight people before any unlock is reachable, and a viewer who has worked
  through their pool runs out. Not a bug; a cold-start constraint.

---

## What's left

### Phase 05 — Trust & safety

Done: **block / report** (`backend/safety.py`) and **rate limits**
(`backend/ratelimit.py`), both with the reasoning recorded above.

Still open:
- Real image moderation (nudity, minors, faces) + human review queue. The face
  gate today only checks "exactly one clear face". `Report` rows exist and are
  queryable by `(status, created_at)`, but **there is no reviewer UI** — a
  report today goes into a table nobody opens.
- **Biometric consent** — ArcFace vectors are biometric identifiers under
  India's DPDP Act. Needs explicit, specific consent at upload, deletion that
  actually removes vectors, and a documented purpose.
- Account deletion reaching Postgres + vectors + object storage — now also
  `affinities`, `connections`, `chat_messages`, `viewer_preferences`, `blocks`
  and `reports` (a report must outlive its reporter; `reporter_id` is already
  `SET NULL` for that reason).
- Redis for chat fanout and presence. The chat socket exists
  (`backend/signaling.py`) but is per-process and the frontend does not use it
  yet — the thread view polls on open instead.
- **No block list UI.** Blocking and reporting are reachable from a
  conversation and from a revealed profile, but there is no screen that lists
  who you have blocked or lets you undo it — `GET/DELETE /api/safety/blocks`
  exist and are unused.
- **Nothing reports a photo or a prompt specifically.** The report is about a
  person; `context` carries a connection id when there is one, so a reviewer
  can see the conversation but not "this image".

### Phase 06 — Design system
- Art-directed landing page. Everything else now shares the sky and the motion
  conventions below; the landing page is the one surface that could still be
  more than tidy.

**Motion conventions, now that there are some.** Entrances are 260–440ms on
`cubic-bezier(0.22, 1, 0.36, 1)`, staggered 40–70ms per item via a `.rise`
class and a `--rise-delay` custom property. Sheets use
`cubic-bezier(0.16, 1, 0.3, 1)` over 340ms. `prefers-reduced-motion` is handled
once, globally, in `base.css` — do not repeat the guard per rule. Motion answers
an action or introduces content; nothing here loops or decorates.

### Phase 07 — Calibration & launch
- Seed enough profiles that pairs exist — **cold start is the central risk**. A
  swipe app with 50 users is thin; a pair app with 50 users cannot form
  meaningful pairs at all, and cannot reach a single unlock.
- Tune, on real decisions: the round-2 weight (2.5), the unlock threshold
  (0.70), the confidence level (90%), `RE_EXPOSURE_RATE` (0.5) and
  `preference.TILT` (0.5). These are the five dials.
- Load test, runbook, closed beta, campus unlock.

### Known gaps in what's built
- **Nothing proves a person is still enrolled.** The gate is a mailbox at the
  campus domain, checked once at signup. Alumni addresses often live on, and
  nothing re-checks later. A periodic re-verification is the obvious answer and
  does not exist.
- **Mail is unconfigured out of the box.** `MAIL_PROVIDER=console` is the
  default and delivers nothing; set `smtp` and the SMTP_* variables before
  deploying. The app refuses to start in production without it, so this fails
  loudly rather than silently.
- **Queue refill is on-demand**, not a batch worker job. Fine at campus scale;
  `generate_one_pair` is the building block when it isn't.
- **Round-2 pairings only surface after 48–72h** in real use — pull `due_at`
  forward manually to demo them.
- **Tests run against SQLite only.** CI verifies migrations against Postgres,
  but business logic isn't exercised against pgvector.
- Git history contains three committed SQLite blobs (untracked since, but still
  in history). Worth a rewrite before the first push if that matters.
- **The thread view does not live-update.** It loads on open and after you
  send; the socket in `backend/signaling.py` is wired and access-checked but
  the frontend does not connect to it yet. Waits for Redis in phase 05.
- **No notification of any kind** when a request arrives. The inbox has to be
  opened. Mail sender first, then this.
- **`/inbox` holds the thread in page state, not the URL**, so a conversation
  cannot be linked to or restored by reload. The router matches exact paths and
  has no params; add them when a second surface needs them. Profiles and
  threads open in a sheet (`src/components/sheet.js`) rather than a route for
  the same reason.
- **The inbox badge on the pair view costs a full `/api/connections` call** on
  mount, which serialises every unlocked profile just to decide whether to show
  a 7px dot. Wants a cheap count endpoint before launch.

---

## Open questions needing a human answer

1. **What is the initial per-segment cap?** Must clear ~500/segment for pairing
   to work at its design point. If the campus can't supply ~1000 verified
   students, the honest options are a lower cap with a longer bootstrap stage,
   or launching two campuses at once.
2. **Does a person know they appeared at all?** The weekly count is the safe
   version; a live "you were shown to someone" signal is more engaging and
   closer to the line drawn on rating privacy.
3. **Which STT provider survives real audio?** Sarvam is chosen and wired, but
   validated only against a synthetic tone. Record 20s each of Telugu, Hindi and
   code-mixed English from a real student and compare providers.
4. **Is seven picks the right price for an unlock?** It falls out of 0.70 at
   90% confidence rather than being chosen directly. Lower either and unlocks
   come faster and mean less; raise them and most viewers never reach one. This
   is the number that decides whether the app feels alive, and it cannot be
   settled without watching real people use it.
5. **Should a declined request be visible to the sender at all?** Today it
   simply disappears from both inboxes — no "declined" state shown, and no way
   to tell it apart from a request still waiting. Kinder, but it does leave
   people hanging; the alternative is telling someone they were turned down on
   a campus where they will see that person again.

---

## Conventions

- **Tests are the contract.** 243 and rising; every bug found gets a regression
  test. `tests/test_pairing.py` (55) splits pure selection logic from DB wiring
  deliberately — check the module docstring before adding to it, and the same
  split is repeated in `test_affinity.py` and `test_preference.py`.
- **Comments explain *why*, never *what*.** The code says what.
- Option-set fields are validated strings against `backend/options.py`, **not**
  database enums — a native enum needs a migration to add one value and this
  vocabulary will grow. Lifecycle states (which are genuinely closed) *are*
  enums.
- **Domain and HTTP are separate modules, and the names do not match on
  purpose.** The pairs of files are worth knowing before looking for anything:

  | Domain | HTTP | What lives there |
  |---|---|---|
  | `pairing.py` | `pairs.py` | generation, serve/decide, round scheduling |
  | `connections.py` | `inbox.py` | requests, replies, declines, the inbox |
  | `rating.py` (pure) | — | Glicko-2 and the Wilson interval, no database |
  | `rating_service.py` | — | the only thing touching both maths and SQL |
  | `affinity.py` | — | the unlock: counts, classification, permanence |
  | `preference.py` | — | the online face model and its tilt |
  | `profile_view.py` | — | the two serializers, shared by pairs and inbox |

- `backend/rating.py` and `backend/preference.py` stay pure — no session, no
  clock. That is what lets the arithmetic be tested precisely and the wiring be
  tested separately.
- Never commit `.env` or `*.db`. Both are gitignored; verify staging anyway.
- **Stage explicit paths, not `git add -A`.** It swept a favicon and its
  `<link>` line into an unrelated commit twice in one session. `git status`
  before staging is the habit; naming the files is the fix.
- **Re-exported `src/styles/overtone_favcon.png`? Run
  `python scripts/make_icons.py`.** The 1024px master is the source; `public/`
  holds the derived sizes a browser asks for, and they are committed rather
  than built so a deploy never needs Pillow. They silently go stale otherwise —
  it happened twice.
