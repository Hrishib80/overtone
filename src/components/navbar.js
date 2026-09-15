import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import router from '../services/router.js';
import store from '../services/store.js';
import { ADMIN_PATH } from '../services/paths.js';

/* One bar across every signed-in screen.

   Before this the app was a set of rooms with one door each: the pair view
   had a single footer link, and everything else was reachable only by going
   back through it. Five destinations that each mean something different, and
   no way to see that four of them existed.

   **My type and Keep choosing you are not the same list and must not look
   like one.** My type is who *you* keep picking — it is a description of your
   taste, assembled from your own choices. Keep choosing you is who picks
   *you*, often, over whoever they were shown against. One is an output of
   your behaviour, the other of everybody else's, and collapsing them into a
   single "your people" page was the mistake this replaces.

   The counts are the point of the bar, not decoration: a number next to
   Messages is the whole of the "you have a new message" nudge, which is why
   there is no push notification and no email. It has to be visible from
   wherever you are, or it is not a nudge.
*/

const ITEMS = [
  { path: '/pairs', label: 'Pairs', hint: 'Choose between two people' },
  { path: '/type', label: 'My type', hint: 'The people you keep choosing' },
  { path: '/chosen', label: 'Keep choosing you', hint: 'The people who keep choosing you' },
  { path: '/messages', label: 'Messages', hint: 'Requests and conversations' },
  { path: '/profile', label: 'Profile', hint: 'Edit how you appear' },
  { path: '/settings', label: 'Settings', hint: 'Blocks, consent, your account' },
];

/* Shared across every screen that mounts a bar, so switching pages does not
   re-ask the server for counts it fetched a second ago. Invalidated by
   `refreshNav()` whenever something changes them. */
let cached = null;
let inFlight = null;
const mounted = new Set();

/* Bumped whenever the counts stop being trustworthy: something changed them,
   or the account did. A request started under an older generation may finish,
   but it may not be cached and its answer may not be painted.

   Without this the counts belonged to the tab rather than to the account.
   Sign out and sign in as somebody else — no page reload happens either way —
   and the second person's bar showed the first person's "Messages 1" until
   something happened to refresh it. On a shared laptop that tells the next
   person the last one has a message waiting. */
let generation = 0;

async function counts() {
  if (!store.getState().token) return {};
  if (cached) return cached;
  if (!inFlight) {
    const asked = generation;
    const request = (async () => {
      try {
        // Three integers from a dedicated endpoint, not a whole inbox. The
        // grouping rules live on the server for both, and a test pins the
        // cheap answer to the expensive one so they cannot drift.
        const n = await api.getCounts();
        const fresh = { '/type': n.type, '/chosen': n.chosen, '/messages': n.messages };
        if (asked === generation) cached = fresh;
        return fresh;
      } catch {
        // A bar that throws would take the page down with it. No counts is a
        // worse bar, not a broken screen — but a failure is not cached, or one
        // dropped request would blank the bar for the rest of the session.
        return {};
      } finally {
        if (inFlight === request) inFlight = null;
      }
    })();
    inFlight = request;
  }
  return inFlight;
}

/** Something changed the counts — drop them and repaint every live bar. */
export function refreshNav() {
  generation += 1;
  cached = null;
  inFlight = null;
  for (const paint of mounted) paint();
}

// A different account, or none: nothing the bar knew is true any more.
store.subscribe('token', () => refreshNav());

export function navbar(current) {
  const nav = createElement('nav', { className: 'nav', 'aria-label': 'Main' });
  const inner = createElement('div', { className: 'nav__inner' });

  const mark = createElement('button', { className: 'nav__mark', type: 'button' });
  mark.innerHTML = 'Over<b>tone</b>';
  mark.addEventListener('click', () => router.go('/pairs'));

  const list = createElement('div', { className: 'nav__links' });
  const badges = new Map();

  for (const item of ITEMS) {
    const link = createElement('button', {
      className: `nav__link${item.path === current ? ' is-here' : ''}`,
      type: 'button',
      title: item.hint,
      ...(item.path === current ? { 'aria-current': 'page' } : {}),
    });
    link.append(createElement('span', { className: 'nav__label' }, item.label));

    const badge = createElement('span', { className: 'nav__count', hidden: 'hidden' });
    link.append(badge);
    badges.set(item.path, badge);

    link.addEventListener('click', () => router.go(item.path));
    list.append(link);
  }

  const out = createElement('button', { className: 'nav__out', type: 'button' }, 'Sign out');
  out.addEventListener('click', () => {
    store.signOut();
    router.go('/');
  });

  inner.append(mark, list, out);
  nav.append(inner);

  async function paint() {
    const asked = generation;
    const n = await counts();
    // Superseded while waiting — a newer paint is already on its way.
    if (asked !== generation) return;
    for (const [path, badge] of badges) {
      const value = n[path] || 0;
      badge.textContent = value > 99 ? '99+' : String(value);
      badge.hidden = value === 0;
      // The label already says what it is; the count needs saying out loud
      // for anyone who cannot see the pill.
      const item = ITEMS.find((i) => i.path === path);
      badge.setAttribute('aria-label', value ? `${value} in ${item.label}` : '');
    }
  }

  nav.mounted = () => {
    mounted.add(paint);
    paint();
  };
  nav.destroy = () => mounted.delete(paint);

  return nav;
}

/** The review queue is not in the bar — it is staff-only and would be a
    permanent empty tab for everybody else. It goes beside Sign out instead. */
export function reviewerLink(nav) {
  const me = store.getState().me;
  const inner = nav.querySelector('.nav__inner');
  const out = nav.querySelector('.nav__out');
  if (me?.is_reviewer) {
    const link = createElement('button', { className: 'nav__out', type: 'button' }, 'Review');
    link.addEventListener('click', () => router.go('/review'));
    inner.insertBefore(link, out);
  }
  // The admin portal sits here for the same reason the queue does: a tab in
  // the bar would be an empty door for every member who is not staff.
  if (me?.is_admin) {
    const link = createElement('button', { className: 'nav__out', type: 'button' }, 'Admin');
    link.addEventListener('click', () => router.go(ADMIN_PATH));
    inner.insertBefore(link, out);
  }
}
