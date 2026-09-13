import { createElement } from '../utils/dom.js';

/* How a revealed person is rendered, wherever they are revealed.

   Three surfaces show the same thing: the round-2 pair, the moment someone
   unlocks, and the inbox prompt for a person not yet written to. They have to
   look identical — the unlock is meant to read as "this is the round-2 view,
   and you earned it early", not as a different, lesser card. Sharing the
   renderer is the only way that survives the next edit to either one. */

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

export const pretty = (v) =>
  LABELS[v] || String(v).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/** Fields worth showing under a profile, in reading order. */
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

/**
 * @param {object} subject
 * @param {(photo: {id: string, url: string}) => void} [onReportPhoto]
 *   when given, each photo carries its own quiet report control — "this one",
 *   rather than a complaint about the person that a reviewer cannot act on.
 */
export function gallery(subject, onReportPhoto) {
  const wrap = createElement('div', { className: 'reveal__gallery' });
  for (const photo of subject.photos || []) {
    const frame = createElement('div', { className: 'reveal__frame' });
    frame.append(createElement('img', { src: photo.url, alt: '', loading: 'lazy' }));

    if (onReportPhoto) {
      const flag = createElement('button', {
        className: 'reveal__flag',
        type: 'button',
        title: 'Report this photo',
        'aria-label': 'Report this photo',
      });
      flag.textContent = '⚑';
      flag.addEventListener('click', (event) => {
        event.stopPropagation();
        onReportPhoto(photo);
      });
      frame.append(flag);
    }
    wrap.append(frame);
  }
  if (!(subject.photos || []).length) {
    wrap.append(createElement('div', { className: 'choice__missing' }, 'No photo'));
  }
  return wrap;
}

export function nameLine(subject) {
  const name = createElement('h2', { className: 'reveal__name' });
  name.append(subject.display_name || 'Unnamed');
  if (subject.age) name.append(createElement('span', { className: 'reveal__age' }, `${subject.age}`));
  return name;
}

export function facts(subject) {
  const list = createElement('dl', { className: 'reveal__facts' });
  for (const [key, label, format] of VITALS) {
    const value = subject[key];
    if (value === null || value === undefined || value === '') continue;
    list.append(createElement('dt', {}, label), createElement('dd', {}, format(value)));
  }
  if (subject.gender_identity) {
    list.append(
      createElement('dt', {}, 'Identifies as'),
      createElement('dd', {}, subject.gender_identity)
    );
  }
  if (Array.isArray(subject.languages) && subject.languages.length) {
    list.append(
      createElement('dt', {}, 'Speaks'),
      createElement('dd', {}, subject.languages.map(pretty).join(', '))
    );
  }
  return list;
}

export function prompts(subject) {
  const wrap = createElement('div', { className: 'reveal__prompts' });
  for (const prompt of subject.prompts || []) {
    const block = createElement('div', { className: 'prompt-card' });
    block.append(createElement('p', { className: 'prompt-card__q' }, prompt.text));

    if (prompt.kind === 'voice') {
      if (prompt.audio_url) {
        block.append(createElement('audio', { controls: 'true', src: prompt.audio_url }));
      } else {
        block.append(
          createElement('p', { className: 'prompt-card__pending' }, 'Recording still processing')
        );
      }
    } else {
      block.append(createElement('p', { className: 'prompt-card__a' }, prompt.body || ''));
    }
    wrap.append(block);
  }
  return wrap;
}

/** Everything about a person, in one card. Callers append their own action. */
export function profileBody(subject, { onReportPhoto } = {}) {
  return [
    gallery(subject, onReportPhoto),
    nameLine(subject),
    facts(subject),
    prompts(subject),
  ];
}
