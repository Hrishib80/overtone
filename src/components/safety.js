import { createElement } from '../utils/dom.js';
import { openSheet } from './sheet.js';
import api from '../services/api.js';
import { toast } from '../utils/toast.js';

/* Blocking and reporting, from wherever you are.

   Two acts, one entry point, because someone who needs one usually wants the
   other and sending them off to find a second screen at the moment they are
   upset is how people end up doing neither.

   Blocking is offered first and needs no reason. Reporting is underneath it,
   because it asks for more — a reason, maybe a note — and somebody who just
   wants this person gone should not have to fill in a form to get it. */

const REASON_LABELS = {
  fake: 'Not who they say they are',
  harassment: 'Harassment or abuse',
  sexual: 'Unwanted sexual content',
  underage: 'They look under 18',
  hate: 'Hate speech',
  other: 'Something else',
};

let cachedReasons = null;

async function reasons() {
  if (cachedReasons) return cachedReasons;
  try {
    const body = await api.getReportReasons();
    cachedReasons = body.reasons;
  } catch {
    // The server is the authority, but a network blip must not be the thing
    // standing between someone and reporting a person who is upsetting them.
    cachedReasons = Object.keys(REASON_LABELS);
  }
  return cachedReasons;
}

/**
 * @param {object} options
 * @param {{id: string, display_name?: string}} options.subject
 * @param {string} [options.context]  a connection id, so a reviewer can see where it happened
 * @param {() => void} [options.onDone]  called after the person is gone from view
 */
export async function openSafety({ subject, context, onDone }) {
  const available = await reasons();
  const name = subject.display_name || 'this person';

  openSheet({
    label: `Block or report ${name}`,
    build: ({ close }) => {
      const wrap = createElement('div', { className: 'safety' });
      let busy = false;

      async function run(work, done) {
        if (busy) return;
        busy = true;
        try {
          await work();
          close();
          toast(done);
          onDone?.();
        } catch (error) {
          toast(error.message, { error: true });
          busy = false;
        }
      }

      // ---- block ----
      const blockSection = createElement('section', { className: 'safety__block' });
      blockSection.append(
        createElement('h2', { className: 'safety__title' }, `Block ${name}`),
        createElement(
          'p',
          { className: 'safety__note' },
          'They stop appearing for you, you stop appearing for them, and any conversation closes. They are not told.'
        )
      );
      const blockBtn = createElement('button', { className: 'btn btn--full', type: 'button' }, 'Block');
      blockBtn.addEventListener('click', () =>
        run(() => api.blockUser(subject.id), `${name} is blocked.`)
      );
      blockSection.append(blockBtn);

      // ---- report ----
      const form = createElement('form', { className: 'safety__report' });
      form.append(
        createElement('h2', { className: 'safety__title' }, 'Or tell us what happened'),
        createElement(
          'p',
          { className: 'safety__note' },
          'A person reads every report. Blocking them as well is on by default.'
        )
      );

      const list = createElement('div', { className: 'safety__reasons' });
      available.forEach((value, index) => {
        const id = `reason-${subject.id}-${value}`;
        const row = createElement('label', { className: 'safety__reason', for: id });
        const input = createElement('input', {
          type: 'radio',
          name: `reason-${subject.id}`,
          id,
          value,
        });
        if (index === 0) input.checked = true;
        row.append(input, createElement('span', {}, REASON_LABELS[value] || value));
        list.append(row);
      });

      const note = createElement('textarea', {
        className: 'safety__text',
        id: `report-note-${subject.id}`,
        rows: '3',
        maxlength: '2000',
        placeholder: 'Anything that would help (optional)',
      });

      const alsoBlockId = `also-block-${subject.id}`;
      const alsoBlockRow = createElement('label', { className: 'safety__check', for: alsoBlockId });
      const alsoBlock = createElement('input', { type: 'checkbox', id: alsoBlockId });
      alsoBlock.checked = true;
      alsoBlockRow.append(alsoBlock, createElement('span', {}, `Block ${name} as well`));

      const send = createElement(
        'button',
        { className: 'btn btn--full safety__send', type: 'submit' },
        'Send report'
      );

      form.append(list, note, alsoBlockRow, send);
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        const chosen = form.querySelector('input[type=radio]:checked');
        run(
          () =>
            api.reportUser(subject.id, {
              reason: chosen?.value,
              note: note.value,
              context,
              block: alsoBlock.checked,
            }),
          'Thanks — someone will look at this.'
        );
      });

      wrap.append(blockSection, form);
      return wrap;
    },
  });
}

/** The control that opens it. Quiet on purpose: present, never prominent. */
export function safetyButton({ subject, context, onDone }) {
  const button = createElement('button', {
    className: 'safety-open',
    type: 'button',
    'aria-label': `Block or report ${subject.display_name || 'this person'}`,
    title: 'Block or report',
  });
  button.textContent = '⋯';
  button.addEventListener('click', () => openSafety({ subject, context, onDone }));
  return button;
}
