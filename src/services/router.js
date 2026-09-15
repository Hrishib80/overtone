import store from './store.js';
import api from './api.js';
import { ADMIN_PATH } from './paths.js';

/* Where an account belongs, given its status. Onboarding is a funnel: you
   cannot skip ahead, and you cannot fall back into a step you've finished. */
const HOME_FOR_STATUS = {
  /* There is no verification step any more, but rows predating its removal
     still carry the status. They belong in onboarding like everybody else —
     leaving them pointed at a route that no longer exists would strand them
     on the landing page with no way forward. */
  pending_verification: '/onboarding',
  onboarding: '/onboarding',
  waitlisted: '/waitlist',
  active: '/pairs',
  suspended: '/suspended',
  // Runs the portal and nothing else; every other path comes back here.
  staff: ADMIN_PATH,
};

const PUBLIC_ROUTES = new Set(['/', '/join', '/signin']);

/* Routes an account may visit besides its home. Onboarding stays a funnel —
   these are the places you can only get to once you are through it. */
const ALSO_ALLOWED = {
  pending_verification: new Set(['/onboarding', '/settings']),
  active: new Set(['/messages', '/type', '/chosen', '/profile', '/settings', '/review', ADMIN_PATH]),
  // Waiting for approval: nothing that involves another person, but their own
  // profile stays editable — a profile sent back is fixed from there — and so
  // do settings.
  waitlisted: new Set(['/profile', '/settings']),
  // Settings is reachable mid-onboarding too, because withdrawing consent and
  // deleting the account are things a half-finished profile must be able to
  // do — being stuck inside a funnel is not a reason to lose that.
  onboarding: new Set(['/settings']),
  // A suspension removes somebody from other people's experience. It does not
  // remove their say over their own data, so settings stays open.
  suspended: new Set(['/settings']),
};

class Router {
  constructor() {
    this.routes = {};
    this.current = null;
    this.active = null;
  }

  register(routes) {
    this.routes = routes;
  }

  start() {
    window.addEventListener('popstate', () => this.resolve(location.pathname));
    document.addEventListener('click', (event) => {
      const link = event.target.closest('[data-link]');
      if (!link) return;
      event.preventDefault();
      this.go(link.getAttribute('href'));
    });
    this.resolve(location.pathname);
  }

  go(path, { replace = false } = {}) {
    if (path === this.current) return;
    history[replace ? 'replaceState' : 'pushState'](null, '', path);
    this.resolve(path);
  }

  /** Where this visitor should be, or null if the path they asked for is fine. */
  redirectFor(path) {
    const { token, me } = store.getState();

    if (!token) return PUBLIC_ROUTES.has(path) ? null : '/';
    if (!me) return null; // still loading; app.js resolves before starting

    const home = HOME_FOR_STATUS[me.status] || '/';
    if (path === home) return null;
    // The queue is a hint here and a check on the server: `/api/moderation`
    // reads the column itself, so this only decides whether the page is worth
    // rendering, never whether the data comes back.
    if (path === '/review' && !me.is_reviewer) return home;
    // The same for the admin portal: a hint, with `/api/admin` answering 404
    // to anybody the column does not name.
    if (path === ADMIN_PATH && !me.is_admin) return home;
    return ALSO_ALLOWED[me.status]?.has(path) ? null : home;
  }

  async resolve(path) {
    // Staff access is granted from the command line, usually while the person
    // is already signed in — so the account this tab loaded may predate the
    // grant, and the portal would bounce to the pair view until a reload. Before
    // refusing a staff page, ask once more.
    const { me, token } = store.getState();
    const staffPage = (path === ADMIN_PATH && !me?.is_admin) || (path === '/review' && !me?.is_reviewer);
    if (token && me && staffPage) {
      try {
        store.setState({ me: await api.getMe() });
      } catch {
        /* keep what we had; the redirect below decides */
      }
    }

    const redirect = this.redirectFor(path);
    if (redirect) {
      history.replaceState(null, '', redirect);
      path = redirect;
    }

    const page = this.routes[path] || this.routes['/'];
    this.current = path;

    const root = document.getElementById('app');
    this.active?.destroy?.();
    root.replaceChildren();

    const view = await page.render();
    this.active = view;
    root.append(view);
    view.mounted?.();
  }

  /** Re-read the account and send the user wherever they now belong. */
  async refresh(me) {
    store.setState({ me });
    const home = HOME_FOR_STATUS[me.status] || '/';
    history.replaceState(null, '', home);
    await this.resolve(home);
  }
}

export default new Router();
