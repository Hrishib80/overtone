import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import router from '../services/router.js';
import store from '../services/store.js';
import { toast } from '../utils/toast.js';

/* Where a finished profile waits for approval.

   Two states, told apart by whether an admin left a note:

   * Waiting — nothing to do. It says a person reads every profile, and that
     there is no email: the way to find out is to come back. "Check again" does
     exactly that rather than leaving somebody to reload a page and wonder.
   * Sent back — the note, word for word, a way to edit the profile, and a way
     to send it again. Resubmitting is a deliberate step rather than something
     every edit triggers, so a person can fix three things and send once.

   Settings stays reachable for the same reason it does everywhere: deleting
   the account and withdrawing consent are not things a wait takes away. */

export default {
  async render() {
    const page = createElement('div', { className: 'status' });
    const card = createElement('div', { className: 'status__card waitlist' });
    page.append(card);

    function paint() {
      const me = store.getState().me || {};
      const note = me.application_note;
      card.replaceChildren();

      if (note) {
        card.append(
          createElement('div', { className: 'status__blot status__blot--red', 'aria-hidden': 'true' }, '!'),
          createElement('h1', {}, 'One thing to change'),
          createElement(
            'p',
            {},
            'Somebody looked at your profile and asked for a change before it goes live:'
          ),
          createElement('blockquote', { className: 'waitlist__note' }, note)
        );

        // Blue, not `.btn`'s default: red is the decline colour, and the first
        // step here is a positive one. Editing leads; resending follows once
        // the change is made, so it is the quieter of the two.
        const edit = createElement('button', { className: 'btn btn--blue btn--full', type: 'button' }, 'Edit my profile');
        edit.addEventListener('click', () => router.go('/profile'));

        const resend = createElement('button', { className: 'btn btn--ghost btn--full', type: 'button' }, 'Send it again');
        resend.addEventListener('click', async () => {
          resend.disabled = true;
          try {
            await api.resubmitProfile();
            store.setState({ me: await api.getMe() });
            toast('Sent. You’re back in the queue.');
            paint();
          } catch (error) {
            toast(error.message, { error: true });
            resend.disabled = false;
          }
        });
        card.append(createElement('div', { className: 'waitlist__actions' }, [edit, resend]));
      } else {
        card.append(
          createElement('div', { className: 'status__blot', 'aria-hidden': 'true' }, '…'),
          createElement('h1', {}, 'You’re on the waitlist'),
          createElement(
            'p',
            {},
            'A person looks at every new profile before it goes live. Until then nobody can see you, and you can’t see anybody.'
          ),
          createElement(
            'p',
            {},
            'There’s no email on Overtone, so we can’t tell you when it happens. Check back — the app opens up as soon as you’re approved.'
          )
        );

        const check = createElement('button', { className: 'btn btn--blue btn--full', type: 'button' }, 'Check again');
        check.addEventListener('click', async () => {
          check.disabled = true;
          try {
            const me = await api.getMe();
            if (me.status === 'waitlisted') {
              store.setState({ me });
              toast('Still waiting. Nothing to do yet.');
              paint();
            } else {
              await router.refresh(me);
            }
          } catch (error) {
            toast(error.message, { error: true });
            check.disabled = false;
          }
        });

        const edit = createElement('button', { className: 'btn btn--ghost btn--full', type: 'button' }, 'Edit my profile');
        edit.addEventListener('click', () => router.go('/profile'));
        card.append(createElement('div', { className: 'waitlist__actions' }, [check, edit]));
      }

      const foot = createElement('p', { className: 'waitlist__foot' });
      const settings = createElement('button', { className: 'waitlist__link', type: 'button' }, 'Settings');
      settings.addEventListener('click', () => router.go('/settings'));
      const out = createElement('button', { className: 'waitlist__link', type: 'button' }, 'Sign out');
      out.addEventListener('click', () => {
        store.signOut();
        router.go('/');
      });
      foot.append(settings, ' · ', out);
      card.append(foot);
    }

    paint();
    return page;
  },
};
