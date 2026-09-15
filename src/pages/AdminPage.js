import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

/* The admin portal: who joins.

   Three tabs, in the order an admin needs them. Waiting is the queue, oldest
   first, each profile open on the page rather than behind a click — approving
   somebody should take one look and one button, not a list, a detail view and
   a way back. Sent back is the people asked to change something, so a note
   never disappears into nowhere. Joined with a code is the other door into the
   platform, kept visible because it skips this queue entirely.

   It borrows the review queue's working surface — same cards, same photo and
   answer layout — so staff see a person the same way whichever question they
   are answering. Members never see this page: the router sends a non-admin
   away, and the API answers 404 regardless. */

const ago = (iso) => {
  if (!iso) return '';
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? 'yesterday' : `${days} days ago`;
};

const PHOTO_MARKS = { held: 'held by screening', rejected: 'rejected', uploaded: 'not processed yet' };
// Only these are worth an alarm. "Not processed yet" just means the photo
// worker has not run — true of every photo locally, where it usually isn't.
const FLAGGED = new Set(['held', 'rejected']);

export default {
  async render() {
    const page = createElement('div', { className: 'review admin' });

    const head = createElement('header', { className: 'review__head' });
    const headInner = createElement('div', { className: 'review__head-inner admin__head' });
    const back = createElement('button', { className: 'review__back', type: 'button' }, '← The app');
    back.addEventListener('click', () => router.go('/pairs'));
    headInner.append(createElement('h1', { className: 'review__title' }, 'Admin'), back);

    const tabs = createElement('div', { className: 'admin__tabs', role: 'tablist', 'aria-label': 'Admin sections' });
    head.append(headInner, createElement('div', { className: 'admin__tabs-wrap' }, [tabs]));

    const body = createElement('main', { className: 'review__body' });
    page.append(head, body);

    let tab = 'waiting';
    let data = { waiting: [], sent_back: [], invited: [] };

    function tabButton(key, label, count) {
      const button = createElement('button', {
        className: `admin__tab${tab === key ? ' is-here' : ''}`,
        type: 'button',
        role: 'tab',
        'aria-selected': String(tab === key),
      });
      button.append(label);
      if (count) button.append(createElement('span', { className: 'admin__count' }, String(count)));
      button.addEventListener('click', () => {
        tab = key;
        paint();
      });
      return button;
    }

    function applicantCard(applicant, { sentBack = false } = {}) {
      const { user, photos, prompts, application } = applicant;
      const card = createElement('article', { className: 'review__subject admin__applicant' });

      const facts = [
        `@${user.username}`,
        user.age ? `${user.age}` : null,
        user.pronouns,
        user.location,
        application.applied_at ? `${sentBack ? 'sent back' : 'waiting'} since ${ago(application.applied_at)}` : null,
      ].filter(Boolean);
      card.append(
        createElement('div', { className: 'review__who' }, [
          createElement('h2', { className: 'review__name' }, user.display_name || 'Unnamed'),
          createElement('p', { className: 'review__muted' }, facts.join(' · ')),
        ])
      );
      if (application.invited_by) {
        card.append(
          createElement(
            'p',
            { className: 'review__muted' },
            `Invited by ${application.invited_by.display_name} (@${application.invited_by.username}) — their invite no longer vouches for them.`
          )
        );
      }

      if (photos.length) {
        const strip = createElement('div', { className: 'review__photos' });
        for (const photo of photos) {
          const frame = createElement('div', { className: 'review__photo admin__photo' });
          if (photo.url) frame.append(createElement('img', { src: photo.url, alt: '', loading: 'lazy' }));
          const marks = createElement('div', { className: 'review__photo-marks' });
          if (PHOTO_MARKS[photo.status]) {
            marks.append(
              createElement(
                'span',
                { className: `review__mark${FLAGGED.has(photo.status) ? ' is-flagged' : ''}` },
                PHOTO_MARKS[photo.status]
              )
            );
          }
          // Not "shown in pairs" as on the review screen: nobody on this page is
          // shown to anyone yet. It is the photo that will be.
          if (photo.is_primary) marks.append(createElement('span', { className: 'review__mark' }, 'main photo'));
          frame.append(marks);
          strip.append(frame);
        }
        card.append(createElement('h3', { className: 'review__section' }, 'Photos'), strip);
      } else {
        card.append(createElement('p', { className: 'review__muted' }, 'No photos.'));
      }

      if (prompts.length) {
        const answers = createElement('div', { className: 'review__answers' });
        for (const answer of prompts) {
          answers.append(
            createElement('div', { className: 'review__answer' }, [
              createElement('p', { className: 'review__answer-q' }, answer.text || answer.prompt_id),
              createElement(
                'p',
                { className: 'review__answer-a' },
                answer.body || (answer.kind === 'voice' ? '(voice note)' : '')
              ),
            ])
          );
        }
        card.append(createElement('h3', { className: 'review__section' }, 'Answers'), answers);
      }

      const decide = createElement('div', { className: 'review__decide' });
      if (sentBack && application.note) {
        decide.append(
          createElement('p', { className: 'review__label' }, 'You asked them to change'),
          createElement('blockquote', { className: 'review__quote' }, application.note)
        );
      }

      const buttons = createElement('div', { className: 'review__buttons' });
      const approve = createElement(
        'button',
        { className: 'btn btn--blue', type: 'button' },
        sentBack ? 'Approve anyway' : 'Approve'
      );
      approve.addEventListener('click', async () => {
        approve.disabled = true;
        try {
          await api.approveApplicant(user.id);
          toast(`${user.display_name || 'They'} can use Overtone now.`);
          await load();
        } catch (error) {
          toast(error.message, { error: true });
          approve.disabled = false;
        }
      });
      buttons.append(approve);

      if (!sentBack) {
        const sendBack = createElement('button', { className: 'btn btn--ghost', type: 'button' }, 'Send back…');
        const noteId = `note-${user.id}`;
        const form = createElement('form', { className: 'admin__sendback', hidden: 'hidden' });
        const note = createElement('textarea', {
          className: 'textarea',
          id: noteId,
          rows: '3',
          maxlength: '1000',
          placeholder: 'e.g. Please add a photo where your face is clearly visible.',
        });
        const confirm = createElement('button', { className: 'btn', type: 'submit' }, 'Send back');
        const cancel = createElement('button', { className: 'btn btn--ghost', type: 'button' }, 'Cancel');
        form.append(
          createElement('label', { className: 'review__label', for: noteId }, 'What should they change? They’ll see this exactly.'),
          note,
          createElement('div', { className: 'review__buttons' }, [confirm, cancel])
        );
        sendBack.addEventListener('click', () => {
          form.hidden = false;
          buttons.hidden = true;
          note.focus();
        });
        cancel.addEventListener('click', () => {
          form.hidden = true;
          buttons.hidden = false;
        });
        form.addEventListener('submit', async (event) => {
          event.preventDefault();
          if (!note.value.trim()) {
            note.setAttribute('aria-invalid', 'true');
            note.focus();
            return;
          }
          confirm.disabled = true;
          try {
            await api.sendBackApplicant(user.id, note.value.trim());
            toast('Sent back with your note.');
            await load();
          } catch (error) {
            toast(error.message, { error: true });
            confirm.disabled = false;
          }
        });
        buttons.append(sendBack);
        decide.append(buttons, form);
      } else {
        decide.append(buttons);
      }

      card.append(decide);
      return card;
    }

    function empty(title, text) {
      return createElement('div', { className: 'review__empty' }, [
        createElement('h2', {}, title),
        createElement('p', { className: 'review__muted' }, text),
      ]);
    }

    function invitedList() {
      if (!data.invited.length) {
        return empty('Nobody yet', 'People who join with a member’s invite code skip the waitlist. They’ll be listed here.');
      }
      const list = createElement('div', { className: 'review__list admin__invited' });
      for (const person of data.invited) {
        list.append(
          createElement('div', { className: 'admin__invited-row' }, [
            createElement('div', {}, [
              createElement('p', { className: 'admin__invited-name' }, `${person.display_name} · @${person.username}`),
              createElement(
                'p',
                { className: 'review__muted' },
                person.invited_by
                  ? `Invited by ${person.invited_by.display_name} (@${person.invited_by.username})`
                  : 'Invited by an account since deleted'
              ),
            ]),
            createElement('div', { className: 'admin__invited-meta' }, [
              createElement('span', { className: 'review__tag' }, person.status),
              createElement('span', { className: 'review__muted' }, ago(person.joined_at)),
            ]),
          ])
        );
      }
      return list;
    }

    function paint() {
      tabs.replaceChildren(
        tabButton('waiting', 'Waiting', data.waiting.length),
        tabButton('sent_back', 'Sent back', data.sent_back.length),
        tabButton('invited', 'Joined with a code', 0)
      );

      if (tab === 'waiting') {
        body.replaceChildren(
          ...(data.waiting.length
            ? data.waiting.map((a) => applicantCard(a))
            : [empty('Nobody waiting', 'New profiles land here when they’re finished. Oldest first.')])
        );
      } else if (tab === 'sent_back') {
        body.replaceChildren(
          ...(data.sent_back.length
            ? data.sent_back.map((a) => applicantCard(a, { sentBack: true }))
            : [empty('Nothing sent back', 'Profiles you ask to change wait here until the person sends them again.')])
        );
      } else {
        body.replaceChildren(invitedList());
      }
    }

    async function load() {
      try {
        const [waitlist, invited] = await Promise.all([api.getWaitlist(), api.getInvited()]);
        data = { ...waitlist, invited: invited.invited };
      } catch (error) {
        body.replaceChildren(createElement('p', { className: 'review__muted' }, error.message));
        return;
      }
      paint();
    }

    body.replaceChildren(createElement('p', { className: 'review__muted' }, 'Loading…'));
    page.mounted = () => load();
    return page;
  },
};
