import { createElement } from '../utils/dom.js';
import router from '../services/router.js';

/* The front door.

   The old version described the mechanic in a sentence and then gave you two
   circles that looked like the mechanic but were actually the nav. This one
   puts the real thing first: a pair, the app's own question over it, and a
   choice that answers rather than navigates. You understand pairwise
   comparison by doing it once, which takes about a second and is the only
   thing on this page that a competitor's landing page could not also say.

   The forms are abstract on purpose. Inventing faces for a dating app's front
   page would be a lie about what is inside, and the gesture is the point, not
   the subject — the difference between the two is named after you pick, which
   is exactly what the product does with a face.

   Everything is visible at rest. Nothing here waits for a scroll to appear:
   the first frame is what a shared link and a skimming reader both get. */

/* Each round is two forms that differ in exactly one nameable way, and each
   side has its own word — because the whole point is that the answer depends
   on what you picked. A fixed line per round would be the page telling you
   what you chose, which is precisely the thing a swipe app does. */
const EVEN = '52% 48% 49% 51% / 51% 49% 51% 49%';

const ROUNDS = [
  {
    // Siblings, not opposites. Red against blue would be two brand colours
    // and a trivial choice; the claim this page makes is that the two things
    // are *nearly the same*, so the difference has to be small enough that
    // you notice yourself preferring rather than deciding.
    a: { word: 'warmer', hue: 14, sat: 66, light: 48, squish: EVEN },
    b: { word: 'cooler', hue: 336, sat: 66, light: 48, squish: EVEN },
  },
  {
    a: { word: 'rounder', hue: 214, sat: 56, light: 48, squish: '50% 50% 50% 50% / 50% 50% 50% 50%' },
    b: { word: 'looser', hue: 214, sat: 56, light: 48, squish: '63% 37% 44% 56% / 41% 59% 41% 59%' },
  },
  {
    a: { word: 'heavier', hue: 348, sat: 60, light: 42, squish: EVEN, scale: 1 },
    b: { word: 'lighter', hue: 348, sat: 60, light: 60, squish: EVEN, scale: 0.86 },
  },
];

/* [light half, bold half, punctuation] — the same two-weight heading the
   pair view uses, one per round. */
const ASKS = [
  ['Which ', 'one', '?'],
  ['And ', 'now', '?'],
  ['One ', 'more', '.'],
];

export default {
  async render() {
    const page = createElement('div', { className: 'landing' });

    // ---- hero --------------------------------------------------------------

    const hero = createElement('section', { className: 'landing__hero' });

    const mark = createElement('p', { className: 'landing__mark' }, 'OVERTONE');

    /* The same two-weight split the pair view uses for its heading, so the
       landing page is a rehearsal of the first screen rather than a different
       product wearing the same colours. */
    const ask = createElement('h1', { className: 'landing__ask' });

    function setAsk([lead, word, tail]) {
      ask.replaceChildren(
        createElement('span', { className: 'landing__ask-lead' }, lead),
        createElement('b', { className: 'landing__ask-word' }, word),
        tail
      );
    }
    setAsk(ASKS[0]);

    const pair = createElement('div', { className: 'pair', role: 'group' });
    const left = blot('left');
    const right = blot('right');
    pair.append(left.el, right.el);

    /* One line under the pair that changes as you play. It is the only thing
       on the page that reacts, so it carries the whole demonstration. */
    const said = createElement('p', { className: 'landing__said' });

    const cta = createElement('div', { className: 'landing__cta' });
    const join = createElement('button', { className: 'landing__join', type: 'button' }, 'Join Overtone');
    join.addEventListener('click', () => router.go('/join'));
    const signin = createElement('button', { className: 'landing__signin', type: 'button' }, 'Sign in');
    signin.addEventListener('click', () => router.go('/signin'));
    cta.append(join, signin);

    hero.append(mark, ask, pair, said, cta);

    // ---- the demonstration -------------------------------------------------

    let round = 0;
    let busy = false;
    const leaned = [];

    function blot(side) {
      const el = createElement('button', {
        className: `blot blot--${side}`,
        type: 'button',
        'aria-label': `Pick the ${side} one`,
      });
      const form = createElement('span', { className: 'blot__form', 'aria-hidden': 'true' });
      el.append(form);
      return { el, form };
    }

    function dress(target, spec) {
      target.form.style.setProperty('--hue', spec.hue);
      target.form.style.setProperty('--sat', `${spec.sat}%`);
      target.form.style.setProperty('--light', `${spec.light}%`);
      target.form.style.setProperty('--squish', spec.squish);
      target.form.style.setProperty('--form-scale', spec.scale ?? 1);
    }

    function deal() {
      const spec = ROUNDS[round];
      // Sides shuffled, so three rounds of picking the left one is a
      // preference rather than a habit — the same reason the pair view
      // randomises position.
      const flip = Math.random() < 0.5;
      dress(left, flip ? spec.b : spec.a);
      dress(right, flip ? spec.a : spec.b);
      left.el.dataset.choice = flip ? 'b' : 'a';
      right.el.dataset.choice = flip ? 'a' : 'b';

      setAsk(ASKS[round]);

      for (const node of [left.el, right.el]) {
        node.classList.remove('is-picked', 'is-passed');
        node.disabled = false;
      }
    }

    function finish() {
      setAsk(['That is ', 'the app', '.']);
      // Read back, because being told what you chose is the moment the idea
      // lands. Three shapes is a parlour trick; three faces is a preference.
      said.replaceChildren(
        createElement('b', {}, `${leaned.join(', ')}.`),
        ' Three choices between two things that were nearly the same, and we can already name what you went for. Do that with people who genuinely look alike and the difference is the person.'
      );
      said.classList.add('is-final');
      pair.classList.add('is-done');
      cta.classList.add('is-loud');
    }

    function choose(node) {
      if (busy) return;
      busy = true;

      const picked = ROUNDS[round][node.dataset.choice];
      const other = node === left.el ? right.el : left.el;
      node.classList.add('is-picked');
      other.classList.add('is-passed');
      node.disabled = other.disabled = true;
      leaned.push(picked.word);

      said.textContent = `You went for the ${picked.word} one.`;
      said.classList.add('is-new');

      setTimeout(() => {
        said.classList.remove('is-new');
        round += 1;
        busy = false;
        if (round >= ROUNDS.length) finish();
        else deal();
      }, 1100);
    }

    left.el.addEventListener('click', () => choose(left.el));
    right.el.addEventListener('click', () => choose(right.el));

    deal();
    said.textContent = 'Pick either. Nothing is saved, and there is no wrong answer.';

    // ---- the argument ------------------------------------------------------

    const why = createElement('section', { className: 'landing__why' });
    why.append(
      createElement('h2', { className: 'landing__h2' }, 'Why not swiping'),
      column(
        'A swipe is a judgement on its own',
        'Right or left, against a private bar that moves all day. Two people swiping right can mean completely different things, and neither of them means much.'
      ),
      column(
        'A choice is a judgement against something',
        'Two people who look alike, side by side. Everything they share cancels out, so what is left is what you actually responded to. A handful of pairs says more than a hundred swipes.'
      )
    );

    // ---- round two ---------------------------------------------------------

    const later = createElement('section', { className: 'landing__later' });
    later.append(
      createElement('p', { className: 'landing__eyebrow' }, 'Two days later'),
      createElement('h2', { className: 'landing__h2' }, 'The same two people come back'),
      createElement(
        'p',
        { className: 'landing__body' },
        'With everything showing this time — what they wrote, how they sound, who they are. You choose again, and that second choice counts for more than the first. It is the difference between a face you liked and a person you would like.'
      )
    );

    // ---- foot --------------------------------------------------------------

    const foot = createElement('footer', { className: 'landing__foot' });
    const footJoin = createElement('button', { className: 'landing__join', type: 'button' }, 'Join Overtone');
    footJoin.addEventListener('click', () => router.go('/join'));
    foot.append(
      footJoin,
      createElement('p', { className: 'landing__fine' }, '18+ · Your photos are analysed only to find people who look like you · You can delete everything, any time')
    );

    page.append(hero, why, later, foot);

    // ---- the magnetic pull, kept ------------------------------------------

    /* Pointer-only and motion-aware. On a touch screen there is no hover to
       answer and the transform only fights the tap. */
    const fine = window.matchMedia('(pointer: fine)');
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    let onMove = null;

    if (fine.matches && !reduced.matches) {
      onMove = (event) => {
        for (const node of [left.el, right.el]) {
          const box = node.getBoundingClientRect();
          if (!box.width) continue;
          const dx = event.clientX - (box.left + box.width / 2);
          const dy = event.clientY - (box.top + box.height / 2);
          const distance = Math.hypot(dx, dy);
          const reach = box.width * 1.5;
          const strength = distance < reach ? (1 - distance / reach) * 12 : 0;
          node.style.setProperty('--pull-x', `${(dx / (distance || 1)) * strength}px`);
          node.style.setProperty('--pull-y', `${(dy / (distance || 1)) * strength}px`);
        }
      };
      window.addEventListener('pointermove', onMove, { passive: true });
    }

    page.destroy = () => {
      if (onMove) window.removeEventListener('pointermove', onMove);
    };

    return page;
  },
};

function column(heading, body) {
  const wrap = createElement('div', { className: 'landing__col' });
  wrap.append(
    createElement('h3', { className: 'landing__h3' }, heading),
    createElement('p', { className: 'landing__body' }, body)
  );
  return wrap;
}
