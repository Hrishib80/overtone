import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import store from '../services/store.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

export default {
  async render() {
    const me = store.getState().me;
    const page = createElement('div', { className: 'status' });
    const card = createElement('div', { className: 'status__card' });

    const blot = createElement('div', { className: 'status__blot' }, '✉');
    const heading = createElement('h2', {}, 'Check your campus inbox');
    const copy = createElement(
      'p',
      { className: 'muted' },
      `We sent a verification link to ${me?.email || 'your campus address'}. Open it to carry on.`
    );

    card.append(blot, heading, copy);

    /* Development only: the API returns the token because no mail sender is
       wired yet. This block disappears the moment one is. */
    const devToken = sessionStorage.getItem('overtone_dev_verification');
    if (devToken) {
      const notice = createElement('div', {
        className: 'notice notice--warn',
        style: 'margin-top:24px;text-align:left',
      });
      notice.append(
        createElement('strong', {}, 'Development mode'),
        createElement('p', { style: 'margin-top:4px' }, 'No mail is sent yet, so you can verify directly.')
      );

      const go = createElement('button', {
        className: 'btn btn--full',
        type: 'button',
        style: 'margin-top:16px',
      }, 'Verify my email');

      go.addEventListener('click', async () => {
        go.disabled = true;
        go.textContent = 'Verifying…';
        try {
          await api.verifyEmail(devToken);
          sessionStorage.removeItem('overtone_dev_verification');
          await router.refresh(await api.getMe());
        } catch (error) {
          toast(error.message, { error: true });
          go.disabled = false;
          go.textContent = 'Verify my email';
        }
      });

      card.append(notice, go);
    }

    const out = createElement('button', {
      className: 'btn btn--ghost btn--full',
      type: 'button',
      style: 'margin-top:12px',
    }, 'Sign out');
    out.addEventListener('click', () => {
      store.signOut();
      router.go('/');
    });
    card.append(out);

    page.append(card);
    return page;
  },
};
