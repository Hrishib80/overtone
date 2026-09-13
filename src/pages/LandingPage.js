import { createElement } from '../utils/dom.js';
import router from '../services/router.js';

/* Two circles you choose between — the source composition's whole idea, and
   the same gesture the app itself is built on. */
export default {
  async render() {
    const page = createElement('div', { className: 'landing' });

    const stage = createElement('div', { className: 'landing__stage' });

    const mark = createElement('h1', { className: 'landing__mark' });
    mark.append('OVERTONE');

    const tagline = createElement(
      'p',
      { className: 'landing__tagline' },
      'Two people. Pick one. We learn who you actually look twice at.'
    );

    const picks = createElement('div', { className: 'picks' });

    const join = createElement('button', {
      className: 'pick pick--red',
      type: 'button',
      'aria-label': 'Join Overtone',
    });
    join.append(createElement('span', {}, 'join'));
    join.addEventListener('click', () => router.go('/join'));

    const signin = createElement('button', {
      className: 'pick pick--blue',
      type: 'button',
      'aria-label': 'Sign in',
    });
    signin.append(createElement('span', {}, 'sign in'));
    signin.addEventListener('click', () => router.go('/signin'));

    picks.append(join, signin);
    stage.append(mark, tagline, picks);

    const foot = createElement(
      'p',
      { className: 'landing__foot' },
      '18+'
    );

    page.append(stage, foot);

    /* Magnetic pull, lifted from the source. Pointer-only: on a touch screen
       there is no hover to answer, and the transform would just fight the tap. */
    const fine = window.matchMedia('(pointer: fine)');
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    let onMove = null;

    if (fine.matches && !reduced.matches) {
      onMove = (event) => {
        for (const blot of picks.children) {
          const box = blot.getBoundingClientRect();
          const dx = event.clientX - (box.left + box.width / 2);
          const dy = event.clientY - (box.top + box.height / 2);
          const distance = Math.hypot(dx, dy);
          const reach = box.width * 1.6;
          const strength = distance < reach ? (1 - distance / reach) * 14 : 0;
          blot.style.setProperty('--pull-x', `${(dx / (distance || 1)) * strength}px`);
          blot.style.setProperty('--pull-y', `${(dy / (distance || 1)) * strength}px`);
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
