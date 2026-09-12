import store from './store.js';

export class WebSocketClient {
  constructor() {
    this.ws = null;
    this.roomId = null;
    this.handlers = new Map();
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
    this.baseDelay = 1000;
    this.pingInterval = null;
    this.state = 'disconnected'; // connecting, connected, disconnected, reconnecting
  }

  connect(roomId) {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    this.roomId = roomId;
    this.state = 'connecting';
    const token = store.getState().token;
    
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const configuredUrl = (import.meta.env.VITE_WS_URL || '').trim().replace(/\/+$/, '');
    const host = configuredUrl || `${wsProtocol}//${window.location.host}/ws`;
    const url = `${host}/signal/${encodeURIComponent(roomId)}?token=${encodeURIComponent(token || '')}`;

    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.state = 'connected';
      this.reconnectAttempts = 0;
      this.startHeartbeat();
      console.log(`WebSocket connected to room: ${roomId}`);
      this.dispatch('connection_state', { state: 'connected' });
    };

    this.ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'pong') return; // Ignore heartbeat responses
        
        this.dispatch(message.type, message.data || message);
      } catch (e) {
        console.error('Failed to parse WS message:', e);
      }
    };

    this.ws.onclose = (event) => {
      this.stopHeartbeat();
      if (this.state !== 'disconnected') {
        this.state = 'reconnecting';
        this.dispatch('connection_state', { state: 'reconnecting' });
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = (error) => {
      console.error('WebSocket Error:', error);
    };
  }

  scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this.state = 'disconnected';
      this.dispatch('connection_state', { state: 'disconnected', error: 'Max retries reached' });
      return;
    }

    const delay = Math.min(this.baseDelay * Math.pow(2, this.reconnectAttempts), 30000);
    this.reconnectAttempts++;
    
    setTimeout(() => {
      console.log(`Reconnecting... attempt ${this.reconnectAttempts}`);
      this.connect(this.roomId);
    }, delay);
  }

  startHeartbeat() {
    this.pingInterval = setInterval(() => {
      this.send('ping', { timestamp: Date.now() });
    }, 30000);
  }

  stopHeartbeat() {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  send(type, data = {}) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type, ...data }));
    } else {
      console.warn('WebSocket is not open, cannot send message:', type);
    }
  }

  on(type, callback) {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set());
    }
    this.handlers.get(type).add(callback);
  }

  off(type, callback) {
    if (this.handlers.has(type)) {
      this.handlers.get(type).delete(callback);
    }
  }

  dispatch(type, data) {
    if (this.handlers.has(type)) {
      this.handlers.get(type).forEach(callback => callback(data));
    }
  }

  disconnect() {
    this.state = 'disconnected';
    this.stopHeartbeat();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.dispatch('connection_state', { state: 'disconnected' });
  }
}
