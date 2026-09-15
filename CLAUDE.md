# Overtone

Campus dating built on **pairwise comparison** instead of swiping. You are shown
two people who look alike, photos only, and you pick one. Every pair returns
days later with full profiles, and that second, informed choice counts for more.

This file is the project's memory. The code records *what* was built; this
records **why**, and the traps that were paid for once already. Read it before
making architectural changes.

---

## Current state

Phases 00–06 are complete; phase 07 — calibration and launch — is what is
left. **438 tests passing**, lint clean, migration round-trips, frontend
builds, and the whole loop — pair, unlock, request, reply — has been driven
end to end in a browser at phone and laptop width.

**There is no email anywhere.** An account is a username and a password; it
is active the moment it is created, and the only way anybody learns they have a
message is a count on the navbar. All of that was traded deliberately — see
*No email, anywhere*, *Usernames* and *The shape of the app* below.

```
e91479e  Fix consent returning the wrong notice version about 5% of the time
deedd0f  A harness for the unlock dials, since tuning them needs data nobody has yet
75c2039  Clear the four outline:none rules rather than leave them suspicious
cc7d217  Audit what is not text, and fix the two things it found
6160286  A count endpoint for the bar, and the socket test that was called impossible
518e6f0  Make the photo controls legible, and pressable at all
c7cda5d  Bring the project memory back in line with the code
76c5a0f  Profile and messages, laid out properly — and a scrim that actually works
b31584d  A navbar, and My type split from Keep choosing you
50a2f79  Say what is waiting, on the only way out of the pair view
3641c4c  Show people who keeps choosing them, and let them answer
fc05d90  Tell people when somebody has written to them
e3fd64c  Sweep every screen for text nobody can read
4094c69  New mark: two circles, overlapping
857d14f  Make the landing ground follow the theme
12d72a8  Phase 06: a landing page that is the mechanic
380c2bb  Phase 05: automatic photo screening, held for a human
bf21aa2  Phase 05: live chat, and one write path for a message
b273e43  Fix the moderation migration against a table that has rows
6599d62  Phase 05: the review queue, and suspension
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
python scripts/seed_demo.py                       # 20 fake people, local SVG portraits

python -m uvicorn backend.app:app --port 8000     # API
npx vite --port 5173                              # frontend
python worker.py                                  # background jobs (optional locally)
```

`MEDIA_ROOT` defaults to the **relative** path `media_uploads`, so it resolves
against whatever directory uvicorn was started from. Start the API somewhere
else and the demo portraits are still on disk but the app is looking in an
empty folder — every pair renders as two blank tiles, which reads as a broken
feature rather than a wrong path. Start it from the repo root, or set
`MEDIA_ROOT` absolutely.

Demo accounts sign in by first name, lowercase — `ravi`, `aditi`, … — all
with password `overtone2026`. `seed_demo.py` prints the full list.
Ten women and ten men, deliberately balanced: an unlock costs seven
comparisons of one person and a pair may only be shown once, so a viewer needs
at least eight candidates before any unlock is reachable. A lopsided pool
silently makes the whole mechanic impossible for whoever is on the short side
— twelve women and four men meant no woman could ever unlock anyone.

```sh
pytest                       # 438 tests, no network, no models needed
python scripts/manage.py stats   # pool size per segment — the number to watch
python scripts/manage.py reviewer --username you   # open the review queue
python scripts/manage.py admin --username you      # open the admin portal (/admin)
python scripts/check_storage.py  # why uploads are or are not working
python scripts/check_contrast.py --token "<jwt>"   # text nobody can read, both themes
python scripts/calibrate.py --sweep   # what the unlock dials cost, before real data
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
preference engine and something that hurts people in a community where they
recognise each other. Decided explicitly; do not soften it.

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

### The shape of the app

**Six destinations, one bar, on every signed-in screen.** Before this the app
was a set of rooms with one door each — the pair view had a single footer
link and everything else was reachable only by going back through it, so four
of the five things you could do were invisible.

**"My type" and "Keep choosing you" are different facts and must be different
pages.** My type is who *you* keep picking: a description of your taste,
assembled from your own choices. Keep choosing you is who picks *you*, often,
over whoever they were shown against. One is an output of your behaviour and
the other of everybody else's. They were one page called "your people" for
exactly one session, and the reason that was wrong is that somebody scanning a
single pile of faces cannot tell which of them they chose and which chose
them — which is the only thing either list is saying.

**The count in the bar is the entire notification system.** There is no email
and no push, so a number beside Messages is how somebody learns they have one.
That is why it lives in the bar rather than on the Messages page: a nudge you
have to already be looking at is not a nudge. `refreshNav()` invalidates it;
anything that changes a count calls it.

**The review queue is not in the bar.** It is staff-only and would be a
permanent empty tab for everybody else, so it sits beside Sign out and only
for a reviewer.

**There is a profile editor now.** Until this, the only way to set any of it
was the onboarding funnel — which you go through once and can never return
to, making a typo in a prompt answer permanent. Each section saves on its own
rather than the page having one Save: they are unrelated edits, and failing
all of them because one select is empty is the behaviour of a form rather than
of a profile.

### No email, anywhere

**Email is gone entirely** — not only verification but the address itself. No
SMTP, no Google, no verification link, no `notify`, no `mail.py`, no
`send_email` job, and no email field on the join form. What replaced the
notification half is the count in the navbar; what replaced the identity half
is a username (see *Usernames*); what replaced the verification half is
nothing, and that is the part worth being honest about.

**Nothing proves anything about who somebody is.** Anyone can register as
anyone, and a banned account can come back for the price of a new username,
which is free. Holding the line instead: the 18+ check, blocking,
reporting, the review queue, rate limits. If ban evasion becomes real the
answer is phone verification or invite codes — something that costs the
attacker something — rather than putting the email round trip back, because a
mailbox is free.

**A forgotten password is a lost account.** There is nowhere to send a reset.
The join form and Settings both say so. Do not add a recovery flow that asks
for an email — that is the removed thing coming back by the side door. If
recovery is ever needed, the honest shapes are a recovery code shown once at
signup, or a reviewer resetting it by hand.

**Old addresses are still in `users.email`.** Accounts made before usernames
keep the address they joined with; the column is nullable, nothing reads it,
and new accounts leave it empty. Clearing those is a one-line migration and a
decision somebody should make on purpose — data nobody uses is still data a
breach would expose.

**The seam is still there.** `users.email_verified_at` and the
`email_verifications` table are kept: the rows are the record of who verified
while it existed, and the column is where the gate goes back. Nothing sets it
now, and the check that read it is gone from `profile.submit`.

### Being chosen

The app always knew who a viewer kept picking. It never told the person being
picked, so being chosen was something that happened to you rather than
something you could act on. `admirers` is that half: the people who unlocked
*you*, their profiles in full, and a way to write back.

**This was a deliberate reversal of a recorded decision, and the cost is worth
naming.** Until now an unlock was private to the person who made it — they
could sit on it, and the other person never knew. That privacy is gone: unlock
somebody and they can see you did. The upside is that the loop no longer
depends on the sender being the one brave enough to write; the downside is
that "I keep choosing them but I'm not ready to say so" is no longer a state
the product supports. If that turns out to matter, the seam is a flag on
`Affinity` and a `.where()` in `admirer_ids`.

**Writing to somebody who chose you opens the conversation outright**, with no
request. They declared first; answering is not a thing they need to approve a
second time. `send_request` now accepts an unlock in *either* direction, and
the existing `mutual` branch already does the right thing once it is let
through — that is the whole change.

**The share is withheld below `MIN_AUDIENCE_FOR_SHARE`.** One keen person out
of three is "33%", which reads like it means a lot and means nothing. Below
the floor the API sends `share: null` and the UI must not invent a number —
the same objection the Wilson interval answers for the unlock, in its blunt
form, because this number is shown to a person rather than used to decide
anything.

**The denominator is the audience, not the fan club.** Everyone with a decided
comparison, not everyone who unlocked you — dividing admirers by admirers is
100% for everybody, forever.

**This is the closest the app comes to showing somebody a rating**, and it is
worth being honest that it is close. What keeps it on the right side of the
line: it is about *you*, shown only to *you*, it is a count of people rather
than a score, and it is never comparative — there is no rank, no "better than
80% of users", and no way to see anybody else's. Do not add one.

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

**White is the display voice, and it is now exactly one element.** White on
the sky is **2.1:1** and fails every threshold there is, so `.pairs__title` —
the 47px "Which man?" — is the only text in the app allowed to use it, where
size and weight carry it and nobody has to read it twice. Everything else
takes `--on-sky-soft`, the deep pole, at **5.5:1**. Both, plus a deep-navy
`--sky`, are redefined for dark mode.

That rule used to say "the heading *and its single supporting line*", and the
supporting line was the bug: "pick the one you prefer" was 17px of 300-weight
white at 2.0:1 — the least readable text on the screen carrying the most
necessary sentence on it. Three page titles had drifted the same way at 20px.
An exception written loosely enough to include a second case will collect a
fourth; `scripts/check_contrast.py` is the thing that keeps it at one.

**The heading names what it is showing** — "Which woman?", "Which man?",
"Which person?" — from `segment` on the pair response. That is the viewer's
own `interested_in` choice reflected back; it says nothing about either
subject, so round 1's photo-only rule is untouched.

### The landing page

**The hero is the mechanic, not a description of it.** The old version
explained pairwise comparison in a sentence and then offered two circles that
*looked* like the mechanic but were actually the nav. Now the circles are a
real pair: the app's own question over them, a choice that answers rather than
navigates, and the difference named back to you after you pick. Somebody
understands the product by doing it once, in about a second, and it is the
only thing on the page a competitor could not also say.

**Three rounds, then it reads your picks back** — "warmer, looser, heavier."
Being told what you chose is the moment the idea lands; three shapes is a
parlour trick, three faces is a preference, and the copy says exactly that.

**The two forms are siblings, never opposites.** Red against blue would be two
brand colours and a trivial choice, while the whole claim is that the pair is
*nearly the same*. The difference has to be small enough that you notice
yourself preferring rather than deciding.

**Each side owns its own word.** A fixed line per round — "you went for the
warmer one" whichever you picked — would be the page telling you what you
chose, which is precisely the thing a swipe app does.

**Abstract on purpose.** Inventing faces for a dating app's front page would
be a lie about what is inside.

**Round two gets the only white surface on the page.** It is the idea nothing
else in the category has, so it is lifted off the ground rather than set as a
third column of the same blue.

### Joining

**Anyone may join, and a person decides who goes live.** The campus domain
check is gone, and so are `Scope`, per-segment caps and the former-member
hash. One pool, one rule at the door: you have to be 18. Then a second rule,
added after the first: a finished profile waits for an admin.

**The waitlist is approval, not capacity.** An earlier waitlist existed to cap
each segment and was removed with the campus layer. This one is different in
kind — nobody waits for a slot, they wait for a person to look — and it was
asked for explicitly. `onboarding → waitlisted → active`: `submit` puts a
finished profile on the waitlist, an admin approves it in `/admin`, and
approval is the whole gate, because everything that shows a person to anyone
or lets them reach anyone already asks for `active` (`require_participant`,
pair eligibility). There is no second list to keep in sync.
`REQUIRE_APPROVAL=false` turns it off and every finished profile is a member
again; the test suite runs with it off, and `test_waitlist.py` switches it on.

**Sent back is still waitlisted.** An admin can ask for a change with a note.
The person stays on the waitlist but out of the queue, reads the note word for
word, edits in the ordinary profile editor (onboarding does not reload saved
answers, so sending them back through it would hand them an empty form), and
resubmits, which clears the note and starts the wait again. An admin can still
approve from sent back — a note is a request, not a verdict.

**Invite codes are the other door, and they are deliberately narrow.** A
member an admin let in has a code; somebody who registers with it skips the
waitlist. `backend/invites.py` holds three limits, each on how far one vouch
reaches: only admin-approved members can invite (an invitee cannot invite in
turn, or one approval becomes a chain); each has `INVITES_PER_MEMBER` (5),
taken at *registration* so a code cannot be sprayed at fifty people hoping
five finish; and the vouch is checked again at submit, so an inviter suspended
in between vouches for nobody. Accounts already active before approval existed
count as let in. The admin portal lists everybody who came in on a code and
whose code it was, because this door skips the queue.

**Admins are a separate flag from reviewers**, granted only by `manage.py
admin` (which refuses an account that is not itself active), with no endpoint
that can set it, and `/api/admin` answers 404 to everybody else. Reviewers
answer reports about members; admins decide who becomes one.

That was a real trade and the cost is worth stating plainly. The campus domain
was the identity anchor — it bounded the population to people who genuinely
belonged somewhere, and it made ban evasion expensive, because coming back
meant obtaining another university mailbox. A personal account removes both.
What carries that weight now: the 18+ check, blocking, reporting, the review
queue and rate limits. **If ban evasion becomes a real problem the answer is
phone verification or invite codes, not bringing the domain back** — the
domain only ever worked because a second university mailbox is hard to get.

Email verification used to be named here as carrying part of that weight. It
does not any more — email is gone entirely, and *No email, anywhere* above is
the honest account of what that costs.

### Usernames

**An account is a username and a password.** The rules live once, in
`backend/usernames.py` — 3 to 20 characters, lowercase letters, digits, `_`
and `.`, starting with a letter or digit, no trailing or doubled full stop, and
a short reserved list (`admin`, `overtone`, `support`, …). The registration
route, the live availability check and the tests all call it.

**Private, not a handle.** No member ever sees another's username; they see a
display name. A public username would be a way to find a specific person, which
on a campus is exactly what a dating app should not hand out. Reviewers see it,
because a report has to name an account unambiguously.
`test_other_members_never_see_a_username` scans the profile serialiser for it.

**Case-insensitive by storage, not by query.** Every username is stored
lowercase, so the plain unique index is already case-insensitive on SQLite and
Postgres alike. `RaviK` and `ravik` are one account — two that differ by a
capital are an impersonation waiting to happen — and signing in as `Ravi` on a
phone keyboard works.

**A wrong username and a wrong password get the same answer**, including a
name that could never have been registered, so the login form is not a way to
learn which usernames exist. The availability check *does* reveal that, and
that is accepted: registration has to refuse a taken name anyway, so the fact
is one submission away regardless. It is rate-limited per address, loosely.

**The migration gives every old account a name** from the part of its address
before the `@` (`shri@gmail.com` → `shri`), numbering collisions oldest-first.
It carries its own copy of the rules rather than importing them, because a
migration has to keep producing the same result after application code
changes; `test_the_migration_and_the_app_agree_about_the_rules` checks the two
copies started identical. Its downgrade refuses if any account has no email to
fall back to, rather than inventing addresses that look real.

**`AppError` can name a field now** (`AppError(msg, field="username")`), and
the response carries it in the same `fields` shape a validation error uses —
so "that username is taken" marks the username box instead of only toasting.

**There is no population boundary at all.** Every active account is in one
pool. The seam where one would go back is a single `.where(...)` in
`pairing._eligible_candidates`, plus whatever column decides which pool
somebody is in — that is the whole change, if per-college or per-city pools
are ever wanted.

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

### Review, and suspension

**Reporting without review is a promise the product does not keep.** The rows
accumulate, nobody opens them, and the person who reported learns that
reporting does nothing. `backend/moderation.py` is the other half, and three
choices shape it:

**The unit of review is the person, not the report.** Four reports about one
account are one question — is this person doing the thing? — and answering it
four times would mean answering the fourth with the first three already
decided. So a reviewer acts on a subject and every open report about them
closes in that act. The queue is ordered by open count, not arrival: four
unrelated reports is the pattern worth seeing first. Age breaks ties so
nothing sits forever.

**A note is required on every decision, dismissals included.** A dismissal
with no reason recorded is indistinguishable from nobody having looked, which
is the exact failure the queue exists to end. `reviewer_id` is stored too — "a
human reads it" is only a real promise if the human is named.

**Reviewers are made from the command line** (`manage.py reviewer --username`).
There is deliberately no endpoint: the one account that can suspend other
people must not be reachable from a surface an attacker already holds a
session on. `test_nothing_over_http_can_make_a_reviewer` pins that. A
non-reviewer gets **404, not 403** — that a moderation surface exists at this
path is itself information.

**Suspension removes somebody from other people's experience, not from their
own data.** A suspended account still signs in, still reaches settings, still
withdraws biometric consent and still deletes itself. Suspension is a
judgement about conduct towards others; it is not a forfeit of what is theirs.
That is why `require_member` guards pairs, inbox, media, safety and profile but
deliberately **not** `auth` and `account`.

**A suspension can be lifted.** One that could not would be one nobody makes on
thin evidence — or on good evidence, since the cost of being wrong is
identical either way.

**Suspension hides somebody from every surface, at read time.** For a while
only pair generation honoured it: a suspended person still sat on other
members' My type and Keep choosing you with a full profile, could still be sent
a first message, stayed in other people's Messages where anybody could keep
writing to them, and could hold a live socket. `safety.hidden_accounts` is now
the one question all of those ask — inbox, counts, `reach`, `send_request`,
`post_message`, `_is_participant`. Nothing is written into connection or
affinity rows, deliberately: lifting a suspension has to put every surface back
exactly as it was, and `test_suspension_reach.py` checks that half too.

**An unfinished account cannot take part.** `require_member` only ever turned
away *suspended* accounts, so an account still in onboarding passed it — and
onboarding asks who you want to see before it asks for consent, a photo or a
profile. Answer that and stop, and the pair view served real members' photos,
unlocks worked, and messages could be sent. `require_participant` requires
`active` on the pairs and inbox routers (on the router, so a new route cannot
forget it). Profile, media and account stay on `require_member`, because they
are the steps that finish joining.

**A report can name one photo or one answer** (`subject_media_id`,
`subject_prompt_id`), verified at write time to belong to the person being
reported. A report attached to the wrong evidence would be worse than one
attached to none. Reporting a single photo does **not** block by default, the
way reporting a person does — flagging an image is a smaller act than cutting
somebody off.

### Rate limits

**Address-keyed limits must stay loose, and this is the reason.** A university,
office or shared connection is a handful of public addresses in front of many
people. Any
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

### Consent, and erasure

**A face embedding is a biometric identifier** under India's DPDP Act, so the
permission to compute one has to be specific, informed and withdrawable. That
shapes four things, all in `backend/privacy.py`:

1. **Asked at the photo step, not in terms of service.** It is the first
   moment the question means anything to the person answering it, and the
   words are served by the API (`purpose`) rather than living only in the
   frontend — consent to an unstated purpose is not consent.
2. **Checked before the signed URL is issued**, not before the embedding is
   computed. The honest place to stop is before somebody's face reaches our
   storage at all. The worker checks again, because consent can be withdrawn
   while a job sits in the queue; a photo caught there is *rejected*, not left
   unprocessed, since an unprocessed photo never went through the face gate.
3. **`notice_version` is stored with the grant.** When the wording changes in
   a way that alters what is being agreed to, the old answer is an answer to
   the old question: `needs_restatement` goes true and a second row is written
   rather than the old one edited. Which words were agreed to, and when, is
   the entire record.
4. **Withdrawal is one click and asks nothing back.** A withdrawal guarded by
   a password, a survey or a confirmation chain is harder than the grant was,
   which is the thing the law exists to prevent. It deletes `face_vector`
   immediately, which also removes the account from pairing on its own —
   `_eligible_candidates` requires a vector, so there is no separate "disabled"
   state to keep in sync.

**Erasure is explicit, not a cascade**, even though every foreign key to
`users` is already `ON DELETE CASCADE`. Three reasons: SQLite does not enforce
`ondelete` unless told to, so the whole test suite would exercise a behaviour
production does not have, in the direction that silently leaves data behind;
`jobs.subject_id` has **no** foreign key at all, and those rows carry user ids
and object keys; and "it cascaded" is not an answer to "what did you delete" —
`erase()` returns a count per table so the claim is checkable. Storage objects
go first, while the rows naming them still exist. The cascades stay as a
backstop.

**Deleting an account takes the whole conversation, not just its half.**
Deleting only the messages this person *sent* would leave the other side of a
chat hanging off a connection that no longer exists. But a report they *made*
survives them with `reporter_id` nulled: four reports from unrelated people is
the pattern, and losing the count because a reporter left would hide exactly
the behaviour worth seeing.

**The test that matters is `test_deleting_an_account_leaves_nothing_that_names_them`.**
It scans every table in the schema for the user id as a substring — which also
catches it inside a rate-limit bucket and a connection key — rather than
asserting the deletes we happened to remember writing. It fails when somebody
adds a table with a user id and forgets `OWNED_BY_USER`, which is how this
requirement breaks in practice.

### Live chat

**There is one write path for a message, and it is HTTP.** The socket used to
insert a `ChatMessage` of its own, which meant the single-message limit on an
unanswered request, blocking and the rate limits applied to one way of sending
and not the other — and it rebroadcast the *client's own payload*, so the
text, id and timestamp the peer saw were whatever the sender's browser
claimed. Now the browser POSTs, `connections.post_message` applies the rules,
and the handler publishes what it committed. `RELAYED_TYPES` is the socket's
allowlist and contains only `typing` and `read-receipt`; a `chat-message`
reappearing in it would restore the hole, and a test pins that.

**Publish after the commit, never before.** An event announcing a message that
then failed to save would put text on somebody's screen that does not exist.

**The socket is an accelerator, never the record.** Everything it carries is
already in Postgres and the client refetches on open, so a dropped event costs
latency and nothing else. That is what makes best-effort fan-out honest rather
than a corner cut — and it means a socket that never connects degrades to
exactly the behaviour before it existed. Never publish something that only
exists as an event.

**Redis is optional, and its absence is a supported mode.** `backend/bus.py`
has two backends behind one interface: Redis pub/sub when `REDIS_URL` is set,
and an in-process implementation when it is not. One worker is the correct
deployment at campus scale, so the local bus is not a stub — it is what
development, the tests and a single-worker production all run. Scaling out is
an environment variable.

**Each subscriber gets its own queue.** A shared one makes two sockets in a
room competing consumers — each receiving every other message — which reads as
a flaky connection rather than a bug and only appears with two people actually
talking. Queues are bounded and drop rather than block, because a wedged
socket must not stall the HTTP request that published.

**Typing is re-stamped with the authenticated sender**, never relayed as
given, or a client could type on somebody else's behalf. It expires on the
receiving side rather than waiting for a "stopped" event, so a peer who closes
the tab mid-word does not leave the dots up for ever.

### Screening photos

**Three outcomes, not two.** A classifier with only pass and fail either
rejects real people's photos or lets things through, because the middle of its
score distribution is genuinely ambiguous — a beach photo, a breastfeeding
photo, a painting, a low-cut top at an unlucky angle. The third outcome is
`held`: nobody but the owner sees it, and a person looks. Having that state is
what lets the automatic thresholds sit where they are honest rather than where
they are least embarrassing, and it is the reason the review queue was built
first.

**Nothing is ever rejected on an age estimate.** buffalo_l's genderage head is
routinely out by several years and the two errors do not cost the same: a
wrongly-rejected nineteen-year-old is told by a machine that they look like a
child with nowhere to argue, while a wrongly-held one waits. Under-age photos
are held, always. The threshold is 21 rather than 18 because the point of the
number is to catch what deserves a second look, not to be a birthday check.

**"Did not look" and "looked and found nothing" must never collapse.** A
detector that was supposed to run and failed *holds*; a deployment with no
detector at all *passes*. `screener().screens` is the flag that separates
them, and `StubScreener` reports `False` rather than clean results — a
moderation system that treats an absence as approval is worse than one that is
visibly switched off. This is how moderation quietly stops working: the model
errors, everything sails through, and nothing in the product looks different.

**The policy is pure and lives in `backend/screening.py`**, away from the
model, for the same reason `rating.py` is pure — the thresholds are the part
that will be argued about and tuned on real photos, and they should be
readable and testable without loading a network.

**Age costs nothing extra.** buffalo_l already carries a genderage head and
the face pass already runs, so the estimate arrives on `FaceResult`. Loading a
second model to answer the same question would be another network in memory
for nothing. Only nudity needs its own detector (NudeNet — ONNX, CPU, and it
returns *labelled* boxes, which is what a reviewer reads, rather than one
opaque "nsfw" number).

**`review_approved` is sticky.** A human decision must not be overturnable by
the next automatic pass, or the queue becomes a thing reviewers do twice.
Approving sets it, returns the photo to `uploaded` and re-enqueues the job —
the worker elects the primary and embeds, because that is where the model
lives.

### Presence, and a column nothing reads

`users.last_active_at` is written from `current_user` rather than only at
login, at most once per five minutes (`ACTIVITY_RESOLUTION`) — people stay
signed in for weeks, so "last seen" taken from the last sign-in would have
said somebody reading their inbox right now was last here on Tuesday.

**It exists for a reason that no longer does.** It was there to suppress email
to somebody already in the app, and there is no email. Nothing reads the column
today — grep it and the only hits are the write and the schema. Two honest
options: drop it, or keep it as the seam for presence in chat, which is the
obvious next thing to want and the one thing it is already shaped for. It is
kept, at the cost of a write per session per five minutes, on that bet.

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

These are **per audience segment**, not per population. 400 members skewed
80/20 leaves one pool at 80 — still bootstrap.

**Anchor sampling is weighted toward low exposure and high deviation.** Without
it, ANN retrieval returns the same central profiles forever and the tail of the
population is never seen.

**Scored in Python, not with a native ANN query.** Deliberate: the anchor
weighting and percentile band aren't expressible as one SQL query anyway, pool
sizes at a campus are small, and it keeps SQLite and Postgres behaving
identically in tests. Revisit around a few hundred thousand candidates in one
pool — far past a single-campus launch.

**A queued pair is checked again when it is served, by the same rule that
generated it.** Round 2 is scheduled at every round-1 decision and arrives 48
to 72 hours later with full profiles — and serving it used to trust that
everybody eligible then was eligible now. So a block either way, a suspension,
a consent withdrawal or a change of audience in between still revealed a full
profile two days later, including to somebody who had blocked the person, and
of somebody told in Settings that withdrawing means "you stop appearing in
pairs". `_eligible` is the one filter both paths use; `still_showable` runs it
for the two subjects in `next_pair` (outstanding, due round 2 and queued round
1) and in `record_decision`, and a pair that fails is marked `withdrawn` rather
than deleted. A decision on a pair that stopped being showable while it sat in
an open tab is refused with 409, and the withdrawal is committed before the
refusal, because the route rolls back on an error and a pair left `shown` would
be served straight back.

### Infrastructure

**Postgres-backed job queue, not Redis.** Jobs survive restarts and are
transactional with the rows they describe — an asset row and its processing job
commit together or not at all. Redis arrives in phase 05 when chat fanout needs
pub/sub and a queue is no longer the only reason for it.

### Photos

**Three slots, minimum one, and the first is not like the other two.** It is
the photo the pair view shows, so it can be *replaced* but never removed — an
account with no face has nothing for the mechanic to work on. The other two can
be replaced or removed freely.

**Replacement is one step, not delete-then-upload.** Deleting first would dip
below the minimum; uploading first would need a fourth slot. So `replaces`
travels on both the ticket request (to let the cap through) and the confirm
(where the new photo inherits the old one's `display_order` and the old row
goes). Position is what makes "replace your first photo" mean the new one *is*
first, rather than landing third.

**`display_order` decides primary, not upload time.** The worker picks the
first *slot* that passes the face gate. Confirm also claims primary when
nothing holds it, so a profile with one photo shows a face without waiting for
a background job to run.

**A ticket nobody uploaded to is not a photo.** It does not count toward the
cap, does not satisfy the minimum, and is not listed — otherwise opening the
file picker and changing your mind burned a slot and drew a blank tile.

### Storage

**Media never transits the API.** Signed URL → browser PUTs straight to Supabase
→ confirm. Removes the class of failure where one large upload exhausts
application memory. Size and type are checked *both* when the URL is issued and
against what storage reports.

**`STORAGE_PROVIDER=local` is the development default, and it exists because
the app was unworkable without it.** Every upload needed a live Supabase
project, so a paused or renamed project made photos impossible to add and said
only "Try again". `local` writes into `MEDIA_ROOT`, which is already served at
`/media_uploads`. Bytes do pass through the API in that mode, which is the one
property the signed-URL design exists to avoid — so production refuses to start
on it.

**Supabase needs the service role key, not the anon key.** Signing an upload is
a server-side write; the anon key is subject to row-level security and is
refused with a bare `400`. The bucket must also already exist. Both failures
look identical from the outside, which is why storage errors now log the
provider's own message instead of swallowing it — and why
`scripts/check_storage.py` exists. It reads the `role` claim out of the key
itself, so "did I paste the anon key" is answered rather than guessed.

**Real models are opt-in outside production** (`USE_REAL_MODELS=true`). A dev
machine must never download gigabytes by surprise. Production always uses them
and fails hard at startup if one is missing.

### Balance, without a cap to enforce it

Caps and the waitlist are gone, but the problem they existed for has not: **a
population that fills up on one side and starves the other is the standard way
a dating app dies before it starts.** There is now nothing in the code stopping
that — it has to be watched instead.

`python scripts/manage.py stats` is the thing to run. It reports per segment,
not as a headline total, because a thousand members split badly is still a
broken app for whoever is on the short side. The two numbers that matter:

| Segment size | What works |
|---|---|
| under 8 | no unlock is reachable at all |
| under ~500 | below the similarity band's design point |

If the split does go bad, the answers are invitation controls or paused
signups on the over-full side — both of which mean bringing something like a
cap back deliberately, rather than discovering the need at launch.

---

## Traps already paid for

Each of these cost real debugging time. Do not reintroduce them.

- **`sorted()` on `(distance, Candidate)` tuples crashes on an exact tie** — it
  falls through to comparing `Candidate`, which has no ordering. Always pass an
  explicit `key=`.
- **`order_by(timestamp)` with no tiebreak is the `sorted()` tie bug again,
  and the clock is coarser than you think.** `current_consent` ordered by
  `granted_at DESC` alone. A grant and the re-grant that follows a notice
  change are about a millisecond apart, which on Windows is inside one tick of
  the system clock — so the two rows landed on the *same* microsecond about
  **5% of the time**, and the database was then free to return either. When it
  returned the older one the app reported `needs_restatement` forever and
  answered "which words did this person agree to" with the wrong version,
  which is the one question the table exists to answer.

  Two fixes, because either alone leaves a hole: `grant()` now writes a
  timestamp strictly after the row it supersedes, and the ordering carries
  `id DESC` as a deterministic secondary sort. Reproduced at 16/300 before and
  0/300 after.

  **It surfaced as a flaky test, and nearly got dismissed as one.** It failed
  once in a full run, passed on its own, passed on the next two full runs. A
  test that fails 5% of the time passes CI and reaches production; the tell is
  that "flaky" and "there is an ordering here with no tiebreak" are the same
  sentence. The regression test freezes the clock so the tie happens every
  time rather than being raced for.
- **A call can outlive its definition, and nothing in the build notices.**
  `requestCard` was dropped when the inbox became Messages while the line
  calling it survived, so from then on anybody with a request waiting got a
  blank Messages page (`requestCard is not defined`) at exactly the moment the
  navbar told them to look. Vite does not check undefined names, there are no
  frontend tests, and none of the accounts used in browser checks had a request
  waiting. ESLint's `no-undef` over `src/` found it and found nothing else; it is
  not a project dependency, so run it from a scratch folder (config:
  `js.configs.recommended` with browser globals) after any rename or large
  deletion in a page.
- **A browser cannot tell a refused WebSocket from a dropped one.** The server
  refuses a socket on an unanswered request by design, but a handshake refused
  with 403 closes as 1006, the same as a network failure — so the client retried
  it six times over about 44 seconds every time somebody opened a request. The
  fix is on the client: join only a conversation that is open, and join again at
  the moment this person's own reply opens it.
- **A withdrawn vector is JSON `null` on SQLite, not SQL NULL.** `face_vector`
  is pgvector on Postgres and JSON on SQLite, and SQLAlchemy stores Python
  `None` into a JSON column as the text `null` — so `face_vector IS NOT NULL`
  is true for somebody who withdrew consent, on every local database and in the
  test suite, while production stores a real NULL. `_eligible_candidates`
  always checked the vector in Python too, which is why it worked; the new
  `still_showable` did not, and served round 2 of a withdrawn person until it
  did. Any SQL null check on an embedding column needs the Python check beside
  it.
- **A module-level cache belongs to the tab, not the account.** Sign-out and
  sign-in are both client-side navigations with no reload, so the navbar's
  cached counts carried the first person's "Messages 1" into the next person's
  bar. It now subscribes to the token and keys everything to a generation, so a
  request started for one account can neither be cached nor painted for the
  next. Any new cache of per-account data needs the same.
- **`toISOString()` is the wrong day for a date input east of Greenwich.** It
  converts to UTC, and local midnight in India is still the previous day in UTC,
  so the join form's birthdate `max` was a day early every day there — somebody
  turning 18 today could not pick their own birthday. A `<input type="date">`
  works in local dates; build its bounds from `getFullYear/getMonth/getDate`.
- **SQLite returns naive datetimes; Postgres returns aware ones.** The
  `UTCDateTime` type in `backend/database.py` normalises both directions. Every
  timestamp column must use it, or comparisons raise on one backend only.
- **Supabase's transaction pooler (pgbouncer) breaks asyncpg's prepared
  statement cache.** `settings.db_connect_args` disables it when it sees a
  pooler host. Removing that makes every query after the first fail.
- **`uvicorn --reload` does not reload on this Windows machine, and killing it
  leaves the server running.** Twice in one session the API kept serving old
  routes — the SPA's HTML came back for a new endpoint, which reads as a routing
  bug — because no change was ever detected. Stopping the `uvicorn` process is
  not enough either: the reloader serves from a child started by
  `multiprocessing.spawn`, whose command line does not mention uvicorn, and it
  keeps port 8000 bound after its parent is gone. Find it by parent PID, stop
  it, and restart without `--reload`; after a backend change, restart by hand.
- **Reading `app.routes` sees only the top level on this FastAPI.** 0.141
  keeps each included router as one `_IncludedRouter` entry instead of copying
  its routes up. `test_nothing_over_http_can_make_a_reviewer` collected paths
  from `app.routes`, so "no route mentions reviewer" had been passing with every
  real route invisible to it. `tests/conftest.all_route_paths` walks into
  `original_router`, and both staff-grant tests now assert a known route is in
  the list before asserting what is not.
- **Log lines that format a field nothing guarantees any more.** Account
  erasure logged `email_domain=email.rsplit("@")[-1]`, which would have raised
  on the first account made without an email — so deleting your account
  would have failed for every new user. Anything that becomes nullable needs a
  grep for the places that treated it as always there, logs included.
- **A mandatory scroll-snap eats its own container's padding.** `.deck` has a
  negative margin and matching padding so cards can scroll to the screen edge
  while their contents keep the page gutter — but `scroll-snap-type: x
  mandatory` aligns the first card to the *scrollport* edge, so the browser
  scrolled 16px on load and the first card sat flush against the screen while
  every other element was indented. `scroll-padding-inline` is the fix;
  measured `scrollLeft: 16` is how it was found, because it looks like a
  design choice rather than a bug.
- **The sheet's close button is positioned over its own contents.** It is
  absolute at the top-right of the panel, so anything else pushed right in the
  first row lands under it — the block/report control overlapped it by 8px in
  both axes at every width, which on a safety control means a tap meant for
  "close" could block somebody. Anything in that row needs
  `--sheet-close-clearance`.
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
- **Migrations are layered now, not squashed.** They *were* squashed while
  nothing was deployed — each phase regenerated one initial migration. That
  era ended with `b1f4a7c92d30`: there is a Supabase database in use with real
  photos in it, stamped `d3c9cf01a102`, and regenerating that revision would
  leave `alembic upgrade head` unable to find where that database is. Add
  migrations from here; never replace one.
- **Autogenerate does not render a `Vector` column correctly on its own.** The
  class is `VECTOR`, not `Vector`, so the old `render_item` branch never fired
  and the generated file used `pgvector.sqlalchemy` without importing it. Fixed
  in `alembic/env.py`, which now also re-renders the `.with_variant(sa.JSON(),
  'sqlite')` half — without it the column cannot be built on SQLite, which is
  every local `alembic upgrade head`. The `CREATE EXTENSION IF NOT EXISTS
  vector` guard is still added by hand.
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
- **A page grid with counted rows breaks the moment a child is added — twice
  now.** `.inbox` got a third child in a two-row grid; then the navbar was
  prepended to `.pairs`, whose rows were `auto 1fr auto` for header, stage and
  footer. The rows re-assigned themselves silently: the *heading* took `1fr`
  and became a slab of empty sky, and the photographs were pushed into an
  `auto` row off the bottom of the screen. Nothing errored and every element
  was individually correct. Page shells are a flex column now, with the one
  stretching region marked `flex: 1` — a later element takes its own height
  and cannot steal the stretch.
- **"Shaky" was a scrollbar.** That same broken `.pairs` layout overflowed the
  viewport for a moment on every load and every pick, and on Windows a
  scrollbar that appears and vanishes slides a centred page sideways by its
  width each time. Headless Chromium draws zero-width scrollbars, so a
  screenshot never shows it — measure `scrollHeight > innerHeight` per frame
  across a load and a decision, which is what found it. The old page toggled
  it zero times; the broken one twice per load.
- **Anything that arrives after first paint must not take up space.** The
  navbar's counts load after the bar draws, and an inline pill pushed every
  later link 4–12px right on every page. Counts are absolutely positioned in
  the corner of their link now. The same rule caught `.pairs__sub`, whose
  reserved `min-height: 1.4em` was 3px short of its real line box (1.6em), so
  the photographs dropped 3px when the subtitle arrived — on the old page too.
  Reserve with `1lh`, which is a line box by definition.
- **Nine dev servers were running at once.** `npx vite --port 5173` quietly
  takes 5174 when 5173 is busy, so every session that started one without
  checking added another, up to nine across ports 5173–5190 — all watching
  the same folder and rewriting the same `node_modules/.vite` cache. Worse, the
  browser's `localhost` resolved to an IPv6 instance started a day earlier
  while scripts hit a different IPv4 one, so what was measured was not what
  was on screen. Every edit to any JS file also forces a full reload of every
  open tab through every server. Start Vite with `--strictPort`, and list the
  listeners before starting another.
- **A worktree with a junction in it can take `node_modules` with it.** To
  serve an old commit beside the current one, the worktree needed
  `node_modules`, linked by junction. A recursive delete follows a junction
  into its target, so the link is removed on its own first (`cmd /c rmdir`,
  no `/s`) and the target checked before the worktree goes.
- **Red is the decline colour.** It is also `.btn`'s default background, so any
  new positive call-to-action arrives red unless told otherwise. The unlock and
  the composer are explicitly blue.
- **A white card on a sky-inked page must set `color: var(--ink)`.** The page
  sets white ink for the ground; a card that does not take its ink back gets
  white headings on white. It is invisible rather than wrong-looking, so it
  survives a glance — `.status__card` shipped like that until the verification
  screen was photographed. `scripts/check_contrast.py` walks every screen in
  both themes and flags anything under the WCAG threshold for its size.
- **Accent fills need `--on-accent`, not `#fff`.** The palette inverts between
  themes — dark red becomes light red — so hardcoded white heads for
  white-on-pale in dark mode. Measured: white on dark-mode `--red` is 3.4:1
  and on dark-mode `--blue` is **2.1:1**; `--on-accent` gives 5.7 and 9.1. It
  came back three more times after being written down once (a chip, the
  recorder button, the error toast), so grep for `color: #fff` before
  believing it is gone. White *is* right over a fixed dark scrim like
  `rgba(11, 20, 32, 0.82)`, which does not invert — that is the only case, and
  see the next entry for the condition attached to it.
- **A scrim is only as good as its density *where the text sits*.** The card
  caption on Keep choosing you is white over `.person__veil`, a
  `rgba(8, 14, 21, 0.82)` gradient fading to nothing — which sounds safe and
  was not. A linear fade is already down to ~0.56 alpha by the top of the
  name, giving 4.4:1 over a light photo and 3.7:1 for the age line. The fix is
  to hold the density through the band the text occupies and fade only above
  it; the veil also got *shorter*, because opacity high up the card dulls the
  face and buys no contrast. Measure this off painted pixels with a white
  photo forced underneath — a white shirt or a pale sky behind somebody's head
  is the ordinary case, and it is the case the demo portraits never show you.
- **The contrast auditor cannot see a scrim, because it is a sibling.** It
  composites *ancestor* backgrounds, and an overlay is beside something above
  the text rather than above it — so a caption over a photograph fell through
  to the card's own pale surface and was reported as white-on-pale at 1.1:1.
  It now climbs alongside the background walk looking for a positioned,
  painting sibling that covers the text, and reports those as **unjudged**
  rather than passed. Unjudged is the honest answer: what is behind the text
  is a gradient over a photograph, which no single colour can stand in for.
  Both wrong readings and right ones came out of that same blind spot, so a
  clean run does not mean the overlay cases are fine — check them by hand.
- **Dark mode was fine; light mode was not.** Sweeping every screen in both
  themes turned up nine unreadable elements and *all nine were in light* —
  white on the sky at 2.1:1, which dark mode fixes for free because the ground
  goes navy and white becomes 15:1. The instinct to check dark and assume
  light is the safe one has it exactly backwards for a palette built on a pale
  brand colour.
- **A gradient written in hex does not follow the theme.** The landing ground
  was `radial-gradient(…, #b4d8f6, var(--sky), #5b9cd4)` — one token and two
  literals. In dark mode the middle flipped to navy and the two ends stayed
  pale blue, while every piece of text on it flipped to *light* blue, so the
  wordmark and half the copy went invisible. Both ends are now `--sky-high` /
  `--sky-low`, defined in all three theme blocks. Any colour in a gradient is
  still a colour and still needs a token.
- **Check both ends of a gradient, not the midpoint.** `--on-sky-soft` clears
  5.5:1 against `--sky` itself, which is the number in the palette note — but
  the old bottom stop was darker and took the fine print down to 3.97:1. The
  stops are now chosen so the *worst* point clears 4.5:1.
- **A focus ring inherits two things from the global rule, and both can be
  wrong locally.** `:focus-visible { outline: 2px solid var(--red); outline-offset: 2px }`
  is right nearly everywhere and wrong on the photo action bar twice over: red
  on that dark fill is 1.2:1, and the tile is `overflow: hidden`, so a ring
  drawn *outside* the button is clipped away whatever colour it is. Any
  control on a dark surface, or inside a clipping box, needs its own ring.
- **An undefined custom property in a `border` shorthand deletes the border.**
  `border: 1px solid var(--field-edge)` with `--field-edge` missing is invalid
  at computed-value time, so the whole shorthand falls back to initial values
  — and `border-style`'s initial value is `none`. Not a wrong colour: no
  border at all. It happened because a token was added to two theme blocks and
  not the third, which is the ordinary way this goes wrong.
- **`"  --x: y;"` is a substring of `"    --x: y;"`.** A plain `str.replace`
  for a two-space-indented token also matches the four-space-indented copy
  inside a media query, so an assertion on `count(...) == 1` fails and, if the
  write comes after, *nothing is saved* while earlier edits in the same script
  already were. Anchor on line numbers when patching a file that repeats a
  declaration per theme block.
- **A control that sits on a photograph cannot be styled against a surface.**
  The Replace/Remove buttons on the profile photo tiles were `.btn--ghost` —
  transparent, `--ink` text, `--rule-strong` border — which is exactly right
  on a card and invisible over a dark photo, and a user's photo is whatever
  they uploaded. Neither theme helps: the photo does not invert. The fix is
  for the *container* to carry the contrast as one flat fill at a fixed alpha
  (`rgba(8, 14, 21, 0.72)`), with fixed white text on top. Measured off
  painted pixels: 8.1:1 over a near-white photo, 19.6:1 over a near-black one,
  so nothing an upload can do moves it. Flat, not a gradient — see the scrim
  entry above for why a fade is the version that goes wrong. A hairline top
  edge is also needed, or the bar vanishes into a dark photo and stops
  reading as a control even while its text is perfectly legible.
- **A flex item will not shrink below its own text, and then it is clipped.**
  Two 48px word-buttons across a 140px photo tile: `flex: 1` looks like it
  should handle it, but a flex item's `min-width` is `auto`, so both sat at
  min-content, overflowed, and `overflow: hidden` on the tile cut `Remove`
  off. It was not merely hard to see — the clipped half is not hittable, so a
  photo could not be removed at all, and the page looked fine because the part
  that survived looked deliberate. `min-width: 0` is the fix; the related grid
  case is the next entry. Worth checking any overlay control at the *smallest*
  track width, not the one the desktop happens to give it.
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
- **A "did suspension work" test passes trivially if the viewer is a
  reporter.** Reporting blocks by default, so the reported account is already
  invisible to whoever reported them — the assertion holds with the feature
  removed. Check through a bystander, and assert the subject *was* visible
  first. Same shape as any before/after test: without the before, the after
  proves nothing.
- **Adding a NOT NULL column needs a `server_default`, and dropping it needs a
  second `batch_alter_table`.** The default is what gives existing rows a
  value; leaving it in place makes `alembic check` report drift for ever,
  because the model declares only a Python-side default. But dropping it in
  the *same* batch block fails on SQLite: batch mode rebuilds the table from
  the block's final state and then copies the old rows in, so the default is
  already gone when the copy needs it. Two blocks — add in one,
  `alter_column(..., server_default=None)` in the next.
- **A migration round-trip against an empty database proves almost nothing.**
  Exactly the bug above passed `upgrade`/`downgrade`/`upgrade` and `alembic
  check` cleanly, then failed on the first database that had a user row in it.
  The test suite builds its schema with `create_all`, so it never runs
  migrations at all. Run a new migration against `dev.db`, which has rows.
- **A type-based selector quietly outranks a class.** `.landing section` is
  (0,1,1) and `.landing__later` is (0,1,0), so the section rule won the width
  no matter that it came first in the file — the card silently stayed
  1040px wide. Qualify the class (`section.landing__later`) rather than
  reordering, because reordering does not fix it. Watch for this wherever a
  layout rule is written against an element name.
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

Done: **block / report** (`backend/safety.py`), **rate limits**
(`backend/ratelimit.py`), **biometric consent + account erasure**
(`backend/privacy.py`, `backend/account.py`, `src/pages/SettingsPage.js`), the
**review queue with suspension** (`backend/moderation.py`,
`src/pages/ReviewPage.js`), **live chat** (`backend/bus.py`,
`backend/signaling.py`, `src/services/live.js`) and **automatic photo
screening** (`backend/screening.py`, `backend/ml/screen.py`) — all with the
reasoning recorded above.

**Phase 05 is complete.** What is left below is tuning and the things that
were always the next phase's.

Still open:
- **Screening is untested against real photos.** The thresholds in
  `backend/screening.py` are reasoned, not measured: nobody has run a few
  hundred real profile photos through NudeNet and looked at what got held.
  Expect the hold rate to be the first thing that needs tuning, and expect it
  to be too high rather than too low.
- **Nothing reaches somebody who is not in the app.** No email, no push: a
  request waits until the recipient next opens Overtone and sees the count in
  the bar. That is the deliberate trade (*No email, anywhere*), and it is also
  the single biggest risk to a loop that turns on the other person answering.
  Watch how long requests sit unanswered — if the median is days, this is why,
  and push with its own consent is the answer rather than putting mail back.
- **Nothing tells a reporter what happened.** Deliberate for now (see the note
  in `safety.py` about why an outcome is not disclosed), but "a person reads
  every report" is a claim the reporter currently has to take on faith.

### Phase 06 — Design system

Done: the **landing page** is art-directed around the mechanic itself (see the
reasoning above), and every surface now shares the sky, the contrast rule and
the motion conventions below.

Every screen has now been walked in both themes by
`scripts/check_contrast.py`. The three remaining failures are all the same
deliberate one — the landing display type, above.

**1.4.11 is checked too, now.** `check_contrast.py` has a second pass for
things that are not text: whether you can tell where a form field is, and
whether you can see where the keyboard is. It found two real faults — every
text field's border was 1.66:1, and the focus ring on the photo controls was
red on a dark bar at 1.2:1 *and* clipped by the tile's `overflow: hidden`.

The first version of that pass also demanded a 3:1 boundary on every button,
card and nav link and produced eighteen findings, every one of which was a
photograph or a word doing its job. **The scope is the judgement**: 1.4.11
asks whether you can tell a control is there, and a button answers that with
its own text, which 1.4.3 already governs. An empty input answers it with
nothing at all. So the check is form fields only, and the reasoning is written
into the script — an audit that cries wolf gets muted, which costs more than
the check was worth.

Still open:
- **Nobody has seen this on a real phone.** It is verified at 320, 390 and
  1440 in Chromium, which is not the same as a mid-range Android in daylight.
- **State on a card is still unchecked.** A selected chip against an
  unselected one is a 1.4.11 question the auditor cannot ask, because it
  compares an element to its background rather than to its other state.
- **Focus rings drawn with `box-shadow` are reported as unjudged.** The colour
  is readable but the geometry is not, and guessing which part of a shadow is
  the ring would be inventing a number.

  The four `outline: none` rules in `inbox.css` have been checked by hand and
  are all fine, so do not re-investigate them. `.sheet__panel:focus` is a
  dialog container focused for screen readers rather than an operable control,
  and a ring around the whole sheet is noise. The three composer textareas
  swap the ring for `border-color: var(--blue)`, and the focused state is
  6.76:1 from the unfocused one in light and 6.57:1 in dark — well past the
  3:1 that a state change needs.
- **It cannot judge anything over a photograph.** Text on a scrim is reported
  as unjudged, and staying unjudged is correct — but it means the card
  captions are held by a measurement written into a comment rather than by
  anything that runs. Re-measure if the veil is ever touched.

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

  Two of them — threshold and confidence — now have a harness.
  `scripts/calibrate.py` draws Bernoulli trials at a known rate and runs the
  real `affinity.classify` after each, so it says what a setting *costs*
  without pretending to know what real people are like. It cannot tune
  anything: the missing input is the distribution of true preference rates
  across a real population, and that needs the beta. What it does is stop the
  tuning conversation from being conducted on intuition.
- Load test, runbook, closed beta, campus unlock.

### Known gaps in what's built
- **Nothing proves anything about who somebody is.** Not enrolment, not the
  address, not that they are a different person from the account banned last
  week. The campus domain went first and the verification link went after it;
  what is left is the 18+ self-declaration and the safety surfaces. This is
  the largest known hole in the product and it is a deliberate one.
- **Queue refill is on-demand**, not a batch worker job. Fine at campus scale;
  `generate_one_pair` is the building block when it isn't.
- **Round-2 pairings only surface after 48–72h** in real use — pull `due_at`
  forward manually to demo them.
- **Tests run against SQLite only.** CI verifies migrations against Postgres,
  but business logic isn't exercised against pgvector.
- Git history contains three committed SQLite blobs (untracked since, but still
  in history). Worth a rewrite before the first push if that matters.
- **`signaling._is_participant` opens `AsyncSessionLocal` directly** rather
  than taking an injected session, because it lives inside a socket that
  outlives any request. Tests put the test database there
  (`monkeypatch.setattr(signaling, "AsyncSessionLocal", db_sessionmaker)`) —
  the `socket_room` fixture does it, and forgetting it makes every connection
  refuse for the wrong reason. The socket itself *is* driven end to end now;
  `tests/test_live.py` has the ASGI client and the reasoning.
- **`users.last_active_at` is written and never read.** Kept as the seam for
  presence in chat; see *Presence, and a column nothing reads*.
- **Old verification emails are still queued in the jobs table.** `send_email`
  jobs from before the mail stack was removed have no handler, so the worker
  retries each five times and parks it as failed — and each payload holds the
  person's address and the verification text. `dev.db` has 16; the Supabase
  database may have real ones. Erasure removes them per account (they carry
  `subject_id`), but nothing removes them otherwise. Deleting the kind outright
  is one statement and loses nothing that can still run; it is left as a
  decision, like the addresses in `users.email`.
- **`/messages` holds the thread in page state, not the URL**, so a conversation
  cannot be linked to or restored by reload. The router matches exact paths and
  has no params; add them when a second surface needs them. Profiles and
  threads open in a sheet (`src/components/sheet.js`) rather than a route for
  the same reason.
- **The thread view refetches the whole conversation on open**, even when the
  socket has been feeding it. Fine at this size; worth a `since` parameter if
  threads get long.

---

## Open questions needing a human answer

1. **How does the population stay balanced with no cap?** Nothing enforces it
   now. A segment needs ~500 for the similarity band to work and at least 8
   before any unlock is possible, so a lopsided signup rush is a silent
   failure for whoever is on the short side. Watch `manage.py stats`; decide
   in advance what you will do if it skews.
2. **Does a person know they appeared at all?** The weekly count is the safe
   version; a live "you were shown to someone" signal is more engaging and
   closer to the line drawn on rating privacy.
3. **Which STT provider survives real audio?** Sarvam is chosen and wired, but
   validated only against a synthetic tone. Record 20s each of Telugu, Hindi and
   code-mixed English from a real student and compare providers.
4. **Is seven picks the right price for an unlock?** It falls out of 0.70 at
   90% confidence rather than being chosen directly, and it still cannot be
   settled without watching real people. But `scripts/calibrate.py` now says
   what each setting costs, and two things are worth knowing before that
   conversation starts.

   At the shipped 0.70 / 90%: a coin flip clears the bar **0.9%** of the time,
   somebody you genuinely prefer 9 times in 10 unlocks **87%** of the time,
   and 8 in 10 unlocks **44%** of the time at a median of 13 comparisons.

   **Dropping the confidence level to 80% is a cliff, not a dial.** The
   fastest unlock falls from 7 straight picks to 4, and the false-unlock rate
   goes from 0.9% to **6.7%** — one in fifteen unlocks would be a viewer being
   told they have a type they do not have, and then spending their one opening
   message on it. If unlocks turn out to be too rare in the beta, move the
   threshold (0.65 buys 28% at p=0.7 for 2.4% false) rather than the
   confidence.

   The missing input is the distribution of true rates across real people. If
   most real preferences sit near 0.6, the current setting unlocks almost
   nothing; if they sit near 0.9, it works as designed. Nothing but the beta
   answers that.
5. **Should a declined request be visible to the sender at all?** Today it
   simply disappears from both inboxes — no "declined" state shown, and no way
   to tell it apart from a request still waiting. Kinder, but it does leave
   people hanging; the alternative is telling someone they were turned down on
   a campus where they will see that person again.

---

## Conventions

- **Tests are the contract.** 438 and rising; every bug found gets a regression
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
  | `connections.py` | `inbox.py` | requests, replies, declines, the inbox — and `admirers`, which the HTTP name gives no hint of |
  | `bus.py` | `signaling.py` | fan-out between processes; the chat socket |
  | `privacy.py` | `account.py` | consent, withdrawal and erasure |
  | `screening.py` (pure) | — | what to do about what the detectors found |
  | `rating.py` (pure) | — | Glicko-2 and the Wilson interval, no database |
  | `rating_service.py` | — | the only thing touching both maths and SQL |
  | `affinity.py` | — | the unlock, both directions: counts, classification, permanence |
  | `preference.py` | — | the online face model and its tilt |
  | `profile_view.py` | — | the two serializers, shared by pairs and inbox |
  | `safety.py` | `safety.py` | blocking and reporting — one file, not enough of either for two |
  | `moderation.py` | `moderation.py` | the review queue, suspension, reinstatement |

- `backend/rating.py`, `backend/preference.py`, `backend/screening.py` and
  `backend/usernames.py` stay pure — no session, no clock, no model. That is what lets the arithmetic be tested precisely and the wiring be
  tested separately.
- Never commit `.env` or `*.db`. Both are gitignored; verify staging anyway.
- **Stage explicit paths, not `git add -A`.** It swept a favicon and its
  `<link>` line into an unrelated commit twice in one session. `git status`
  before staging is the habit; naming the files is the fix.
- **The mark is drawn, not exported.** `scripts/make_mark.py` writes both
  1024px masters and `scripts/make_icons.py` cuts `public/` from them, in that
  order. The derived files are committed rather than built, so a deploy never
  needs Pillow — and they silently go stale otherwise, which happened twice.
  Replacing the artwork with a real export means dropping it over
  `overtone_favcon.png` and running **only** `make_icons.py`; `make_mark.py`
  would paint over it.
- **The tab icon is a different drawing from the big one, on purpose.** The
  mark stripes the lens where its two circles overlap, which is the best part
  of it at 1024px and the first thing to die at 16: nine bands average into
  one brown smear. The small cut drops the stripes and overlaps harder — the
  mark is a landscape shape in a square box, so tightening it is worth about
  three pixels of circle height at tab size. Check any new artwork at 16px
  magnified before believing it works; it is not a small version of itself.
