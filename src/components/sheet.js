import { createElement } from '../utils/dom.js';

/* A panel that slides up over the page.

   The inbox needs somewhere to put things that deserve the whole screen — a
   full profile, a conversation — without navigating away from the list that
   got you there. Stacking them into the list instead was the original mistake:
   three unlocked people became three full-height profiles to scroll past.

   Slides from the bottom on a phone and settles in the middle on a laptop,
   because on a phone the gesture the shape suggests is the one that works.
*/

let openCount = 0;

function lockScroll() {
  openCount += 1;
  document.body.style.overflow = 'hidden';
}

function unlockScroll() {
  openCount = Math.max(0, openCount - 1);
  if (openCount === 0) document.body.style.overflow = '';
}

/**
 * @param {object} options
 * @param {(api: {close: () => void}) => Node|Node[]} options.build  contents, given a way to dismiss itself
 * @param {string} [options.label]  accessible name for the dialog
 * @param {() => void} [options.onClose]  teardown — a socket, a timer — run as it starts closing
 */
export function openSheet({ build, label, onClose }) {
  // Restored on close: dismissing a sheet should put the keyboard back where
  // it was, not at the top of the document.
  const previouslyFocused = document.activeElement;

  const root = createElement('div', {
    className: 'sheet',
    role: 'dialog',
    'aria-modal': 'true',
    'aria-label': label || 'Details',
  });
  const scrim = createElement('div', { className: 'sheet__scrim' });
  const panel = createElement('div', { className: 'sheet__panel', tabindex: '-1' });
  const grip = createElement('div', { className: 'sheet__grip', 'aria-hidden': 'true' });
  const dismiss = createElement('button', {
    className: 'sheet__close',
    type: 'button',
    'aria-label': 'Close',
  });
  dismiss.innerHTML = '&times;';

  const body = createElement('div', { className: 'sheet__body' });

  let closing = false;

  function close() {
    if (closing) return;
    closing = true;
    root.classList.remove('is-open');
    document.removeEventListener('keydown', onKey);
    // Called at the decision to close rather than when the animation ends:
    // a caller tearing down a live connection should not have it linger for
    // the length of a transition.
    onClose?.();

    // The panel's own transition decides when it is gone. Reduced motion
    // shortens that to ~0ms globally, so the fallback is only for a browser
    // that never fires the event at all.
    let removed = false;
    const remove = () => {
      if (removed) return;
      removed = true;
      root.remove();
      unlockScroll();
      previouslyFocused?.focus?.();
    };
    panel.addEventListener('transitionend', (event) => {
      if (event.target === panel) remove();
    });
    setTimeout(remove, 400);
  }

  const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select, [tabindex]:not([tabindex="-1"])';

  function onKey(event) {
    if (event.key === 'Escape') {
      close();
      return;
    }
    if (event.key !== 'Tab') return;

    // `aria-modal` tells a screen reader this is modal; it does not stop Tab
    // walking out into the page underneath, which is still there and still
    // focusable. This does.
    const targets = [...panel.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null);
    if (!targets.length) {
      event.preventDefault();
      panel.focus();
      return;
    }
    const first = targets[0];
    const last = targets[targets.length - 1];
    const active = document.activeElement;

    if (event.shiftKey && (active === first || active === panel)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  scrim.addEventListener('click', close);
  dismiss.addEventListener('click', close);
  document.addEventListener('keydown', onKey);

  const contents = build({ close });
  body.append(...(Array.isArray(contents) ? contents : [contents]));

  panel.append(grip, dismiss, body);
  root.append(scrim, panel);
  document.body.append(root);
  lockScroll();

  // One frame between "in the DOM" and "open", or the transition has nothing
  // to transition from and the panel simply appears.
  requestAnimationFrame(() => {
    root.classList.add('is-open');
    panel.focus({ preventScroll: true });
  });

  return { close, body, panel };
}
