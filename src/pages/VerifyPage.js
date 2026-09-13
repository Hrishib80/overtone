import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import store from '../services/store.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

/* The other end of the verification email.

   This page is reached two ways and has to work both times:

   1. Straight after registering, in the tab they signed up in. There is a
      session, and the job is to say "go and check your mail".
   2. By opening the link itself, which very often happens on a different
      device from the one they registered on — the laptop signs up, the phone
      reads the email. There is no session there at all, so the token in the
      URL is the only credential, and this page must render without one. */

function tokenFromUrl() {
  return new URLSearchParams(location.search).get('token');
}

export default {
  async render() {
    const me = store.getState().me;
    const token = tokenFromUrl();

    const page = createElement('div', { className: 'status' });
    const card = createElement('div', { className: 'status__card' });
    page.append(card);

    function show(blot, title, body, ...actions) {
      card.replaceChildren(
        createElement('div', { className: 'status__blot' }, blot),
        createElement('h2', {}, title),
        createElement('p', { className: 'muted' }, body),
        ...actions
      );
    }

    function signOutButton(label = 'Sign out') {
      const out = createElement(
        'button',
        { className: 'btn btn--ghost btn--full', type: 'button', style: 'margin-top:12px' },
        label
      );
      out.addEventListener('click', () => {
        store.signOut();
        router.go('/');
      });
      return out;
    }

    /* ---- came in on the link ---- */
    async function consumeToken() {
      show('✉', 'Confirming…', 'One moment.');

      try {
        await api.verifyEmail(token);
      } catch (error) {
        const retry = createElement(
          'button',
          { className: 'btn btn--full', type: 'button', style: 'margin-top:16px' },
          'Send a new link'
        );
        retry.addEventListener('click', () => router.go('/verify'));
        show(
          '✕',
          'That link has expired',
          error.message || 'Links work once and last 24 hours.',
          retry
        );
        return;
      }

      // Clear the token out of the address bar: it is a credential, and it
      // would otherwise sit in history and in anything that reads a referrer.
      history.replaceState(null, '', '/verify');

      if (store.getState().token) {
        // Same device they registered on — carry straight on into onboarding.
        await router.refresh(await api.getMe());
        return;
      }

      const go = createElement(
        'button',
        { className: 'btn btn--full', type: 'button', style: 'margin-top:16px' },
        'Sign in'
      );
      go.addEventListener('click', () => router.go('/signin'));
      show(
        '✓',
        "That's confirmed",
        'Your address is verified. Sign in to finish setting up your profile.',
        go
      );
    }

    /* ---- waiting for the link ---- */
    function waiting() {
      const address = me?.email || 'your address';

      const resend = createElement(
        'button',
        { className: 'btn btn--full', type: 'button', style: 'margin-top:16px' },
        'Send it again'
      );
      resend.addEventListener('click', async () => {
        if (!me?.email) {
          toast('Sign in first, and we can send it again.', { error: true });
          return;
        }
        resend.disabled = true;
        resend.textContent = 'Sending…';
        try {
          const body = await api.resendVerification(me.email);
          // Console-mailer mode: nothing was delivered, so the only way on is
          // the token in the response. Disappears the moment mail is real.
          if (body.verification_token) {
            sessionStorage.setItem('overtone_dev_verification', body.verification_token);
            card.append(devShortcut(body.verification_token));
          }
          toast('Sent. Give it a minute.');
        } catch (error) {
          toast(error.message, { error: true });
        } finally {
          resend.disabled = false;
          resend.textContent = 'Send it again';
        }
      });

      const devToken = sessionStorage.getItem('overtone_dev_verification');

      show(
        '✉',
        'Check your inbox',
        `We sent a link to ${address}. Open it to carry on — it works once and lasts 24 hours.`,
        resend,
        ...(devToken ? [devShortcut(devToken)] : []),
        signOutButton()
      );
    }

    /* Development only. Present exactly when the server told us nothing was
       actually delivered, so it disappears on its own once mail is wired. */
    function devShortcut(devToken) {
      const notice = createElement('div', {
        className: 'notice notice--warn',
        style: 'margin-top:24px;text-align:left',
      });
      notice.append(
        createElement('strong', {}, 'Development mode'),
        createElement(
          'p',
          { style: 'margin-top:4px' },
          'No mail provider is configured, so nothing was sent. You can confirm directly.'
        )
      );

      const go = createElement(
        'button',
        { className: 'btn btn--full', type: 'button', style: 'margin-top:16px' },
        'Confirm my email'
      );
      go.addEventListener('click', async () => {
        go.disabled = true;
        go.textContent = 'Confirming…';
        try {
          await api.verifyEmail(devToken);
          sessionStorage.removeItem('overtone_dev_verification');
          await router.refresh(await api.getMe());
        } catch (error) {
          toast(error.message, { error: true });
          go.disabled = false;
          go.textContent = 'Confirm my email';
        }
      });

      const wrap = createElement('div', {});
      wrap.append(notice, go);
      return wrap;
    }

    page.mounted = () => {
      if (token) consumeToken();
      else waiting();
    };
    return page;
  },
};
