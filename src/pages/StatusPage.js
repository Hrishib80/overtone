import { createElement } from '../utils/dom.js';
import store from '../services/store.js';
import router from '../services/router.js';

export default {
  async render() {
    const me = store.getState().me || {};
    const waitlisted = me.status === 'waitlisted';

    const page = createElement('div', { className: 'status' });
    const card = createElement('div', { className: 'status__card' });

    const blot = createElement(
      'div',
      { className: waitlisted ? 'status__blot' : 'status__blot status__blot--red' },
      waitlisted ? '◷' : '✓'
    );
    card.append(blot);

    if (waitlisted) {
      const wl = me.waitlist || {};
      card.append(
        createElement('h2', {}, "You're on the list"),
        createElement(
          'p',
          { className: 'muted' },
          'Your segment is full at the moment. We admit people as places open up.'
        ),
        createElement('div', { className: 'status__position' }, `#${wl.position ?? '—'}`),
        createElement('p', { className: 'eyebrow' }, `in the ${wl.segment || 'queue'} queue`)
      );

      if (wl.invited) {
        card.append(
          createElement(
            'div',
            { className: 'notice notice--ok', style: 'margin-top:20px;text-align:left' },
            'A place has opened up for you. Claim it before it passes to the next person.'
          )
        );
      }
    } else {
      card.append(
        createElement('h2', {}, "You're in"),
        createElement(
          'p',
          { className: 'muted' },
          'Your profile is complete. Pairs start once enough people have joined your campus — we will let you know the moment they do.'
        )
      );
    }

    const meta = createElement('div', { className: 'status__meta' });
    meta.append(
      createElement('span', {}, me.display_name || ''),
      createElement('span', {}, me.age ? `${me.age}` : '')
    );
    card.append(meta);

    const out = createElement(
      'button',
      { className: 'btn btn--ghost btn--full', type: 'button', style: 'margin-top:20px' },
      'Sign out'
    );
    out.addEventListener('click', () => {
      store.signOut();
      router.go('/');
    });
    card.append(out);

    page.append(card);
    return page;
  },
};
