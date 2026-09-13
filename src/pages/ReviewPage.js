import { createElement } from '../utils/dom.js';
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

const ACTIONS = [
  ['dismiss', 'Dismiss', 'Nothing here. The reports close, the account is untouched.'],
  ['remove_photo', 'Remove a photo', 'One photo goes. Pick it above first.'],
  ['suspend', 'Suspend', 'They stop appearing for anyone. Reversible.'],
];

const when = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' }) : '');

export default {
  async render() {
    const page = createElement('div', { className: 'review' });

    const head = createElement('header', { className: 'review__head' });
    const headInner = createElement('div', { className: 'review__head-inner' });
    const back = createElement('button', { className: 'review__back', type: 'button' });
    back.innerHTML = '<span aria-hidden="true">&larr;</span> Pairs';
    back.addEventListener('click', () => router.go('/pairs'));
    const title = createElement('h1', { className: 'review__title' }, 'Review');
    headInner.append(back, title);
    head.append(headInner);

    const body = createElement('main', { className: 'review__body' });
    page.append(head, body);

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
            entry.reasons.map((r) => REASON_LABELS[r] || r).join(' · ')
          )
        );

        const right = createElement('div', { className: 'review__row-meta' });
        right.append(
          createElement(
            'span',
            { className: 'review__count' },
            `${entry.open_reports} open`
          ),
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
        if (photo.status === 'rejected') {
          marks.append(
            createElement('span', { className: 'review__mark' }, photo.gate_reason || 'rejected')
          );
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
      wrap.append(createElement('h3', { className: 'review__section' }, 'Reports'), reports);

      // ---- deciding ----
      const openCount = detail.reports.filter((r) => r.status === 'open').length;
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
        if (!openCount) {
          actions.append(
            createElement('p', { className: 'review__muted' }, 'Nothing open about this account.')
          );
          if (detail.user.status === 'suspended') actions.append(reinstateButton());
          return;
        }

        actions.append(
          createElement(
            'h3',
            { className: 'review__section' },
            `Close ${openCount} report${openCount === 1 ? '' : 's'}`
          ),
          createElement('label', { className: 'review__label', for: 'reviewer-note' }, 'Your note'),
          note
        );

        const row = createElement('div', { className: 'review__buttons' });
        for (const [action, label, hint] of ACTIONS) {
          const needsPhoto = action === 'remove_photo' && !selectedPhoto;
          const button = createElement(
            'button',
            {
              className: action === 'dismiss' ? 'btn btn--ghost' : 'btn',
              type: 'button',
              title: hint,
            },
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
            mediaId: action === 'remove_photo' ? selectedPhoto : null,
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

    page.mounted = () => showQueue();
    return page;
  },
};
