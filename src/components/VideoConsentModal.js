import { createElement, escapeHtml } from '../utils/dom.js';

const icons = {
  camera: '<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/></svg>'
};

export function createVideoConsentModal(peerName, { onAccept, onDecline }) {
  const overlay = createElement('div', { className: 'modal-overlay' });
  const modal = createElement('div', { className: 'consent-modal' });
  
  const icon = createElement('div', { className: 'consent-modal__icon' });
  icon.innerHTML = icons.camera;
  modal.appendChild(icon);
  
  const title = createElement('h3', { className: 'consent-modal__title' }, `${escapeHtml(peerName)} wants to enable video`);
  modal.appendChild(title);
  
  const subtitle = createElement('p', { className: 'consent-modal__subtitle' }, 'Your camera will only be activated after you accept');
  modal.appendChild(subtitle);
  
  const actions = createElement('div', { className: 'consent-modal__actions' });
  
  const declineBtn = createElement('button', { className: 'btn btn--secondary' }, 'Decline');
  declineBtn.onclick = onDecline;
  
  const acceptBtn = createElement('button', { className: 'btn btn--primary' }, 'Accept');
  acceptBtn.onclick = onAccept;
  
  actions.appendChild(declineBtn);
  actions.appendChild(acceptBtn);
  modal.appendChild(actions);
  
  overlay.appendChild(modal);
  
  overlay.destroy = () => {
    overlay.remove();
  };
  
  return overlay;
}
