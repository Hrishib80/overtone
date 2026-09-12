import { createElement } from '../utils/dom.js';

export function createTypingIndicator() {
  const li = createElement('li', { className: 'message message--received typing-indicator-container' });
  
  const indicator = createElement('div', { className: 'typing-indicator' });
  
  const dot1 = createElement('div', { className: 'typing-indicator__dot' });
  const dot2 = createElement('div', { className: 'typing-indicator__dot' });
  const dot3 = createElement('div', { className: 'typing-indicator__dot' });
  
  indicator.appendChild(dot1);
  indicator.appendChild(dot2);
  indicator.appendChild(dot3);
  
  li.appendChild(indicator);
  return li;
}
