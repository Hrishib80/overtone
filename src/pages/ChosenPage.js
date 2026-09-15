import { createElement } from '../utils/dom.js';
import { navbar, refreshNav, reviewerLink } from '../components/navbar.js';
import { peopleDeck, shell } from '../components/people.js';
import api from '../services/api.js';
import router from '../services/router.js';

/* Keep choosing you — the people who pick *you*, often, over whoever they
   were shown against.

   The mirror of My type, and the opposite kind of fact: that page is built
   from what you did, this one from what everybody else did. Which is why they
   are two pages. Somebody scanning one pile of faces cannot tell which of
   them they chose and which of them chose them, and that difference is the
   only thing either list is actually saying.

   Writing to somebody here opens the conversation outright — they already
   declared, so there is no request to make and nothing for them to approve a
   second time. */

export default {
  async render() {
    const nav = navbar('/chosen');
    reviewerLink(nav);
    const { page, body } = shell(
      nav,
      'Keep choosing you',
      'The people who pick you, again and again, over whoever they were shown against.'
    );

    async function load() {
      body.replaceChildren(createElement('p', { className: 'people__muted' }, 'Loading…'));
      let inbox;
      try {
        inbox = await api.getInbox();
      } catch (error) {
        body.replaceChildren(createElement('p', { className: 'people__muted' }, error.message));
        return;
      }

      const reach = inbox.reach || { admirers: 0, seen_by: 0, share: null };

      if (!inbox.admirers.length) {
        body.replaceChildren(empty(reach));
        return;
      }

      body.replaceChildren(
        reachCard(reach, inbox.admirers.length),
        peopleDeck(inbox.admirers, {
          admirer: true,
          onSent: () => {
            refreshNav();
            load();
          },
          onGone: () => {
            refreshNav();
            load();
          },
        })
      );
    }

    /* The number, given the room to be read properly rather than squeezed
       into a caption. It is the only figure in the app derived from other
       people's behaviour and shown to the person it is about, so it gets said
       plainly and once. */
    function reachCard(reach, shown) {
      const card = createElement('div', { className: 'reach' });

      // The card counts everyone; the grid below it and the badge in the bar
      // count only the people still to be written to. When somebody is
      // already in a conversation those disagree, and a number that
      // disagrees with the thing under it reads as a bug rather than as a
      // distinction — so say where the difference went.
      const talking = reach.admirers - shown;
      const tail = talking > 0
        ? ` You are already talking to ${talking === reach.admirers ? 'all of them' : `${talking} of them`}.`
        : '';

      if (reach.share === null) {
        card.append(
          createElement('p', { className: 'reach__big' }, String(reach.admirers)),
          createElement(
            'p',
            { className: 'reach__note' },
            `${reach.admirers === 1 ? 'person keeps' : 'people keep'} choosing you. Too few people have compared you to put a percentage on it yet.${tail}`
          )
        );
      } else {
        card.append(
          createElement('p', { className: 'reach__big' }, `${reach.share}%`),
          createElement(
            'p',
            { className: 'reach__note' },
            `of the ${reach.seen_by} people who have compared you keep choosing you. Nobody else can see this.${tail}`
          )
        );
      }
      return card;
    }

    function empty(reach) {
      const card = createElement('div', { className: 'people__empty' });
      card.append(
        createElement('h2', {}, reach.admirers ? 'Nobody new' : 'Nobody yet'),
        createElement(
          'p',
          { className: 'people__muted' },
          reach.admirers
            ? 'Everyone who keeps choosing you is already in Messages.'
            : 'This fills up on its own. Somebody has to be shown you several times and pick you each time before they appear here, you cannot make it happen faster.'
        )
      );
      const go = createElement('button', { className: 'btn', type: 'button' }, 'Go to pairs');
      go.addEventListener('click', () => router.go('/pairs'));
      card.append(go);
      return card;
    }

    page.mounted = () => {
      nav.mounted();
      load();
    };
    page.destroy = () => nav.destroy();
    return page;
  },
};
