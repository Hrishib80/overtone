import { createElement } from '../utils/dom.js';
import { navbar, refreshNav, reviewerLink } from '../components/navbar.js';
import { peopleDeck, shell } from '../components/people.js';
import api from '../services/api.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

/* My type — the people *you* keep choosing.

   This is a description of your own taste, assembled from your own choices,
   and it is the half of the app that has no counterpart in a swipe product:
   nobody here has been told anything. You picked them often enough that the
   guessing stopped, their profile opened, and they still know nothing about
   it until you write.

   Deliberately a separate page from "Keep choosing you". They were one page
   once and it read as a single pile of people, which hid the thing that makes
   them different — one list is what you did, the other is what was done to
   you. */

export default {
  async render() {
    const nav = navbar('/type');
    reviewerLink(nav);
    const { page, body } = shell(nav, 'My type', 'The people you keep choosing.');

    async function load() {
      body.replaceChildren(createElement('p', { className: 'people__muted' }, 'Loading…'));
      let inbox;
      try {
        inbox = await api.getInbox();
      } catch (error) {
        body.replaceChildren(createElement('p', { className: 'people__muted' }, error.message));
        return;
      }

      if (!inbox.unlocked.length) {
        body.replaceChildren(
          empty(
            'Nobody yet',
            'Keep choosing. Pick the same person often enough and their profile opens up here, and they are not told, so there is no cost to taking your time.'
          )
        );
        return;
      }

      body.replaceChildren(
        createElement(
          'p',
          { className: 'people__note' },
          'They have not been told. Writing is what tells them, and you get one message to do it with.'
        ),
        peopleDeck(inbox.unlocked, {
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

    function empty(title, note) {
      const card = createElement('div', { className: 'people__empty' });
      card.append(
        createElement('h2', {}, title),
        createElement('p', { className: 'people__muted' }, note)
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

export { toast };
