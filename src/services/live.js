import store from './store.js';

/* The chat socket, from the browser's side.

   It only ever *listens* and sends typing. Messages go over HTTP, because
   that is where the rules are — the one-message limit on an unanswered
   request, blocking, rate limits — and a second write path would be a second
   place for those to not apply. The server publishes what it committed; this
   receives it.

   That also makes reconnection cheap to reason about: nothing is lost by a
   dropped connection, because everything on it is already saved and the
   thread refetches on open. A socket that never connects at all degrades to
   exactly the behaviour before it existed.

   Reconnects with backoff, and stops trying after a while rather than
   hammering a server that is plainly down. */

const MAX_ATTEMPTS = 6;
const BASE_DELAY = 700;
const PING_EVERY = 25_000;

function socketUrl(roomId, token) {
  const configured = (import.meta.env.VITE_API_URL || '').trim().replace(/\/+$/, '');
  const base = configured || window.location.origin;
  const url = new URL(`/ws/signal/${roomId}`, base);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('token', token);
  return url.toString();
}

/**
 * @param {string} roomId  the connection id
 * @param {object} handlers
 * @param {(message: object) => void} [handlers.onMessage]
 * @param {() => void} [handlers.onRead]        the peer read what you sent
 * @param {(on: boolean) => void} [handlers.onTyping]
 * @returns {{ typing: () => void, close: () => void }}
 */
export function joinThread(roomId, { onMessage, onRead, onTyping } = {}) {
  const token = store.getState().token;
  let socket = null;
  let attempts = 0;
  let closed = false;
  let pinger = null;
  let typingTimer = null;
  let reconnectTimer = null;

  function connect() {
    if (closed || !token) return;
    try {
      socket = new WebSocket(socketUrl(roomId, token));
    } catch {
      return; // a URL we cannot build is not a URL worth retrying
    }

    socket.addEventListener('open', () => {
      attempts = 0;
      pinger = setInterval(() => {
        if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ping' }));
      }, PING_EVERY);
    });

    socket.addEventListener('message', (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      if (data.type === 'chat-message' && data.message) {
        // Everything arriving here is from the other person — the server
        // filters the sender's own echo — so `mine` is false by definition.
        onMessage?.({ ...data.message, mine: false });
      } else if (data.type === 'read-receipt') {
        onRead?.();
      } else if (data.type === 'typing') {
        onTyping?.(true);
        clearTimeout(typingTimer);
        // Nothing sends "stopped typing"; it expires instead, so a peer who
        // closes the tab mid-word does not leave the dots up for ever.
        typingTimer = setTimeout(() => onTyping?.(false), 3000);
      } else if (data.type === 'peer-left') {
        onTyping?.(false);
      }
    });

    socket.addEventListener('close', () => {
      clearInterval(pinger);
      if (closed || attempts >= MAX_ATTEMPTS) return;
      attempts += 1;
      reconnectTimer = setTimeout(connect, BASE_DELAY * 2 ** (attempts - 1));
    });

    // 'error' is always followed by 'close', which already handles retrying.
    socket.addEventListener('error', () => {});
  }

  connect();

  let lastTyping = 0;
  return {
    typing() {
      // Throttled: one per second is enough to keep the dots alive, and a
      // frame per keystroke is a lot of traffic for a decoration.
      const now = Date.now();
      if (now - lastTyping < 1000) return;
      lastTyping = now;
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'typing' }));
      }
    },
    close() {
      closed = true;
      clearInterval(pinger);
      clearTimeout(typingTimer);
      clearTimeout(reconnectTimer);
      socket?.close();
    },
  };
}
