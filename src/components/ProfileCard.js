import { createElement, escapeHtml } from '../utils/dom.js';

export function createProfileCard(profile) {
  const card = createElement('div', {
    className: 'swipe-card',
    dataset: { id: profile.id }
  });

  const scroll = createElement('div', { className: 'swipe-card__scroll' });

  const photos = (profile.photos && profile.photos.length > 0)
    ? profile.photos
    : (profile.avatarUrl ? [profile.avatarUrl] : []);
  const prompts = profile.prompts || [];

  if (photos.length > 0) {
    const hero = createElement('div', { className: 'swipe-card__hero' });
    const heroImg = createElement('img', {
      className: 'swipe-card__photo swipe-card__photo--hero',
      src: escapeHtml(photos[0]),
      alt: ''
    });
    hero.appendChild(heroImg);

    const heroInfo = createElement('div', { className: 'swipe-card__hero-info' });
    heroInfo.appendChild(
      createElement('h2', { className: 'swipe-card__name' }, escapeHtml(profile.displayName || 'Unknown'))
    );
    if (profile.score != null) {
      heroInfo.appendChild(
        createElement('p', { className: 'swipe-card__meta' }, `${Math.round(profile.score * 100)}% vibe match`)
      );
    }
    hero.appendChild(heroInfo);
    scroll.appendChild(hero);
  } else {
    const header = createElement('div', { className: 'swipe-card__header-block' });
    header.appendChild(
      createElement('h2', { className: 'swipe-card__name' }, escapeHtml(profile.displayName || 'Unknown'))
    );
    scroll.appendChild(header);
  }

  let photoIndex = 1;
  prompts.forEach((prompt) => {
    const block = createElement('div', { className: 'swipe-card__prompt' });
    block.appendChild(
      createElement('p', { className: 'swipe-card__prompt-q' }, escapeHtml(prompt.question))
    );
    block.appendChild(
      createElement('p', { className: 'swipe-card__prompt-a' }, escapeHtml(prompt.answer))
    );
    scroll.appendChild(block);

    if (photoIndex < photos.length) {
      scroll.appendChild(createPhoto(photos[photoIndex]));
      photoIndex++;
    }
  });

  while (photoIndex < photos.length) {
    scroll.appendChild(createPhoto(photos[photoIndex]));
    photoIndex++;
  }

  if (prompts.length === 0 && photos.length <= 1) {
    const empty = createElement('div', { className: 'swipe-card__empty-note' });
    empty.textContent = 'No prompts yet — say hi if you like their vibe!';
    scroll.appendChild(empty);
  }

  card.appendChild(scroll);

  card.appendChild(createElement('div', { className: 'stamp stamp--like' }, 'LIKE'));
  card.appendChild(createElement('div', { className: 'stamp stamp--nope' }, 'NOPE'));

  return card;
}

function createPhoto(src) {
  const wrap = createElement('div', { className: 'swipe-card__photo-wrap' });
  wrap.appendChild(createElement('img', {
    className: 'swipe-card__photo',
    src: escapeHtml(src),
    alt: ''
  }));
  return wrap;
}

export function animateCardAction(card, action) {
  card.classList.add(action === 'like' ? 'swipe-right' : 'swipe-left');
}
