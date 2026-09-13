import { createElement } from '../utils/dom.js';
import { navbar } from '../components/navbar.js';
import api from '../services/api.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

/* The queue, for the person who reads it.

   Designed against one failure: a reviewer who cannot see what was reported
   makes worse decisions than one who can, and the way that happens is a queue
   that shows a reason code and a timestamp and nothing else. So a subject
   opens into everything at once — the photos, the answers, the reports with
   their notes, and the conversation each report came out of.

   It is deliberately the plainest screen in the app. Nobody is being courted
   here; somebody is working.

   Reachable only by a reviewer, and the API refuses everyone else with a 404
   rather than a 403 — so this page never has to be the thing keeping anybody
   out. */

const REASON_LABELS = {
  fake: 'Not who they say they are',
  harassment: 'Harassment or abuse',
  sexual: 'Unwanted sexual content',
  underage: 'Looks under 18',
  hate: 'Hate speech',
  other: 'Something else',
};

/* action, label, hint, and whether it is one of the ones that hurts.

   Approving is the safe, common answer and must not wear the same red as
   Suspend — three identical red buttons make the reviewer read the labels
   every time, which is exactly when a tired person clicks the wrong one. */
const ACTIONS = [
  ['dismiss', 'Dismiss', 'Nothing here. The reports close, the account is untouched.', 'quiet'],
  ['approve_photo', 'Approve the photo', 'The held photo is fine. Pick it above first.', 'good'],
  ['remove_photo', 'Remove a photo', 'One photo goes. Pick it above first.', 'grave'],
  ['suspend', 'Suspend', 'They stop appearing for anyone. Reversible.', 'grave'],
];

const BUTTON_CLASS = {
  quiet: 'btn btn--ghost',
  good: 'btn btn--blue',
  grave: 'btn',
};

/* What the machine said, in the reviewer's language. The queue mixes two
   sources — people, and the screener — and they read differently. */
const HELD_REASONS = {
  possible_explicit_content: 'Possibly explicit',
  possibly_underage: 'Possibly under 18',
  screening_failed: 'Screening did not finish',
};

const when = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' }) : '');

export default {
  async render() {
    const page = createElement('div', { className: 'review' });

    const nav = navbar('/review');

    const head = createElement('header', { className: 'review__head' });
    const headInner = createElement('div', { className: 'review__head-inner' });
    const title = createElement('h1', { className: 'review__title' }, 'Review');
    headInner.append(title);
    head.append(headInner);

    const body = createElement('main', { className: 'review__body' });
    page.append(nav, head, body);

    // ---- the queue -------------------------------------------------------

    async function showQueue() {
      body.replaceChildren(createElement('p', { className: 'review__muted' }, 'Loading…'));
      let subjects = [];
      try {
        ({ subjects } = await api.getReviewQueue());
      } catch (error) {
        body.replaceChildren(createElement('p', { className: 'review__muted' }, error.message));
        return;
      }

      title.textContent = subjects.length ? `Review · ${subjects.length}` : 'Review';

      if (!subjects.length) {
        const done = createElement('div', { className: 'review__empty' });
        done.append(
          createElement('h2', {}, 'Nothing waiting'),
          createElement('p', { className: 'review__muted' }, 'Every report has been answered.')
        );
        body.replaceChildren(done);
        return;
      }

      const list = createElement('div', { className: 'review__list' });
      for (const entry of subjects) {
        const row = createElement('button', { className: 'review__row', type: 'button' });

        const left = createElement('div', { className: 'review__row-main' });
        left.append(
          createElement('span', { className: 'review__row-name' }, entry.user.display_name || 'Unnamed'),
          createElement(
            'span',
            { className: 'review__row-why' },
            [
              ...entry.reasons.map((r) => REASON_LABELS[r] || r),
              ...(entry.held_reasons || []).map((r) => HELD_REASONS[r] || r),
            ].join(' · ')
          )
        );

        const right = createElement('div', { className: 'review__row-meta' });
        // Reports and held photos are both work, but they are not the same
        // work — a reviewer chooses differently knowing which it is.
        const counts = [];
        if (entry.open_reports) counts.push(`${entry.open_reports} reported`);
        if (entry.held_photos) counts.push(`${entry.held_photos} held`);
        right.append(
          createElement('span', { className: 'review__count' }, counts.join(' · ')),
          createElement('span', { className: 'review__since' }, when(entry.oldest_open))
        );
        if (entry.user.status === 'suspended') {
          right.append(createElement('span', { className: 'review__tag' }, 'suspended'));
        }

        row.append(left, right);
        row.addEventListener('click', () => showSubject(entry.user.id));
        list.append(row);
      }
      body.replaceChildren(list);
    }

    // ---- one subject -----------------------------------------------------

    async function showSubject(userId) {
      body.replaceChildren(createElement('p', { className: 'review__muted' }, 'Loading…'));
      let detail;
      try {
        detail = await api.getReviewSubject(userId);
      } catch (error) {
        body.replaceChildren(createElement('p', { className: 'review__muted' }, error.message));
        return;
      }

      const wrap = createElement('div', { className: 'review__subject' });

      const toQueue = createElement('button', { className: 'review__back', type: 'button' });
      toQueue.innerHTML = '<span aria-hidden="true">&larr;</span> All reports';
      toQueue.addEventListener('click', showQueue);
      wrap.append(toQueue);

      const who = createElement('div', { className: 'review__who' });
      who.append(
        createElement('h2', { className: 'review__name' }, detail.user.display_name || 'Unnamed'),
        createElement(
          'p',
          { className: 'review__muted' },
          [
            detail.user.email,
            detail.user.age ? `${detail.user.age}` : null,
            `joined ${when(detail.user.joined_at)}`,
            detail.user.status,
          ]
            .filter(Boolean)
            .join(' · ')
        )
      );
      wrap.append(who);

      // Which photo, if any, a decision is about. Selecting one is what makes
      // "remove a photo" a real option rather than a question back.
      let selectedPhoto = null;
      const reportedPhotos = new Set(detail.reports.map((r) => r.about_photo).filter(Boolean));

      const photos = createElement('div', { className: 'review__photos' });
      for (const photo of detail.photos) {
        const frame = createElement('button', {
          className: 'review__photo',
          type: 'button',
          'aria-pressed': 'false',
        });
        if (photo.url) frame.append(createElement('img', { src: photo.url, alt: '', loading: 'lazy' }));

        const marks = createElement('div', { className: 'review__photo-marks' });
        if (reportedPhotos.has(photo.id)) {
          marks.append(createElement('span', { className: 'review__mark is-flagged' }, 'reported'));
        }
        if (photo.status === 'held') {
          marks.append(
            createElement(
              'span',
              { className: 'review__mark is-flagged' },
              HELD_REASONS[photo.gate_reason] || 'held'
            )
          );
        } else if (photo.status === 'rejected') {
          marks.append(
            createElement('span', { className: 'review__mark' }, photo.gate_reason || 'rejected')
          );
        }
        // The machine's working, shown rather than summarised. A verdict
        // nobody can second-guess is not one worth asking a person about.
        if (photo.screen_detail) {
          marks.append(createElement('span', { className: 'review__mark' }, photo.screen_detail));
        }
        if (photo.is_primary) {
          marks.append(createElement('span', { className: 'review__mark' }, 'shown in pairs'));
        }
        frame.append(marks);

        frame.addEventListener('click', () => {
          const already = selectedPhoto === photo.id;
          selectedPhoto = already ? null : photo.id;
          for (const node of photos.children) node.setAttribute('aria-pressed', 'false');
          if (!already) frame.setAttribute('aria-pressed', 'true');
          renderActions();
        });
        photos.append(frame);
      }
      if (detail.photos.length) {
        wrap.append(createElement('h3', { className: 'review__section' }, 'Photos'), photos);
      }

      if (detail.prompts.length) {
        const answers = createElement('div', { className: 'review__answers' });
        for (const answer of detail.prompts) {
          const flagged = detail.reports.some((r) => r.about_prompt === answer.id);
          const card = createElement('div', {
            className: `review__answer${flagged ? ' is-flagged' : ''}`,
          });
          card.append(
            createElement('p', { className: 'review__answer-q' }, answer.text || answer.prompt_id),
            createElement(
              'p',
              { className: 'review__answer-a' },
              answer.body || (answer.kind === 'voice' ? '(voice note)' : '')
            )
          );
          answers.append(card);
        }
        wrap.append(createElement('h3', { className: 'review__section' }, 'Answers'), answers);
      }

      // ---- the reports themselves ----
      const reports = createElement('div', { className: 'review__reports' });
      for (const report of detail.reports) {
        const card = createElement('div', {
          className: `review__report${report.status === 'open' ? ' is-open' : ''}`,
        });
        card.append(
          createElement(
            'p',
            { className: 'review__report-head' },
            `${REASON_LABELS[report.reason] || report.reason} · ${when(report.created_at)} · ${report.status}`
          ),
          createElement(
            'p',
            { className: 'review__muted' },
            `from ${report.reporter?.display_name || 'an account since deleted'}`
          )
        );
        if (report.note) card.append(createElement('p', { className: 'review__quote' }, report.note));
        if (report.about_photo) {
          card.append(createElement('p', { className: 'review__muted' }, 'About one photo.'));
        }
        if (report.reviewer_note) {
          card.append(
            createElement('p', { className: 'review__muted' }, `Decided: ${report.reviewer_note}`)
          );
        }

        if (report.conversation?.length) {
          const thread = createElement('div', { className: 'review__thread' });
          for (const message of report.conversation) {
            const line = createElement('p', {
              className: `review__msg${message.from_subject ? ' is-subject' : ''}`,
            });
            line.textContent = message.text || '';
            thread.append(line);
          }
          card.append(thread);
        }
        reports.append(card);
      }
      if (detail.reports.length) {
        wrap.append(createElement('h3', { className: 'review__section' }, 'Reports'), reports);
      }

      // ---- deciding ----
      const openCount = detail.reports.filter((r) => r.status === 'open').length;
      const heldCount = detail.photos.filter((p) => p.status === 'held').length;
      const actions = createElement('div', { className: 'review__decide' });

      const note = createElement('textarea', {
        className: 'textarea',
        id: 'reviewer-note',
        rows: '3',
        maxlength: '2000',
        placeholder: 'What you decided, and why',
      });

      function renderActions() {
        actions.replaceChildren();
        if (!openCount && !heldCount) {
          actions.append(
            createElement('p', { className: 'review__muted' }, 'Nothing open about this account.')
          );
          if (detail.user.status === 'suspended') actions.append(reinstateButton());
          return;
        }

        const outstanding = [];
        if (openCount) outstanding.push(`${openCount} report${openCount === 1 ? '' : 's'}`);
        if (heldCount) outstanding.push(`${heldCount} held photo${heldCount === 1 ? '' : 's'}`);

        actions.append(
          createElement('h3', { className: 'review__section' }, `Decide · ${outstanding.join(', ')}`),
          createElement('label', { className: 'review__label', for: 'reviewer-note' }, 'Your note'),
          note
        );

        const row = createElement('div', { className: 'review__buttons' });
        for (const [action, label, hint, weight] of ACTIONS) {
          // Dismiss closes reports. With none open it would close nothing and
          // leave the held photo exactly where it was, so it is not offered.
          if (action === 'dismiss' && !openCount) continue;
          if (action === 'approve_photo' && !heldCount) continue;

          const needsPhoto =
            (action === 'remove_photo' || action === 'approve_photo') && !selectedPhoto;
          const button = createElement(
            'button',
            { className: BUTTON_CLASS[weight], type: 'button', title: hint },
            label
          );
          button.disabled = needsPhoto;
          button.addEventListener('click', () => decide(action));
          row.append(button);
        }
        actions.append(row);
        if (detail.user.status === 'suspended') actions.append(reinstateButton());
      }

      function reinstateButton() {
        const button = createElement(
          'button',
          { className: 'btn btn--ghost', type: 'button' },
          'Lift the suspension'
        );
        button.addEventListener('click', async () => {
          button.disabled = true;
          try {
            await api.reinstate(userId);
            toast('Reinstated.');
          } catch (error) {
            toast(error.message, { error: true });
            button.disabled = false;
            return;
          }
          showSubject(userId);
        });
        return button;
      }

      async function decide(action) {
        if (!note.value.trim()) {
          toast('Say what you decided — a decision nobody wrote down is not one.', { error: true });
          note.focus();
          return;
        }
        try {
          await api.decideReport(userId, {
            action,
            note: note.value.trim(),
            mediaId:
              action === 'remove_photo' || action === 'approve_photo' ? selectedPhoto : null,
          });
        } catch (error) {
          toast(error.message, { error: true });
          return;
        }
        toast('Done.');
        showQueue();
      }

      renderActions();
      wrap.append(actions);
      body.replaceChildren(wrap);
      window.scrollTo({ top: 0 });
    }

    page.mounted = () => {
      nav.mounted();
      showQueue();
    };
    page.destroy = () => nav.destroy();
    return page;
  },
};
