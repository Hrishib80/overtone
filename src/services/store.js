const TOKEN_KEY = 'overtone_token';

function readToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    // Private windows and blocked site data throw on access.
    return null;
  }
}

function writeToken(value) {
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* session-only is an acceptable fallback */
  }
}

class Store {
  constructor() {
    this.state = {
      token: readToken(),
      me: null,
      options: null,
    };
    this.listeners = new Map();
  }

  getState() {
    return this.state;
  }

  setState(updates) {
    for (const [key, value] of Object.entries(updates)) {
      if (this.state[key] === value) continue;
      this.state[key] = value;
      if (key === 'token') writeToken(value);
      this.listeners.get(key)?.forEach((cb) => cb(value));
    }
  }

  subscribe(key, callback) {
    if (!this.listeners.has(key)) this.listeners.set(key, new Set());
    this.listeners.get(key).add(callback);
    return () => this.listeners.get(key)?.delete(callback);
  }

  signOut() {
    this.setState({ token: null, me: null });
  }
}

export default new Store();
