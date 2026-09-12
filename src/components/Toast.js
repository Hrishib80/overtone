import { createElement } from '../utils/dom.js';

const icons = {
  success: '✓',
  error: '✕',
  info: 'ℹ'
};

export function showToast(message, type = 'info', duration = 3000) {
  let toastRoot = document.getElementById('toast-root');
  if (!toastRoot) {
    toastRoot = createElement('div', { id: 'toast-root', className: 'toast-root' });
    document.body.appendChild(toastRoot);
  }

  const toast = createElement('div', { className: `toast toast--${type}` });
  
  const icon = createElement('span', { className: 'toast__icon' }, icons[type] || icons.info);
  const text = createElement('span', { className: 'toast__text' }, message);
  
  toast.appendChild(icon);
  toast.appendChild(text);
  
  const existingToasts = toastRoot.querySelectorAll('.toast:not(.dismissing)');
  if (existingToasts.length > 0) {
    toast.style.marginTop = `${existingToasts.length * 60}px`;
  }
  
  toastRoot.appendChild(toast);
  
  setTimeout(() => {
    toast.classList.add('dismissing');
    setTimeout(() => {
      toast.remove();
      const remaining = toastRoot.querySelectorAll('.toast:not(.dismissing)');
      remaining.forEach((t, i) => {
        t.style.marginTop = `${i * 60}px`;
      });
    }, 300);
  }, duration - 300);
}
