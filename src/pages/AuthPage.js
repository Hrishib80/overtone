import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import store from '../services/store.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

/* Join and sign in.

   An account is a username and a password — there is no email anywhere in
   Overtone. Two consequences shape this form:

   * The username is checked while it is typed, because it is the one field
     that can be refused for a reason nobody can see in advance: somebody else
     already has it.
   * There is no password reset, since there is nowhere to send one. The join
     form says so next to the password, which is the moment it matters, rather
     than somebody discovering it the day they forget. */

const BACK_ICON =
  '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>';

const USERNAME_RULE = '3–20 characters: letters, numbers, _ and full stops.';

function field(label, input, hint) {
  const wrap = createElement('div', { className: 'field' });
  wrap.append(createElement('label', { for: input.id }, label), input);
  if (hint) wrap.append(hint instanceof Node ? hint : createElement('p', { className: 'hint' }, hint));
  return wrap;
}

/** Latest date of birth that is still 18 — used as the picker's max. */
function eighteenYearsAgo() {
  const now = new Date();
  const d = new Date(now.getFullYear() - 18, now.getMonth(), now.getDate());
  return d.toISOString().slice(0, 10);
}

export function createAuthPage(mode) {
  const joining = mode === 'join';

  return {
    async render() {
      const page = createElement('div', { className: 'auth' });

      const bar = createElement('header', { className: 'auth__bar' });
      const back = createElement('button', {
        className: 'auth__back',
        type: 'button',
        'aria-label': 'Back',
      });
      back.innerHTML = BACK_ICON;
      back.addEventListener('click', () => router.go('/'));
      bar.append(back, createElement('h1', { className: 'auth__title' }, joining ? 'Join Overtone' : 'Sign in'));

      const body = createElement('div', { className: 'auth__body' });
      const form = createElement('form', { className: 'stack', novalidate: 'true' });

      const username = createElement('input', {
        className: 'input',
        id: 'username',
        name: 'username',
        type: 'text',
        autocomplete: 'username',
        autocapitalize: 'none',
        autocorrect: 'off',
        spellcheck: 'false',
        maxlength: joining ? '20' : '64',
        placeholder: joining ? 'e.g. ravi_k' : 'Your username',
        required: 'true',
      });

      // Polite, so a screen reader hears "taken" without being interrupted
      // mid-keystroke by every intermediate state.
      const status = createElement(
        'p',
        { className: 'hint auth__status', 'aria-live': 'polite', id: 'username-status' },
        joining ? USERNAME_RULE : ''
      );
      if (joining) username.setAttribute('aria-describedby', 'username-status');

      const password = createElement('input', {
        className: 'input',
        id: 'password',
        name: 'password',
        type: 'password',
        autocomplete: joining ? 'new-password' : 'current-password',
        placeholder: joining ? 'Choose a password' : 'Your password',
        required: 'true',
      });

      form.append(field('Username', username, joining ? status : null));

      let name;
      let birthdate;
      if (joining) {
        name = createElement('input', {
          className: 'input',
          id: 'name',
          type: 'text',
          autocomplete: 'given-name',
          placeholder: 'First name',
          maxlength: '80',
          required: 'true',
        });
        birthdate = createElement('input', {
          className: 'input',
          id: 'birthdate',
          type: 'date',
          max: eighteenYearsAgo(),
          required: 'true',
        });
        form.append(
          field('First name', name, 'This is what people see. Your username is private.'),
          field('Date of birth', birthdate, 'Overtone is 18+.')
        );
      }

      form.append(
        field(
          'Password',
          password,
          joining
            ? "At least 8 characters. There's no email on Overtone, so a forgotten password can't be reset — keep it somewhere safe."
            : null
        )
      );

      const submit = createElement(
        'button',
        { className: 'btn btn--full', type: 'submit' },
        joining ? 'Create account' : 'Sign in'
      );
      form.append(submit);

      const swap = createElement('p', { className: 'hint', style: 'text-align:center' });
      const swapLink = createElement(
        'a',
        { href: joining ? '/signin' : '/join', 'data-link': '' },
        joining ? 'Sign in instead' : 'Create an account'
      );
      swap.append(joining ? 'Already have an account? ' : 'New here? ', swapLink);
      form.append(swap);

      function setStatus(text, tone) {
        status.textContent = text;
        status.classList.toggle('auth__status--ok', tone === 'ok');
        status.classList.toggle('auth__status--bad', tone === 'bad');
        if (tone === 'bad') username.setAttribute('aria-invalid', 'true');
        else username.removeAttribute('aria-invalid');
      }

      // Usernames are stored lowercase, so show that as it is typed rather
      // than letting somebody pick "RaviK" and be surprised to sign in as
      // "ravik". The caret is put back where it was, or every capital letter
      // would throw it to the end of the field.
      username.addEventListener('input', () => {
        const lowered = username.value.toLowerCase();
        if (lowered !== username.value) {
          const at = username.selectionStart;
          username.value = lowered;
          username.setSelectionRange(at, at);
        }
        if (!joining) username.removeAttribute('aria-invalid');
      });

      if (joining) {
        let timer = null;
        let asked = 0;

        username.addEventListener('input', () => {
          clearTimeout(timer);
          const value = username.value.trim();
          if (!value) {
            setStatus(USERNAME_RULE, null);
            return;
          }
          setStatus('Checking…', null);
          timer = setTimeout(async () => {
            // Answers can come back out of order; only the latest question
            // is allowed to write to the screen.
            const mine = ++asked;
            try {
              const result = await api.checkUsername(value);
              if (mine !== asked) return;
              if (result.available) setStatus(`@${result.username} is available.`, 'ok');
              else setStatus(result.reason, 'bad');
            } catch {
              // Not being able to check is not a reason to block the form;
              // the server decides on submit either way.
              if (mine === asked) setStatus(USERNAME_RULE, null);
            }
          }, 350);
        });
      }

      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        submit.disabled = true;
        submit.textContent = joining ? 'Creating…' : 'Signing in…';

        try {
          const result = joining
            ? await api.register({
                username: username.value.trim(),
                password: password.value,
                displayName: name.value.trim(),
                birthdate: birthdate.value,
              })
            : await api.login(username.value.trim(), password.value);

          store.setState({ token: result.access_token });
          await router.refresh(await api.getMe());
        } catch (error) {
          const onUsername = error.fields?.find((f) => f.field === 'username');
          if (joining && onUsername) {
            // Said where the problem is, not only in a toast that disappears.
            setStatus(onUsername.message, 'bad');
            username.focus();
          } else {
            toast(error.message, { error: true });
          }
          for (const f of error.fields || []) {
            form.querySelector(`#${CSS.escape(f.field)}`)?.setAttribute('aria-invalid', 'true');
          }
          submit.disabled = false;
          submit.textContent = joining ? 'Create account' : 'Sign in';
        }
      });

      body.append(form);
      page.append(bar, body);
      page.mounted = () => username.focus();
      return page;
    },
  };
}

export const JoinPage = createAuthPage('join');
export const SignInPage = createAuthPage('signin');
