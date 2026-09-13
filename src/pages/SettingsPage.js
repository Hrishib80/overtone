import { createElement } from '../utils/dom.js';
import { navbar, reviewerLink } from '../components/navbar.js';
import api from '../services/api.js';
import router from '../services/router.js';
import store from '../services/store.js';
import { toast } from '../utils/toast.js';

/* The three things a person is entitled to do to their own account.

   They sit on one screen because they are the same kind of act — taking
   something back — and because a permission you can only give is not a
   permission. Withdrawing face analysis and deleting the account are both one
   screen away from the app, not buried behind a support email.

   The blocked list lives here too: blocking happens in the moment, on a
   profile, and this is the only place it can be undone once the moment has
   passed.
*/

export default {
  async render() {
    const page = createElement('div', { className: 'settings' });

    // Mid-onboarding there is no bar to show — the funnel has its own way
    // out, and half a navigation is worse than none.
    const onboarding = store.getState().me?.status === 'onboarding';
    const nav = onboarding ? null : navbar('/settings');
    if (nav) reviewerLink(nav);

    const head = createElement('header', { className: 'settings__head' });
    const headInner = createElement('div', { className: 'settings__head-inner' });
    if (onboarding) {
      const back = createElement('button', { className: 'settings__back', type: 'button' });
      back.innerHTML = '<span aria-hidden="true">&larr;</span> Your profile';
      back.addEventListener('click', () => router.go('/onboarding'));
      headInner.append(back);
    }
    headInner.append(createElement('h1', { className: 'settings__title' }, 'Settings'));
    head.append(headInner);

    const body = createElement('main', { className: 'settings__body' });
    page.append(...(nav ? [nav] : []), head, body);

    function section(title, lede) {
      const card = createElement('section', { className: 'settings__card' });
      card.append(createElement('h2', { className: 'settings__card-title' }, title));
      if (lede) card.append(createElement('p', { className: 'settings__lede' }, lede));
      return card;
    }

    // ---- blocked people --------------------------------------------------

    const blocksCard = section(
      'People you’ve blocked',
      'They can’t see you and you can’t see them. Nobody is told either way.'
    );
    const blockList = createElement('div', { className: 'settings__list' });
    blocksCard.append(blockList);

    async function loadBlocks() {
      blockList.replaceChildren(createElement('p', { className: 'settings__muted' }, 'Loading…'));
      let people = [];
      try {
        ({ blocks: people } = await api.getBlocks());
      } catch (error) {
        blockList.replaceChildren(
          createElement('p', { className: 'settings__muted' }, error.message)
        );
        return;
      }

      if (!people.length) {
        blockList.replaceChildren(
          createElement('p', { className: 'settings__muted' }, 'Nobody. Good.')
        );
        return;
      }

      blockList.replaceChildren(
        ...people.map((person) => {
          const row = createElement('div', { className: 'settings__row' });
          row.append(
            createElement(
              'span',
              { className: 'settings__row-name' },
              person.display_name || 'Someone who has since left'
            )
          );
          const undo = createElement(
            'button',
            { className: 'btn btn--ghost btn--small', type: 'button' },
            'Unblock'
          );
          undo.addEventListener('click', async () => {
            undo.disabled = true;
            try {
              await api.unblockUser(person.id);
              toast('Unblocked.');
            } catch (error) {
              toast(error.message, { error: true });
              undo.disabled = false;
              return;
            }
            await loadBlocks();
          });
          row.append(undo);
          return row;
        })
      );
    }

    // ---- face analysis ---------------------------------------------------

    const consentCard = section('Reading faces from your photos');
    const consentBody = createElement('div', { className: 'settings__consent' });
    consentCard.append(consentBody);

    async function loadConsent() {
      let state;
      try {
        state = await api.getConsent();
      } catch (error) {
        consentBody.replaceChildren(
          createElement('p', { className: 'settings__muted' }, error.message)
        );
        return;
      }

      consentBody.replaceChildren(createElement('p', { className: 'settings__lede' }, state.purpose));

      const granted = state.granted && !state.needs_restatement;
      const status = createElement(
        'p',
        { className: `settings__status ${granted ? 'is-on' : 'is-off'}` },
        granted ? 'Allowed' : 'Not allowed'
      );
      consentBody.append(status);

      if (granted) {
        // Said plainly, because somebody weighing this up deserves to know
        // what it costs rather than find out afterwards.
        consentBody.append(
          createElement(
            'p',
            { className: 'settings__note' },
            'Withdrawing deletes the description of your face straight away. Your photos stay, but you stop appearing in pairs, because there is nothing left to compare you on.'
          )
        );
        const stop = createElement(
          'button',
          { className: 'btn btn--ghost', type: 'button' },
          'Withdraw permission'
        );
        stop.addEventListener('click', async () => {
          stop.disabled = true;
          try {
            await api.withdrawConsent();
            toast('Withdrawn. Your face data is deleted.');
          } catch (error) {
            toast(error.message, { error: true });
            stop.disabled = false;
            return;
          }
          await loadConsent();
        });
        consentBody.append(stop);
      } else {
        const allow = createElement(
          'button',
          { className: 'btn', type: 'button' },
          'Allow'
        );
        allow.addEventListener('click', async () => {
          allow.disabled = true;
          try {
            await api.giveConsent();
          } catch (error) {
            toast(error.message, { error: true });
            allow.disabled = false;
            return;
          }
          await loadConsent();
        });
        consentBody.append(allow);
      }
    }

    // ---- deletion --------------------------------------------------------

    const deleteCard = section(
      'Delete your account',
      'Everything goes: your photos, your answers, your conversations and the comparisons you made. It cannot be undone, and there is no grace period to change your mind in.'
    );
    deleteCard.classList.add('settings__card--grave');

    const openDelete = createElement(
      'button',
      { className: 'btn btn--danger', type: 'button' },
      'Delete my account'
    );
    const deleteForm = createElement('form', { className: 'settings__danger', hidden: 'hidden' });
    const password = createElement('input', {
      className: 'input',
      type: 'password',
      id: 'delete-password',
      autocomplete: 'current-password',
      placeholder: 'Your password',
      required: 'required',
    });
    const confirm = createElement(
      'button',
      { className: 'btn btn--danger', type: 'submit' },
      'Delete everything'
    );
    const cancel = createElement('button', { className: 'btn btn--ghost', type: 'button' }, 'Keep it');
    const row = createElement('div', { className: 'settings__danger-actions' });
    row.append(confirm, cancel);
    deleteForm.append(
      createElement(
        'label',
        { className: 'settings__label', for: 'delete-password' },
        'Type your password to confirm'
      ),
      password,
      row
    );

    openDelete.addEventListener('click', () => {
      openDelete.hidden = true;
      deleteForm.hidden = false;
      password.focus();
    });
    cancel.addEventListener('click', () => {
      deleteForm.hidden = true;
      openDelete.hidden = false;
      password.value = '';
    });

    deleteForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      confirm.disabled = true;
      try {
        await api.deleteAccount(password.value);
      } catch (error) {
        toast(error.message, { error: true });
        confirm.disabled = false;
        return;
      }
      // Signing out locally first, so the router never tries to resolve a
      // token whose account no longer exists.
      store.signOut();
      router.go('/');
      toast('Your account is gone. Take care.');
    });

    deleteCard.append(openDelete, deleteForm);

    body.append(blocksCard, consentCard, deleteCard);

    page.mounted = () => {
      nav?.mounted();
      return Promise.all([loadBlocks(), loadConsent()]);
    };
    page.destroy = () => nav?.destroy();
    return page;
  },
};
