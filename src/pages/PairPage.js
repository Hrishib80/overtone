import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import store from '../services/store.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';

/* The whole product, in one screen.

   Round 1 is two photographs and nothing else — the choice has to be a snap
   judgement or it measures something different. Round 2 is the same two
   people, days later, with everything showing. The UI difference between
   them is the mechanic, so the two layouts are deliberately not shared. */

const LABELS = {
  life_partner: 'Life partner',
  long_term: 'Long-term relationship',
  long_term_open_to_short: 'Long-term, open to short',
  short_term_open_to_long: 'Short-term, open to long',
  short_term: 'Short-term fun',
  figuring_it_out: 'Figuring it out',
  monogamy: 'Monogamy',
  non_monogamy: 'Non-monogamy',
  yes: 'Yes',
  sometimes: 'Sometimes',
  no: 'No',
  prefer_not_to_say: 'Prefer not to say',
};

const pretty = (v) => LABELS[v] || String(v).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/** Fields worth showing under a round-2 profile, in reading order. */
const VITALS = [
  ['height_cm', 'Height', (v) => `${v} cm`],
  ['location', 'Lives in', (v) => v],
  ['hometown', 'From', (v) => v],
  ['school', 'College', (v) => v],
  ['job_title', 'Work', (v) => v],
  ['dating_intentions', 'Looking for', pretty],
  ['relationship_type', 'Open to', pretty],
  ['religion', 'Beliefs', pretty],
  ['drinking', 'Drinks', pretty],
  ['smoking', 'Smokes', pretty],
];

export default {
  async render() {
    const page = createElement('div', { className: 'pairs' });

    const header = createElement('header', { className: 'pairs__head' });
    const heading = createElement('h1', { className: 'pairs__title' }, 'Who would you rather?');
    const sub = createElement('p', { className: 'pairs__sub' }, '');
    header.append(heading, sub);

    const stage = createElement('main', { className: 'pairs__stage' });

    const foot = createElement('footer', { className: 'pairs__foot' });
    const signOut = createElement('button', { className: 'pairs__signout', type: 'button' }, 'Sign out');
    signOut.addEventListener('click', () => {
      store.signOut();
      router.go('/');
    });
    foot.append(signOut);

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

    async function choose(pairing, subjectId) {
      if (busy) return;
      busy = true;

      try {
        const result = await api.decidePair(pairing.id, subjectId);
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

      const gallery = createElement('div', { className: 'reveal__gallery' });
      for (const url of subject.photos || []) {
        gallery.append(createElement('img', { src: url, alt: '', loading: 'lazy' }));
      }
      if (!(subject.photos || []).length) {
        gallery.append(createElement('div', { className: 'choice__missing' }, 'No photo'));
      }

      const name = createElement('h2', { className: 'reveal__name' });
      name.append(subject.display_name || 'Unnamed');
      if (subject.age) name.append(createElement('span', { className: 'reveal__age' }, `${subject.age}`));

      const facts = createElement('dl', { className: 'reveal__facts' });
      for (const [key, label, format] of VITALS) {
        const value = subject[key];
        if (value === null || value === undefined || value === '') continue;
        facts.append(
          createElement('dt', {}, label),
          createElement('dd', {}, format(value))
        );
      }
      if (subject.gender_identity) {
        facts.append(createElement('dt', {}, 'Identifies as'), createElement('dd', {}, subject.gender_identity));
      }
      if (Array.isArray(subject.languages) && subject.languages.length) {
        facts.append(
          createElement('dt', {}, 'Speaks'),
          createElement('dd', {}, subject.languages.map(pretty).join(', '))
        );
      }

      const prompts = createElement('div', { className: 'reveal__prompts' });
      for (const prompt of subject.prompts || []) {
        const block = createElement('div', { className: 'prompt-card' });
        block.append(createElement('p', { className: 'prompt-card__q' }, prompt.text));

        if (prompt.kind === 'voice') {
          if (prompt.audio_url) {
            block.append(createElement('audio', { controls: 'true', src: prompt.audio_url }));
          } else {
            block.append(createElement('p', { className: 'prompt-card__pending' }, 'Recording still processing'));
          }
        } else {
          block.append(createElement('p', { className: 'prompt-card__a' }, prompt.body || ''));
        }
        prompts.append(block);
      }

      const pick = createElement(
        'button',
        { className: 'btn btn--full reveal__pick', type: 'button' },
        `Choose ${subject.display_name || 'them'}`
      );
      pick.addEventListener('click', () => choose(pairing, subject.id));

      card.append(gallery, name, facts, prompts, pick);
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
