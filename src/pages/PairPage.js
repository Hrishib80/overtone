import { createElement } from '../utils/dom.js';
import { profileBody } from '../components/profile.js';
import api from '../services/api.js';
import store from '../services/store.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

/* The whole product, in one screen.

   Round 1 is two photographs and nothing else — the choice has to be a snap
   judgement or it measures something different. Round 2 is the same two
   people, days later, with everything showing. The UI difference between
   them is the mechanic, so the two layouts are deliberately not shared.

   The third state is the unlock, and it interrupts on purpose. It is the one
   moment the loop pays off, and sliding it into a corner as a badge would
   make a page of photographs out of the only thing here that is about a
   person. */

export default {
  async render() {
    const page = createElement('div', { className: 'pairs' });

    const header = createElement('header', { className: 'pairs__head' });
    const heading = createElement('h1', { className: 'pairs__title' }, 'Who would you rather?');
    const sub = createElement('p', { className: 'pairs__sub' }, '');
    header.append(heading, sub);

    const stage = createElement('main', { className: 'pairs__stage' });

    const foot = createElement('footer', { className: 'pairs__foot' });
    const inboxLink = createElement('button', { className: 'pairs__inbox', type: 'button' }, 'Your people');
    inboxLink.addEventListener('click', () => router.go('/inbox'));
    const signOut = createElement('button', { className: 'pairs__signout', type: 'button' }, 'Sign out');
    signOut.addEventListener('click', () => {
      store.signOut();
      router.go('/');
    });
    foot.append(inboxLink, signOut);

    page.append(header, stage, foot);

    let busy = false;

    function setStage(...nodes) {
      stage.replaceChildren(...nodes);
    }

    function skeleton() {
      const wrap = createElement('div', { className: 'choice-row' });
      for (let i = 0; i < 2; i += 1) {
        wrap.append(createElement('div', { className: 'choice choice--loading' }));
      }
      return wrap;
    }

    function emptyState() {
      const card = createElement('div', { className: 'pairs__empty' });
      card.append(
        createElement('div', { className: 'pairs__empty-blot' }),
        createElement('h2', {}, 'Nobody to show you yet'),
        createElement(
          'p',
          { className: 'muted' },
          'Pairs appear once enough people have joined your campus. We will let you know the moment they do.'
        )
      );
      return card;
    }

    /* ---- the unlock ---- */
    function renderUnlock(subject, connectionId) {
      heading.textContent = connectionId ? 'You both did' : 'You keep choosing them';
      sub.textContent = connectionId
        ? 'They picked you too, so there is nothing to ask. Go and talk.'
        : 'Enough times that it is not chance. Here they are, properly.';

      const card = createElement('article', { className: 'reveal reveal--blue unlocked' });
      card.append(...profileBody(subject));

      const actions = createElement('div', { className: 'unlocked__actions' });
      const write = createElement(
        'button',
        { className: 'btn btn--full', type: 'button' },
        connectionId ? 'Open the conversation' : 'Say something'
      );
      write.addEventListener('click', () => router.go('/inbox'));

      const later = createElement('button', { className: 'btn btn--ghost btn--full', type: 'button' }, 'Later');
      later.addEventListener('click', () => {
        sub.textContent = 'They are waiting in Your people, whenever you want.';
        load();
      });

      actions.append(write, later);
      card.append(actions);
      setStage(card);
      window.scrollTo({ top: 0 });
    }

    async function choose(pairing, subjectId) {
      if (busy) return;
      busy = true;

      try {
        const result = await api.decidePair(pairing.id, subjectId);

        if (result.unlocked) {
          busy = false;
          renderUnlock(result.unlocked, result.connection_id);
          return;
        }

        if (result.round_two_scheduled) {
          // Said once, quietly — the delay is the point, and a viewer who
          // knows a second look is coming treats the first one differently.
          sub.textContent = "You'll see these two again in a few days, with everything showing.";
        }
        await load();
      } catch (error) {
        toast(error.message, { error: true });
        busy = false;
      }
    }

    /* ---- round 1: two photos, nothing else ---- */
    function renderRoundOne(pairing) {
      heading.textContent = 'Who would you rather?';
      if (!sub.textContent) sub.textContent = 'Go with your gut.';

      const row = createElement('div', { className: 'choice-row' });

      pairing.subjects.forEach((subject, index) => {
        const button = createElement('button', {
          className: `choice ${index === 0 ? 'choice--red' : 'choice--blue'}`,
          type: 'button',
          'aria-label': index === 0 ? 'Choose the person on the left' : 'Choose the person on the right',
        });

        if (subject.photo_url) {
          button.append(createElement('img', { src: subject.photo_url, alt: '' }));
        } else {
          button.append(createElement('div', { className: 'choice__missing' }, 'No photo'));
        }

        button.addEventListener('click', () => choose(pairing, subject.id));
        row.append(button);
      });

      setStage(row);
    }

    /* ---- round 2: the same two, revealed ---- */
    function profileCard(subject, index, pairing) {
      const card = createElement('article', {
        className: `reveal ${index === 0 ? 'reveal--red' : 'reveal--blue'}`,
      });
      card.append(...profileBody(subject));

      const pick = createElement(
        'button',
        { className: 'btn btn--full reveal__pick', type: 'button' },
        `Choose ${subject.display_name || 'them'}`
      );
      pick.addEventListener('click', () => choose(pairing, subject.id));

      card.append(pick);
      return card;
    }

    function renderRoundTwo(pairing) {
      heading.textContent = 'You saw these two before';
      sub.textContent = 'Now with everything showing. Take your time.';

      const row = createElement('div', { className: 'reveal-row' });
      pairing.subjects.forEach((subject, index) => row.append(profileCard(subject, index, pairing)));
      setStage(row);
    }

    async function load() {
      busy = true;
      setStage(skeleton());

      try {
        const { pair } = await api.getNextPair();
        if (!pair) {
          heading.textContent = 'Overtone';
          sub.textContent = '';
          setStage(emptyState());
          return;
        }

        if (pair.round === 'round_2') renderRoundTwo(pair);
        else renderRoundOne(pair);

        window.scrollTo({ top: 0 });
      } catch (error) {
        toast(error.message, { error: true });
        setStage(emptyState());
      } finally {
        busy = false;
      }
    }

    page.mounted = () => load();
    return page;
  },
};
