import store from './store.js';

/* Where an account belongs, given its status. Onboarding is a funnel: you
   cannot skip ahead, and you cannot fall back into a step you've finished. */
const HOME_FOR_STATUS = {
  pending_verification: '/verify',
  onboarding: '/onboarding',
  waitlisted: '/status',
  active: '/pairs',
};

const PUBLIC_ROUTES = new Set(['/', '/join', '/signin']);

/* Routes an account may visit besides its home. Onboarding stays a funnel —
   these are the places you can only get to once you are through it. */
const ALSO_ALLOWED = {
  active: new Set(['/inbox']),
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
    return ALSO_ALLOWED[me.status]?.has(path) ? null : home;
  }

  async resolve(path) {
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
