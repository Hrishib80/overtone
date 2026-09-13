import { createElement } from '../utils/dom.js';
import { profileBody } from '../components/profile.js';
import api from '../services/api.js';
import store from '../services/store.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

/* The whole product, in one screen.

   Round 1 is two photographs and nothing else — the choice has to be a snap
   judgement or it measures something different. Round 2 is the same two
   people, days later, with everything showing. The UI difference between them
   is the mechanic, so the two layouts are deliberately not shared.

   The third state is the unlock, and it interrupts on purpose. It is the one
   moment the loop pays off, and sliding it into a corner as a badge would make
   a page of photographs out of the only thing here that is about a person.

   Motion does one job on this screen: answering the tap. A choice that
   produces no visible result before the next pair appears feels like the app
   swallowed it. */

const SETTLE_MS = 360; // long enough to read the result of a tap, short enough not to wait on it

/* The heading names what it is showing, from the viewer's own interested_in
   segment. "Which person?" for nonbinary rather than inventing a noun. */
const SEGMENT_NOUN = {
  man: 'man',
  woman: 'woman',
  nonbinary: 'person',
};

export default {
  async render() {
    const page = createElement('div', { className: 'pairs' });

    const header = createElement('header', { className: 'pairs__head' });

    const mark = createElement('p', { className: 'pairs__mark' });
    mark.innerHTML = 'Over<b>tone</b>';

    // Two weights in one line, the way the source sets it: the light half is
    // the question, the bold half is the thing being asked about.
    const heading = createElement('h1', { className: 'pairs__title' });
    const headingLead = createElement('span', { className: 'pairs__title-lead' }, 'Which ');
    const headingWord = createElement('b', { className: 'pairs__title-word' }, 'one');
    heading.append(headingLead, headingWord, '?');

    const sub = createElement('p', { className: 'pairs__sub' }, '');

    const who = createElement('p', { className: 'pairs__who' });
    header.append(mark, heading, sub, who);

    function renderSignedIn() {
      const email = store.getState().me?.email;
      who.replaceChildren();
      if (!email) return;
      who.append('signed in as ', createElement('b', {}, email), ' · ');
      const out = createElement('button', { className: 'pairs__signout', type: 'button' }, 'sign out');
      out.addEventListener('click', () => {
        store.signOut();
        router.go('/');
      });
      who.append(out);
    }

    const stage = createElement('main', { className: 'pairs__stage' });

    const foot = createElement('footer', { className: 'pairs__foot' });
    const inboxLink = createElement('button', { className: 'pairs__inbox', type: 'button' }, 'your people');
    const inboxDot = createElement('span', { className: 'pairs__badge', hidden: 'hidden' });
    inboxLink.append(inboxDot);
    inboxLink.addEventListener('click', () => router.go('/inbox'));
    foot.append(inboxLink);

    page.append(header, stage, foot);

    let busy = false;
    /** subject id -> the element that represents them in the current pair. */
    let choices = new Map();

    function setStage(...nodes) {
      stage.replaceChildren(...nodes);
    }

    function say(text) {
      if (sub.textContent === text) return;
      sub.textContent = text;
      sub.classList.remove('is-new');
      void sub.offsetWidth; // restart the animation rather than skip it
      sub.classList.add('is-new');
    }

    /** Once on mount: is there anything worth going to the inbox for? */
    async function checkInbox() {
      try {
        const inbox = await api.getInbox();
        const waiting = inbox.requests.length + inbox.unlocked.length;
        inboxDot.hidden = waiting === 0;
      } catch {
        inboxDot.hidden = true; // never let a badge be the reason a page errors
      }
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
          'Pairs appear once enough people have joined. We will let you know the moment they do.'
        )
      );
      return card;
    }

    /* ---- the unlock ---- */
    function renderUnlock(subject, connectionId) {
      headingLead.textContent = connectionId ? 'You both ' : 'You keep ';
      headingWord.textContent = connectionId ? 'did' : 'choosing them';
      say(
        connectionId
          ? 'They picked you too, so there is nothing to ask. Go and talk.'
          : 'Enough times that it is not chance. Here they are, properly.'
      );

      const card = createElement('article', { className: 'reveal reveal--blue unlocked' });

      // The contents arrive in sequence rather than all at once: the profile
      // is the reward, and a reward that simply appears reads as a page load.
      profileBody(subject).forEach((node, i) => {
        node.classList.add('rise');
        node.style.setProperty('--rise-delay', `${140 + i * 70}ms`);
        card.append(node);
      });

      const actions = createElement('div', { className: 'unlocked__actions rise' });
      actions.style.setProperty('--rise-delay', '460ms');

      const write = createElement(
        'button',
        { className: 'btn btn--full', type: 'button' },
        connectionId ? 'Open the conversation' : 'Say something'
      );
      write.addEventListener('click', () => router.go('/inbox'));

      const later = createElement('button', { className: 'btn btn--ghost btn--full', type: 'button' }, 'Later');
      later.addEventListener('click', () => {
        say('They are waiting in Your people, whenever you want.');
        load();
      });

      actions.append(write, later);
      card.append(actions);

      inboxDot.hidden = false;
      setStage(card);
      window.scrollTo({ top: 0 });
    }

    /** The tap, answered: the picked one holds, the other stands down. */
    function settle(chosenId) {
      for (const [id, element] of choices) {
        element.classList.add(id === chosenId ? 'is-picked' : 'is-passed');
        element.disabled = true;
      }
    }

    async function choose(pairing, subjectId) {
      if (busy) return;
      busy = true;
      settle(subjectId);

      try {
        const [result] = await Promise.all([
          api.decidePair(pairing.id, subjectId),
          new Promise((resolve) => setTimeout(resolve, SETTLE_MS)),
        ]);

        if (result.unlocked) {
          busy = false;
          renderUnlock(result.unlocked, result.connection_id);
          return;
        }

        if (result.round_two_scheduled) {
          // Said once, quietly — the delay is the point, and a viewer who
          // knows a second look is coming treats the first one differently.
          say("You'll see these two again in a few days, with everything showing.");
        }
        await load();
      } catch (error) {
        toast(error.message, { error: true });
        busy = false;
        await load();
      }
    }

    function setQuestion(segment) {
      headingWord.textContent = SEGMENT_NOUN[segment] || 'one';
    }

    /* ---- round 1: two photos, nothing else ---- */
    function renderRoundOne(pairing) {
      headingLead.textContent = 'Which ';
      setQuestion(pairing.segment);
      if (!sub.textContent) say('pick the one you prefer');

      const row = createElement('div', { className: 'choice-row' });
      choices = new Map();

      pairing.subjects.forEach((subject, index) => {
        const button = createElement('button', {
          className: `choice ${index === 0 ? 'choice--red' : 'choice--blue'}`,
          type: 'button',
          'aria-label': index === 0 ? 'Choose the person on the left' : 'Choose the person on the right',
        });
        button.style.setProperty('--rise-delay', `${index * 60}ms`);
        button.classList.add('rise');

        if (subject.photo_url) {
          button.append(createElement('img', { src: subject.photo_url, alt: '' }));
        } else {
          button.append(createElement('div', { className: 'choice__missing' }, 'No photo'));
        }

        button.addEventListener('click', () => choose(pairing, subject.id));
        choices.set(subject.id, button);
        row.append(button);
      });

      // Sits over the seam between the two, so the pair reads as one object
      // rather than two things that happen to be adjacent.
      row.append(createElement('div', { className: 'vs', 'aria-hidden': 'true' }, 'vs'));
      setStage(row);
    }

    /* ---- round 2: the same two, revealed ---- */
    function profileCard(subject, index, pairing) {
      const card = createElement('article', {
        className: `reveal rise ${index === 0 ? 'reveal--red' : 'reveal--blue'}`,
      });
      card.style.setProperty('--rise-delay', `${index * 80}ms`);
      card.append(...profileBody(subject));

      const pick = createElement(
        'button',
        { className: 'btn btn--full reveal__pick', type: 'button' },
        `Choose ${subject.display_name || 'them'}`
      );
      pick.addEventListener('click', () => choose(pairing, subject.id));

      card.append(pick);
      choices.set(subject.id, card);
      return card;
    }

    function renderRoundTwo(pairing) {
      headingLead.textContent = 'Which ';
      setQuestion(pairing.segment);
      say('you saw these two before — now with everything showing');

      const row = createElement('div', { className: 'reveal-row' });
      choices = new Map();
      pairing.subjects.forEach((subject, index) => row.append(profileCard(subject, index, pairing)));
      setStage(row);
    }

    async function load() {
      busy = true;
      setStage(skeleton());

      try {
        const { pair } = await api.getNextPair();
        if (!pair) {
          headingLead.textContent = 'Nobody ';
          headingWord.textContent = 'yet';
          say('');
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

    page.mounted = () => {
      renderSignedIn();
      load();
      checkInbox();
    };
    return page;
  },
};
