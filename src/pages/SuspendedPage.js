import { createElement } from '../utils/dom.js';
import router from '../services/router.js';
import store from '../services/store.js';

/* Where a suspended account lands.

   It says the thing plainly and then gets out of the way. Two temptations
   worth naming, because both are the wrong call:

   Do not list the reports. Telling somebody who reported them, or quoting
   what was said, hands them exactly what they need to go and find that
   person — which is the situation the suspension exists to end.

   Do not dead-end them. Settings stays reachable: they can still withdraw
   permission to analyse their photos, and still delete the account outright.
   Those are rights over their own data, and conduct towards other people is
   not what decides them. */

export default {
  async render() {
    const page = createElement('div', { className: 'status' });
    const card = createElement('div', { className: 'status__card' });

    card.append(
      createElement('h1', {}, 'Your account is suspended'),
      createElement(
        'p',
        { className: 'muted' },
        'Someone reported the way this account behaved towards other people, and a person on our side reviewed it and agreed. You will not appear in anyone’s pairs while this stands.'
      ),
      createElement(
        'p',
        { className: 'muted' },
        'We do not say who reported you. A suspension is a person’s decision, not an automatic one, and it can be lifted.'
      )
    );

    const settings = createElement(
      'button',
      { className: 'btn btn--full', type: 'button', style: 'margin-top:16px' },
      'Your settings'
    );
    settings.addEventListener('click', () => router.go('/settings'));

    const out = createElement(
      'button',
      { className: 'btn btn--ghost btn--full', type: 'button', style: 'margin-top:12px' },
      'Sign out'
    );
    out.addEventListener('click', () => {
      store.signOut();
      router.go('/');
    });

    card.append(settings, out);
    page.append(card);
    return page;
  },
};
