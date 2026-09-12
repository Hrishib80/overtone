import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import store from '../services/store.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

const BACK_ICON =
  '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>';

function field(label, input, hint) {
  const wrap = createElement('div', { className: 'field' });
  const labelEl = createElement('label', { for: input.id }, label);
  wrap.append(labelEl, input);
  if (hint) wrap.append(createElement('p', { className: 'hint' }, hint));
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

      const email = createElement('input', {
        className: 'input',
        id: 'email',
        type: 'email',
        autocomplete: 'email',
        inputmode: 'email',
        placeholder: 'you@campus.edu',
        required: 'true',
      });

      const password = createElement('input', {
        className: 'input',
        id: 'password',
        type: 'password',
        autocomplete: joining ? 'new-password' : 'current-password',
        placeholder: joining ? 'At least 8 characters' : 'Your password',
        required: 'true',
      });

      form.append(field('Campus email', email, joining ? 'Only your campus address works.' : null));

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
          field('First name', name),
          field('Date of birth', birthdate, 'Overtone is 18+.')
        );
      }

      form.append(field('Password', password));

      const submit = createElement('button', {
        className: 'btn btn--full',
        type: 'submit',
      }, joining ? 'Create account' : 'Sign in');
      form.append(submit);

      const swap = createElement('p', { className: 'hint', style: 'text-align:center' });
      const swapLink = createElement('a', { href: joining ? '/signin' : '/join', 'data-link': '' },
        joining ? 'Sign in instead' : 'Create an account');
      swap.append(joining ? 'Already have an account? ' : 'New here? ', swapLink);
      form.append(swap);

      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        submit.disabled = true;
        submit.textContent = joining ? 'Creating…' : 'Signing in…';

        try {
          const result = joining
            ? await api.register({
                email: email.value.trim(),
                password: password.value,
                displayName: name.value.trim(),
                birthdate: birthdate.value,
              })
            : await api.login(email.value.trim(), password.value);

          store.setState({ token: result.access_token });

          // Dev convenience: no mail sender yet, so the API hands back the token.
          if (result.verification_token) {
            sessionStorage.setItem('overtone_dev_verification', result.verification_token);
          }

          await router.refresh(await api.getMe());
        } catch (error) {
          toast(error.message, { error: true });
          if (error.fields?.length) {
            for (const f of error.fields) {
              const el = form.querySelector(`#${CSS.escape(f.field)}`);
              el?.setAttribute('aria-invalid', 'true');
            }
          }
          submit.disabled = false;
          submit.textContent = joining ? 'Create account' : 'Sign in';
        }
      });

      body.append(form);
      page.append(bar, body);
      page.mounted = () => email.focus();
      return page;
    },
  };
}

export const JoinPage = createAuthPage('join');
export const SignInPage = createAuthPage('signin');
