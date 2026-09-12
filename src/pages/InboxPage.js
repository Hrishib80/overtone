import { createElement, formatTime } from '../utils/dom.js';
import { profileBody } from '../components/profile.js';
import api from '../services/api.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

/* Everything that came out of the pair loop.

   Four groups, in the order they matter to the person reading. Requests are
   first because someone is waiting on an answer. Then conversations. Then the
   people this viewer unlocked and hasn't written to — the group with no
   equivalent in a swipe app, because nobody has been notified of anything: the
   viewer decided, and the other person still knows nothing about it.

   Sent requests come last and are deliberately dull. A pending request is not
   an achievement and should not look like one. */

const OPENERS = [
  'Say something worth answering.',
  'Ask about one specific thing on their profile.',
  'No pressure — one message, then it is their turn.',
];

export default {
  async render() {
    const page = createElement('div', { className: 'inbox' });

    const head = createElement('header', { className: 'inbox__head' });
    const back = createElement('button', { className: 'inbox__back', type: 'button' }, 'Back to pairs');
    back.addEventListener('click', () => router.go('/pairs'));
    head.append(back, createElement('h1', { className: 'inbox__title' }, 'Your people'));

    const body = createElement('main', { className: 'inbox__body' });
    page.append(head, body);

    let busy = false;

    function section(title, note) {
      const wrap = createElement('section', { className: 'inbox__section' });
      wrap.append(createElement('h2', { className: 'inbox__section-title' }, title));
      if (note) wrap.append(createElement('p', { className: 'inbox__section-note' }, note));
      return wrap;
    }

    function avatar(peer) {
      if (peer.photo_url) {
        return createElement('img', { className: 'row__avatar', src: peer.photo_url, alt: '' });
      }
      const blank = createElement('div', { className: 'row__avatar row__avatar--blank' });
      blank.textContent = (peer.display_name || '?').slice(0, 1);
      return blank;
    }

    function threadRow(entry, { onOpen }) {
      const row = createElement('button', { className: 'row', type: 'button' });
      const text = createElement('div', { className: 'row__text' });

      const name = createElement('div', { className: 'row__name' }, entry.peer.display_name || 'Unnamed');
      if (entry.unread > 0) name.append(createElement('span', { className: 'row__dot' }));

      text.append(name);
      if (entry.last_message) {
        const preview = entry.last_message.mine
          ? `You: ${entry.last_message.text}`
          : entry.last_message.text;
        text.append(createElement('div', { className: 'row__preview' }, preview));
      }

      row.append(avatar(entry.peer), text);
      if (entry.last_message?.sent_at) {
        row.append(createElement('time', { className: 'row__time' }, formatTime(entry.last_message.sent_at)));
      }
      row.addEventListener('click', () => onOpen(entry));
      return row;
    }

    /* ---- the thread ---- */
    async function openThread(entry) {
      const thread = await api.getThread(entry.id);
      if (entry.unread > 0) await api.markRead(entry.id).catch(() => {});

      const view = createElement('div', { className: 'thread' });

      const bar = createElement('header', { className: 'thread__bar' });
      const leave = createElement('button', { className: 'inbox__back', type: 'button' }, 'All conversations');
      leave.addEventListener('click', () => load());
      bar.append(leave, createElement('h2', { className: 'thread__name' }, thread.peer.display_name || ''));

      const log = createElement('div', { className: 'thread__log' });
      for (const message of thread.messages) {
        const bubble = createElement(
          'div',
          { className: `bubble ${message.mine ? 'bubble--mine' : 'bubble--theirs'}` },
          message.text
        );
        log.append(bubble);
      }

      const form = createElement('form', { className: 'thread__form' });
      const input = createElement('input', {
        className: 'thread__input',
        id: 'thread-message',
        type: 'text',
        maxlength: '2000',
        placeholder: 'Write a message',
        autocomplete: 'off',
      });
      const send = createElement('button', { className: 'btn', type: 'submit' }, 'Send');
      form.append(input, send);

      // The one-message limit, said plainly rather than discovered by being
      // refused: the sender already wrote, and it is not their turn.
      const waiting = thread.status === 'requested' && thread.messages.some((m) => m.mine);
      if (waiting) {
        form.replaceChildren(
          createElement('p', { className: 'thread__waiting' }, 'Sent. The next move is theirs.')
        );
      }

      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const text = input.value.trim();
        if (!text || busy) return;
        busy = true;
        try {
          await api.sendMessage(entry.id, text);
          input.value = '';
          await openThread({ ...entry, unread: 0 });
        } catch (error) {
          toast(error.message, { error: true });
        } finally {
          busy = false;
        }
      });

      view.append(bar, log, form);
      body.replaceChildren(view);
      log.scrollTop = log.scrollHeight;
    }

    /* ---- writing the one opening message ---- */
    function composer(subject) {
      const card = createElement('article', { className: 'reveal reveal--blue unlocked' });
      card.append(...profileBody(subject));

      const form = createElement('form', { className: 'unlocked__form' });
      const input = createElement('textarea', {
        className: 'unlocked__input',
        id: `opener-${subject.id}`,
        rows: '3',
        maxlength: '2000',
        placeholder: OPENERS[Math.floor(Math.random() * OPENERS.length)],
      });
      const send = createElement('button', { className: 'btn btn--full', type: 'submit' }, 'Send one message');

      form.append(
        createElement(
          'p',
          { className: 'unlocked__note' },
          'You get one message. They can reply, and then you can talk properly.'
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
        try {
          await api.sendRequest(subject.id, text);
          toast('Sent.');
          await load();
        } catch (error) {
          toast(error.message, { error: true });
          send.disabled = false;
        } finally {
          busy = false;
        }
      });

      card.append(form);
      return card;
    }

    /* ---- the list ---- */
    async function load() {
      body.replaceChildren(createElement('p', { className: 'muted' }, 'Loading…'));

      let inbox;
      try {
        inbox = await api.getInbox();
      } catch (error) {
        toast(error.message, { error: true });
        body.replaceChildren(createElement('p', { className: 'muted' }, 'Could not load your inbox.'));
        return;
      }

      const groups = [];

      if (inbox.requests.length) {
        const wrap = section('Waiting on you', 'Someone wrote. Reply, or let it go.');
        for (const entry of inbox.requests) {
          const row = threadRow(entry, { onOpen: openThread });
          const pass = createElement('button', { className: 'row__decline', type: 'button' }, 'No thanks');
          pass.addEventListener('click', async () => {
            try {
              await api.declineRequest(entry.id);
              await load();
            } catch (error) {
              toast(error.message, { error: true });
            }
          });
          wrap.append(createElement('div', { className: 'row-pair' }, [row, pass]));
        }
        groups.push(wrap);
      }

      if (inbox.conversations.length) {
        const wrap = section('Conversations');
        for (const entry of inbox.conversations) {
          wrap.append(threadRow(entry, { onOpen: openThread }));
        }
        groups.push(wrap);
      }

      if (inbox.unlocked.length) {
        const wrap = section(
          'Open to you',
          'You picked them enough times that we stopped guessing. They have not been told.'
        );
        for (const subject of inbox.unlocked) wrap.append(composer(subject));
        groups.push(wrap);
      }

      if (inbox.sent.length) {
        const wrap = section('Sent');
        for (const entry of inbox.sent) wrap.append(threadRow(entry, { onOpen: openThread }));
        groups.push(wrap);
      }

      if (!groups.length) {
        const empty = createElement('div', { className: 'pairs__empty' });
        empty.append(
          createElement('div', { className: 'pairs__empty-blot' }),
          createElement('h2', {}, 'Nothing here yet'),
          createElement(
            'p',
            { className: 'muted' },
            'Keep choosing. When you pick the same person often enough, their profile opens up here.'
          )
        );
        groups.push(empty);
      }

      body.replaceChildren(...groups);
    }

    page.mounted = () => load();
    return page;
  },
};
