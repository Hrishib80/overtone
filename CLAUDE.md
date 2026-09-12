# Overtone

Campus dating built on **pairwise comparison** instead of swiping. You are shown
two people who look alike, photos only, and you pick one. Every pair returns
days later with full profiles, and that second, informed choice counts for more.

This file is the project's memory. The code records *what* was built; this
records **why**, and the traps that were paid for once already. Read it before
making architectural changes.

---

## Current state

Phases 00–03 are complete and committed. **174 tests passing**, lint clean,
migration round-trips, frontend builds, and the pair loop runs end to end in a
browser.

```
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

It is the source of truth for the mechanic, the roadmap, and the numbers
(percentile bands, round weights, population thresholds). Update it when a
decision in it changes.

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
python scripts/seed_demo.py --scope demo          # 8 fake people, local SVG portraits

python -m uvicorn backend.app:app --port 8000     # API
npx vite --port 5173                              # frontend
python worker.py                                  # background jobs (optional locally)
```

Demo accounts: `ravi@demo.edu`, `aditi@demo.edu`, … password `overtone2026`.

```sh
pytest                       # 174 tests, no network, no models needed
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

---

## What's left

### Phase 04 — Preference & unlock
- Online preference model trained on decided pairs (the difference vector
  between chosen and rejected is clean supervised signal).
- **Wilson lower-bound unlock.** `wilson_lower_bound()` already exists and is
  tested. At 95% confidence / 0.85 threshold a viewer needs ~25 comparisons of
  the same person — unreachable at launch volumes. **Start at 90% confidence and
  ~0.70**, which is still strong given the 0.50 expected rate against
  similar-rated opponents.
- Deliberate re-exposure: once a viewer picks someone, preferentially re-pair
  that person against fresh similar opponents to test the hypothesis.
- **Message requests** — crossing the threshold sends *one* opening message into
  a request inbox; a reply opens the chat. Mutual crossing skips the request.
- `MatchRecord` / `ChatMessage` are swipe-era leftovers still used by
  `backend/chat.py`; they get reworked here.

### Phase 05 — Trust & safety
- Real image moderation (nudity, minors, faces) + human review queue. The face
  gate today only checks "exactly one clear face".
- Block / report on every surface; rate limits.
- **Biometric consent** — ArcFace vectors are biometric identifiers under
  India's DPDP Act. Needs explicit, specific consent at upload, deletion that
  actually removes vectors, and a documented purpose.
- Account deletion reaching Postgres + vectors + object storage.
- Redis for chat fanout and presence.

### Phase 06 — Design system
- Carry the two-pole palette through remaining surfaces.
- Art-directed landing page.

### Phase 07 — Calibration & launch
- Seed enough profiles that pairs exist — **cold start is the central risk**. A
  swipe app with 50 users is thin; a pair app with 50 users cannot form
  meaningful pairs at all.
- Tune round-2 weight, unlock threshold, confidence level on real decisions.
- Load test, runbook, closed beta, campus unlock.

### Known gaps in what's built
- **No mail sender.** Verification tokens are returned in the response outside
  production and only logged in it. Needs a real provider before launch.
- **Queue refill is on-demand**, not a batch worker job. Fine at campus scale;
  `generate_one_pair` is the building block when it isn't.
- **Round-2 pairings only surface after 48–72h** in real use — pull `due_at`
  forward manually to demo them.
- **Tests run against SQLite only.** CI verifies migrations against Postgres,
  but business logic isn't exercised against pgvector.
- Git history contains three committed SQLite blobs (untracked since, but still
  in history). Worth a rewrite before the first push if that matters.

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

---

## Conventions

- **Tests are the contract.** 174 and rising; every bug found gets a regression
  test. `tests/test_pairing.py` (40) splits pure selection logic from DB wiring
  deliberately — check the module docstring before adding to it.
- **Comments explain *why*, never *what*.** The code says what.
- Option-set fields are validated strings against `backend/options.py`, **not**
  database enums — a native enum needs a migration to add one value and this
  vocabulary will grow. Lifecycle states (which are genuinely closed) *are*
  enums.
- `backend/rating.py` stays pure. `backend/rating_service.py` is the only thing
  that touches both the maths and the database.
- Never commit `.env` or `*.db`. Both are gitignored; verify staging anyway.
