import { createElement } from '../utils/dom.js';
import { openSheet } from './sheet.js';
import { profileBody } from './profile.js';
import { reportPhoto, safetyButton } from './safety.js';
import api from '../services/api.js';
import { toast } from '../utils/toast.js';

/* The parts "My type" and "Keep choosing you" have in common.

   Both are a page of whole people you can open and write to. What differs is
   only what writing *means*: from My type it is the one opening message an
   unlock buys, and the other person has heard nothing until it lands. From
   Keep choosing you they have already chosen you, so writing opens the
   conversation outright — there is nothing left for them to agree to.

   That difference is the entire reason the two pages exist separately, so it
   is a parameter here rather than a guess made from context. */

const OPENERS = [
  'Say something worth answering.',
  'Ask about one specific thing up there.',
  'One message. Then it is their turn.',
];

const pick = (list) => list[Math.floor(Math.random() * list.length)];

/** The page frame every signed-in list screen shares. */
export function shell(nav, title, lede) {
  const page = createElement('div', { className: 'people' });
  const head = createElement('header', { className: 'people__head' });
  head.append(
    createElement('h1', { className: 'people__title' }, title),
    createElement('p', { className: 'people__lede' }, lede)
  );
  const body = createElement('main', { className: 'people__body' });
  page.append(nav, head, body);
  return { page, head, body };
}

export function personCard(subject, { admirer = false, onSent, onGone } = {}) {
  const card = createElement('button', {
    className: 'person',
    type: 'button',
    'aria-label': `Open ${subject.display_name || 'this profile'}`,
  });

  const photo = subject.photos?.[0];
  if (photo) {
    card.append(
      createElement('img', { className: 'person__photo', src: photo.url, alt: '', loading: 'lazy' })
    );
  } else {
    card.append(createElement('div', { className: 'person__photo person__photo--blank' }));
  }

  const caption = createElement('div', { className: 'person__caption' });
  caption.append(createElement('span', { className: 'person__name' }, subject.display_name || 'Unnamed'));
  if (subject.age) caption.append(createElement('span', { className: 'person__age' }, `${subject.age}`));

  card.append(createElement('div', { className: 'person__veil' }), caption);
  card.addEventListener('click', () => openPerson(subject, { admirer, onSent, onGone }));
  return card;
}

export function peopleDeck(subjects, options = {}) {
  const grid = createElement('div', { className: 'people__grid' });
  subjects.forEach((subject, i) => {
    const card = personCard(subject, options);
    card.classList.add('rise');
    card.style.setProperty('--rise-delay', `${Math.min(i, 8) * 45}ms`);
    grid.append(card);
  });
  return grid;
}

export function openPerson(subject, { admirer = false, onSent, onGone } = {}) {
  let busy = false;

  openSheet({
    label: subject.display_name || 'Profile',
    build: ({ close }) => {
      const wrap = createElement('div', { className: 'revealed' });

      const bar = createElement('div', { className: 'revealed__bar' });
      bar.append(
        safetyButton({
          subject,
          onDone: () => {
            close();
            onGone?.();
          },
        })
      );
      wrap.append(
        bar,
        ...profileBody(subject, { onReportPhoto: (photo) => reportPhoto({ subject, photo }) })
      );

      const form = createElement('form', { className: 'compose' });
      const input = createElement('textarea', {
        className: 'compose__input',
        id: `opener-${subject.id}`,
        rows: '3',
        maxlength: '2000',
        placeholder: pick(OPENERS),
      });
      const send = createElement(
        'button',
        { className: 'btn btn--full compose__send', type: 'submit' },
        admirer ? 'Start talking' : 'Send one message'
      );

      form.append(
        createElement(
          'p',
          { className: 'compose__note' },
          admirer
            ? 'They already chose you, so this opens the conversation straight away.'
            : 'You get one message. They reply, and then you can talk properly.'
        ),
        input,
        send
      );

      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const text = input.value.trim();
        if (!text || busy) return;
        busy = true;
        send.disabled = true;
        send.textContent = 'Sending…';
        try {
          await api.sendRequest(subject.id, text);
          close();
          toast(admirer ? 'The conversation is open.' : 'Sent.');
          onSent?.();
        } catch (error) {
          toast(error.message, { error: true });
          send.disabled = false;
          send.textContent = admirer ? 'Start talking' : 'Send one message';
          busy = false;
        }
      });

      wrap.append(form);
      return wrap;
    },
  });
}
