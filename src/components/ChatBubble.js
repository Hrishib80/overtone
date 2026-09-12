import { createElement, escapeHtml, formatTime } from '../utils/dom.js';

export function createChatBubble(message) {
  const liClass = message.isMine ? 'message message--sent new' : 'message message--received new';
  const li = createElement('li', { className: liClass });
  
  const bubble = createElement('div', { className: 'message__bubble' });
  bubble.textContent = message.text; // Text content safely escapes HTML
  li.appendChild(bubble);
  
  const meta = createElement('div', { className: 'message__meta' });
  const time = createElement('span', { className: 'message__time' }, formatTime(message.timestamp));
  meta.appendChild(time);
  
  if (message.isMine) {
    const status = createElement('span', { className: `message__status message__status--${message.status}` });
    if (message.status === 'sent') status.textContent = '✓';
    else if (message.status === 'delivered') status.textContent = '✓✓';
    else if (message.status === 'read') status.textContent = '✓✓'; // Usually styled differently (e.g. blue)
    meta.appendChild(status);
  }
  
  li.appendChild(meta);
  return li;
}
