import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import router from '../services/router.js';

/* Reached from inside an email, by somebody who may not be signed in and may
   not want to be.

   It acts on arrival rather than asking first. A page that says "click here to
   confirm you want to unsubscribe" is a page that failed at the one job it
   has — the person already decided, in their mail client, and making them
   decide twice is how "unsubscribe" turns into "mark as spam".

   It answers the same way whether or not the link was valid. Saying "no such
   account" would turn this into a way of finding out which addresses are
   registered, which is exactly the kind of thing a dating app must not hand
   to somebody guessing. */

export default {
  async render() {
    const page = createElement('div', { className: 'status' });
    const card = createElement('div', { className: 'status__card' });
    page.append(card);

    const params = new URLSearchParams(location.search);
    const userId = params.get('u') || '';
    const token = params.get('t') || '';

    card.append(
      createElement('h1', {}, 'One moment'),
      createElement('p', { className: 'muted' }, 'Turning these off…')
    );

    page.mounted = async () => {
      if (userId && token) {
        try {
          await api.unsubscribe(userId, token);
        } catch {
          // Deliberately ignored. The person came here to stop hearing from
          // us; an error message they can do nothing about is worse than
          // silence, and the same wording either way is what keeps this from
          // being an oracle.
        }
      }

      card.replaceChildren(
        createElement('h1', {}, "That's done"),
        createElement(
          'p',
          { className: 'muted' },
          'You will not get any more email from Overtone about people reaching out. Your account is untouched, and you can turn it back on in settings whenever you like.'
        )
      );

      const back = createElement(
        'button',
        { className: 'btn btn--full', type: 'button', style: 'margin-top:16px' },
        'Go to Overtone'
      );
      back.addEventListener('click', () => router.go('/'));
      card.append(back);

      // The address bar keeps the signed token otherwise, and it stays in
      // browser history and in anything that syncs it.
      history.replaceState(null, '', '/unsubscribe');
    };

    return page;
  },
};
