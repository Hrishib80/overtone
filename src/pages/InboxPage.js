import { createElement, formatTime } from '../utils/dom.js';
import { profileBody } from '../components/profile.js';
import { openSheet } from '../components/sheet.js';
import api from '../services/api.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

/* Everything the pair loop produced.

   Ordered by who is waiting on whom. Requests first, because somebody wrote
   and is sitting there. Then the people who opened up to you — a deck of
   photographs you can move through, not a column of full profiles to scroll
   past; a profile is a whole screen's worth of attention and gets one when you
   ask for it. Then conversations. Sent requests last and deliberately dull: a
   message nobody has answered yet is not an achievement.

   Profiles and threads both open in a sheet over the list, so the list never
   loses its place and the two heaviest things on the screen share one
   entrance. */

const OPENERS = [
  'Say something worth answering.',
  'Ask about one specific thing up there.',
  'One message. Then it is their turn.',
];

const pick = (list) => list[Math.floor(Math.random() * list.length)];

export default {
  async render() {
    const page = createElement('div', { className: 'inbox' });

    const head = createElement('header', { className: 'inbox__head' });
    // The bar is full-bleed so the rule under it runs edge to edge, but its
    // contents share the body's column — otherwise the title floats off at the
    // far left of a wide window while everything it labels sits in the middle.
    const headInner = createElement('div', { className: 'inbox__head-inner' });
    const back = createElement('button', { className: 'inbox__back', type: 'button' });
    back.innerHTML = '<span aria-hidden="true">&larr;</span> Pairs';
    back.addEventListener('click', () => router.go('/pairs'));
    headInner.append(back, createElement('h1', { className: 'inbox__title' }, 'Your people'));
    head.append(headInner);

    const body = createElement('main', { className: 'inbox__body' });
    page.append(head, body);

    let busy = false;

    // ---- small pieces ----------------------------------------------------

    function avatar(peer, { size = '' } = {}) {
      const className = `avatar ${size}`.trim();
      if (peer.photo_url) {
        return createElement('img', { className, src: peer.photo_url, alt: '', loading: 'lazy' });
      }
      const blank = createElement('div', { className: `${className} avatar--blank` });
      blank.textContent = (peer.display_name || '?').slice(0, 1);
      return blank;
    }

    function section(title, note, { count } = {}) {
      const wrap = createElement('section', { className: 'group' });
      const heading = createElement('h2', { className: 'group__title' }, title);
      if (count) heading.append(createElement('span', { className: 'group__count' }, String(count)));
      wrap.append(heading);
      if (note) wrap.append(createElement('p', { className: 'group__note' }, note));
      return wrap;
    }

    /** Staggered entrance, so the page assembles rather than snapping in. */
    function stagger(nodes) {
      nodes.forEach((node, i) => {
        node.classList.add('rise');
        node.style.setProperty('--rise-delay', `${Math.min(i, 8) * 45}ms`);
      });
      return nodes;
    }

    // ---- the thread ------------------------------------------------------

    /* `stamped` is false for a message with another from the same person
       directly beneath it. A time under every line in a run of four is noise
       that makes a short conversation look like a log file. */
    function bubbleFor(message, { fresh = false, stamped = true } = {}) {
      const row = createElement('div', {
        className: `bubble-row ${message.mine ? 'bubble-row--mine' : 'bubble-row--theirs'}${
          fresh ? ' is-fresh' : ''
        }`,
      });
      const bubble = createElement('div', { className: 'bubble' }, message.text);
      row.append(bubble);
      if (stamped && message.sent_at) {
        row.append(createElement('time', { className: 'bubble__time' }, formatTime(message.sent_at)));
      }
      return row;
    }

    async function openThread(entry) {
      let thread;
      try {
        thread = await api.getThread(entry.id);
      } catch (error) {
        toast(error.message, { error: true });
        return;
      }
      if (entry.unread > 0) api.markRead(entry.id).catch(() => {});

      openSheet({
        label: `Conversation with ${thread.peer.display_name || 'someone'}`,
        build: () => {
          const wrap = createElement('div', { className: 'thread' });

          const bar = createElement('header', { className: 'thread__bar' });
          bar.append(
            avatar(thread.peer, { size: 'avatar--sm' }),
            createElement('h2', { className: 'thread__name' }, thread.peer.display_name || '')
          );

          const log = createElement('div', { className: 'thread__log' });
          thread.messages.forEach((message, i) => {
            const next = thread.messages[i + 1];
            const row = bubbleFor(message, { stamped: !next || next.mine !== message.mine });
            row.classList.add('rise');
            row.style.setProperty('--rise-delay', `${Math.min(i, 6) * 40}ms`);
            log.append(row);
          });

          const foot = createElement('div', { className: 'thread__foot' });

          // The one-message limit, said plainly rather than discovered by
          // being refused. The sender already wrote; it is not their turn.
          const waiting = thread.status === 'requested' && thread.messages.some((m) => m.mine);

          if (waiting) {
            foot.append(
              createElement('p', { className: 'thread__waiting' }, 'Sent. The next move is theirs.')
            );
          } else {
            const form = createElement('form', { className: 'thread__form' });
            const input = createElement('input', {
              className: 'thread__input',
              id: `thread-message-${entry.id}`,
              type: 'text',
              maxlength: '2000',
              placeholder:
                thread.status === 'requested' ? 'Reply, and the chat opens' : 'Write a message',
              autocomplete: 'off',
            });
            const send = createElement('button', {
              className: 'btn thread__send',
              type: 'submit',
              'aria-label': 'Send',
            });
            send.innerHTML = '<span aria-hidden="true">&uarr;</span>';
            form.append(input, send);

            form.addEventListener('submit', async (event) => {
              event.preventDefault();
              const text = input.value.trim();
              if (!text || busy) return;
              busy = true;
              input.value = '';

              // Optimistic: the bubble is there before the round trip, which
              // is the difference between "sent" and "waiting to send".
              const previous = log.lastElementChild;
              if (previous?.classList.contains('bubble-row--mine')) {
                previous.querySelector('.bubble__time')?.remove();
              }
              const optimistic = bubbleFor(
                { text, mine: true, sent_at: new Date().toISOString() },
                { fresh: true }
              );
              log.append(optimistic);
              log.scrollTop = log.scrollHeight;

              try {
                await api.sendMessage(entry.id, text);
                if (thread.status === 'requested') {
                  // Their reply just opened it; nothing more to announce.
                  thread.status = 'open';
                }
              } catch (error) {
                optimistic.remove();
                input.value = text;
                toast(error.message, { error: true });
              } finally {
                busy = false;
                refresh();
              }
            });

            foot.append(form);
          }

          wrap.append(bar, log, foot);
          requestAnimationFrame(() => {
            log.scrollTop = log.scrollHeight;
          });
          return wrap;
        },
      });
    }

    // ---- a revealed profile, and the one message it buys ------------------

    function openProfile(subject) {
      openSheet({
        label: subject.display_name || 'Profile',
        build: ({ close }) => {
          const wrap = createElement('div', { className: 'revealed' });
          wrap.append(...profileBody(subject));

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
            'Send one message'
          );

          form.append(
            createElement(
              'p',
              { className: 'compose__note' },
              'You get one message. They reply, and then you can talk properly.'
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
              toast('Sent.');
              await refresh();
            } catch (error) {
              toast(error.message, { error: true });
              send.disabled = false;
              send.textContent = 'Send one message';
            } finally {
              busy = false;
            }
          });

          wrap.append(form);
          return wrap;
        },
      });
    }

    // ---- groups ----------------------------------------------------------

    function requestCard(entry) {
      const card = createElement('article', { className: 'request' });

      const top = createElement('div', { className: 'request__top' });
      top.append(
        avatar(entry.peer),
        createElement('div', { className: 'request__who' }, [
          createElement('span', { className: 'request__name' }, entry.peer.display_name || 'Unnamed'),
          createElement(
            'span',
            { className: 'request__when' },
            entry.last_message?.sent_at ? formatTime(entry.last_message.sent_at) : ''
          ),
        ])
      );

      card.append(top);
      if (entry.last_message) {
        card.append(
          createElement('blockquote', { className: 'request__quote' }, entry.last_message.text)
        );
      }

      const actions = createElement('div', { className: 'request__actions' });
      const reply = createElement('button', { className: 'btn request__reply', type: 'button' }, 'Reply');
      reply.addEventListener('click', () => openThread(entry));

      const pass = createElement(
        'button',
        { className: 'btn btn--ghost request__pass', type: 'button' },
        'Not now'
      );
      pass.addEventListener('click', async () => {
        pass.disabled = true;
        try {
          await api.declineRequest(entry.id);
          card.classList.add('is-leaving');
          card.addEventListener('animationend', () => refresh(), { once: true });
        } catch (error) {
          toast(error.message, { error: true });
          pass.disabled = false;
        }
      });

      actions.append(reply, pass);
      card.append(actions);
      return card;
    }

    function deckCard(subject) {
      const card = createElement('button', { className: 'card', type: 'button' });

      const photo = subject.photos?.[0];
      if (photo) {
        card.append(createElement('img', { className: 'card__photo', src: photo, alt: '', loading: 'lazy' }));
      } else {
        card.append(createElement('div', { className: 'card__photo card__photo--blank' }));
      }

      const caption = createElement('div', { className: 'card__caption' });
      caption.append(createElement('span', { className: 'card__name' }, subject.display_name || 'Unnamed'));
      if (subject.age) caption.append(createElement('span', { className: 'card__age' }, `${subject.age}`));

      card.append(createElement('div', { className: 'card__veil' }), caption);
      card.addEventListener('click', () => openProfile(subject));
      return card;
    }

    function threadRow(entry, { muted = false } = {}) {
      const row = createElement('button', {
        className: `row${muted ? ' row--muted' : ''}`,
        type: 'button',
      });
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
      row.append(
        createElement(
          'span',
          { className: 'row__meta' },
          muted ? 'Waiting' : entry.last_message?.sent_at ? formatTime(entry.last_message.sent_at) : ''
        )
      );
      row.addEventListener('click', () => openThread(entry));
      return row;
    }

    function emptyState() {
      const card = createElement('div', { className: 'blank' });
      card.append(
        createElement('div', { className: 'blank__blot' }),
        createElement('h2', {}, 'Nothing here yet'),
        createElement(
          'p',
          { className: 'muted' },
          'Keep choosing. Pick the same person often enough and their profile opens up here.'
        )
      );
      const go = createElement('button', { className: 'btn', type: 'button' }, 'Back to pairs');
      go.addEventListener('click', () => router.go('/pairs'));
      card.append(go);
      return card;
    }

    // ---- load ------------------------------------------------------------

    function skeleton() {
      const wrap = createElement('div', { className: 'group' });
      for (let i = 0; i < 3; i += 1) {
        wrap.append(createElement('div', { className: 'row row--loading' }));
      }
      return wrap;
    }

    async function refresh() {
      let inbox;
      try {
        inbox = await api.getInbox();
      } catch (error) {
        toast(error.message, { error: true });
        body.replaceChildren(
          createElement('p', { className: 'muted' }, 'Could not load your inbox.')
        );
        return;
      }

      const groups = [];

      if (inbox.requests.length) {
        const wrap = section('Waiting on you', null, { count: inbox.requests.length });
        for (const entry of inbox.requests) wrap.append(requestCard(entry));
        groups.push(wrap);
      }

      if (inbox.unlocked.length) {
        const wrap = section(
          'Open to you',
          'You picked them enough times that we stopped guessing. They have not been told.'
        );
        const deck = createElement('div', { className: 'deck' });
        for (const subject of inbox.unlocked) deck.append(deckCard(subject));
        wrap.append(deck);
        groups.push(wrap);
      }

      if (inbox.conversations.length) {
        const wrap = section('Conversations');
        const list = createElement('div', { className: 'rows' });
        for (const entry of inbox.conversations) list.append(threadRow(entry));
        wrap.append(list);
        groups.push(wrap);
      }

      if (inbox.sent.length) {
        const wrap = section('Sent');
        const list = createElement('div', { className: 'rows' });
        for (const entry of inbox.sent) list.append(threadRow(entry, { muted: true }));
        wrap.append(list);
        groups.push(wrap);
      }

      body.replaceChildren(...(groups.length ? stagger(groups) : [emptyState()]));
    }

    async function load() {
      body.replaceChildren(skeleton());
      await refresh();
    }

    page.mounted = () => load();
    return page;
  },
};
