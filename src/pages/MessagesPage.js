import { createElement, formatTime } from '../utils/dom.js';
import { openSheet } from '../components/sheet.js';
import { safetyButton } from '../components/safety.js';
import { joinThread } from '../services/live.js';
import { navbar, refreshNav, reviewerLink } from '../components/navbar.js';
import api from '../services/api.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

/* Messages: requests waiting on you, live conversations, and the ones you
   sent that nobody has answered yet.

   The two *people* lists that used to live here — the ones you keep choosing,
   and the ones who keep choosing you — are their own pages now. What is left
   is the part that is actually correspondence, ordered by who is waiting on
   whom: requests first, because somebody wrote and is sitting there.

   There is no email and no push. The count in the navbar is the whole of the
   nudge, which is why it is on every screen rather than only this one.

   Original note, still true of the thread:

   Ordered by who is waiting on whom. Requests first, because somebody wrote
   and is sitting there. Then the people who opened up to you — a deck of
   photographs you can move through, not a column of full profiles to scroll
   past; a profile is a whole screen's worth of attention and gets one when you
   ask for it. Then conversations. Sent requests last and deliberately dull: a
   message nobody has answered yet is not an achievement.

   Profiles and threads both open in a sheet over the list, so the list never
   loses its place and the two heaviest things on the screen share one
   entrance. */


export default {
  async render() {
    const page = createElement('div', { className: 'inbox' });

    const nav = navbar('/messages');
    reviewerLink(nav);

    const head = createElement('header', { className: 'inbox__head' });
    const headInner = createElement('div', { className: 'inbox__head-inner' });
    headInner.append(createElement('h1', { className: 'inbox__title' }, 'Messages'));
    head.append(headInner);

    const body = createElement('main', { className: 'inbox__body' });
    page.append(nav, head, body);

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

      // Held across the sheet's lifetime so the teardown below can close it.
      let live = null;

      openSheet({
        label: `Conversation with ${thread.peer.display_name || 'someone'}`,
        onClose: () => live?.close(),
        build: ({ close }) => {
          const wrap = createElement('div', { className: 'thread' });

          const bar = createElement('header', { className: 'thread__bar' });
          bar.append(
            avatar(thread.peer, { size: 'avatar--sm' }),
            createElement('h2', { className: 'thread__name' }, thread.peer.display_name || ''),
            safetyButton({
              subject: thread.peer,
              context: entry.id,
              onDone: () => {
                close();
                refresh();
              },
            })
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
            input.addEventListener('input', () => live?.typing());
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
              log.insertBefore(optimistic, log.querySelector('.thread__typing'));
              log.scrollTop = log.scrollHeight;

              try {
                await api.sendMessage(entry.id, text);
                if (thread.status === 'requested') {
                  // Their reply just opened it; nothing more to announce, but
                  // there is a live channel to join now.
                  thread.status = 'open';
                  goLive();
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

          // Typing dots live at the end of the log so they read as the next
          // message about to arrive, which is what they mean.
          const typing = createElement('div', { className: 'thread__typing', hidden: 'hidden' });
          typing.append(
            createElement('span', { className: 'thread__dot' }),
            createElement('span', { className: 'thread__dot' }),
            createElement('span', { className: 'thread__dot' })
          );
          log.append(typing);

          /* Live updates. Everything below is an accelerator: the same state
             arrives from `getThread` on the next open, so a socket that never
             connects costs immediacy and nothing else. */
          const atBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 60;

          /* Only for a conversation that is actually open. The server refuses
             a socket on an unanswered request — it has one message and no live
             channel, by design — but a browser cannot tell that refusal from a
             network failure (both close with 1006), so the client used to
             retry it six times over about 44 seconds, every time somebody
             opened a request. Joined here when it is open already, and again
             the moment this person's own reply opens it. */
          const goLive = () => {
            if (live) return;
            live = joinThread(entry.id, {
              onMessage: (message) => {
                // Only follow the conversation down if they were already at the
                // bottom — yanking the view while somebody is reading back
                // through a thread is worse than a missed message.
                const follow = atBottom();
                const previous = typing.previousElementSibling;
                if (previous?.classList.contains('bubble-row--theirs')) {
                  previous.querySelector('.bubble__time')?.remove();
                }
                log.insertBefore(bubbleFor(message, { fresh: true }), typing);
                typing.hidden = true;
                if (follow) log.scrollTop = log.scrollHeight;

                // They wrote, so this is now read by us, and the list badge
                // behind the sheet is stale either way.
                api.markRead(entry.id).catch(() => {});
                refresh();
              },
              onRead: () => {
                for (const node of log.querySelectorAll('.bubble-row--mine')) {
                  node.classList.add('is-read');
                }
              },
              onTyping: (on) => {
                const follow = atBottom();
                typing.hidden = !on;
                if (on && follow) log.scrollTop = log.scrollHeight;
              },
            });
          };
          if (thread.status === 'open') goLive();

          wrap.append(bar, log, foot);
          requestAnimationFrame(() => {
            log.scrollTop = log.scrollHeight;
          });
          return wrap;
        },
      });
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
        createElement('h2', {}, 'No messages yet'),
        createElement(
          'p',
          { className: 'muted' },
          'Conversations start from My type or from Keep choosing you, write to somebody there and it lands here.'
        )
      );
      const go = createElement('button', { className: 'btn', type: 'button' }, 'Back to pairs');
      go.addEventListener('click', () => router.go('/pairs'));
      card.append(go);
      return card;
    }

    // ---- a request waiting on you -----------------------------------------

    /* Somebody's one opening message, and the two things you can do with it.

       This function was lost when the inbox became Messages (b31584d): the
       call to it survived and the definition did not, so from then on anyone
       with a request waiting got a blank Messages page — `requestCard is not
       defined` — at exactly the moment the navbar told them to look.

       Declining used to be labelled "Not now", which is a promise the server
       does not keep: a decline is final in both directions, and the request
       leaves both inboxes for good. So it says what it does, and asks once
       more before doing something that cannot be undone. The other person is
       never told either way. */
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
        card.append(createElement('blockquote', { className: 'request__quote' }, entry.last_message.text));
      }

      const actions = createElement('div', { className: 'request__actions' });
      const reply = createElement('button', { className: 'btn request__reply', type: 'button' }, 'Reply');
      reply.addEventListener('click', () => openThread(entry));

      const pass = createElement(
        'button',
        { className: 'btn btn--ghost request__pass', type: 'button' },
        'Decline'
      );
      let armed = false;
      pass.addEventListener('click', async () => {
        if (!armed) {
          armed = true;
          pass.textContent = 'Decline for good?';
          pass.setAttribute('aria-live', 'polite');
          return;
        }
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

      refreshNav();
      body.replaceChildren(...(groups.length ? stagger(groups) : [emptyState()]));
    }

    async function load() {
      body.replaceChildren(skeleton());
      await refresh();
    }

    page.mounted = () => {
      nav.mounted();
      load();
    };
    page.destroy = () => nav.destroy();
    return page;
  },
};
