export function toast(message, { error = false, ms = 4000 } = {}) {
  const root = document.getElementById('toast-root');
  if (!root) return;

  const el = document.createElement('div');
  el.className = error ? 'toast toast--error' : 'toast';
  el.setAttribute('role', error ? 'alert' : 'status');
  el.textContent = message;
  root.append(el);

  setTimeout(() => el.remove(), ms);
}
