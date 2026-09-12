class Store {
  constructor() {
    this.state = {
      currentUser: null,
      token: localStorage.getItem('overtone_token') || null,
      matches: [],
      conversations: [],
      activeCall: null,
      notifications: {
        likes: 0,
        messages: 0
      }
    };
    this.listeners = new Map();
  }

  subscribe(key, callback) {
    if (!this.listeners.has(key)) {
      this.listeners.set(key, new Set());
    }
    this.listeners.get(key).add(callback);
    
    return () => {
      this.listeners.get(key).delete(callback);
    };
  }

  notify(key, value) {
    if (this.listeners.has(key)) {
      this.listeners.get(key).forEach(callback => callback(value));
    }
  }

  getState() {
    return this.state;
  }

  setState(updates) {
    for (const [key, value] of Object.entries(updates)) {
      if (this.state[key] !== value) {
        this.state[key] = value;
        
        if (key === 'token') {
          if (value) {
            localStorage.setItem('overtone_token', value);
          } else {
            localStorage.removeItem('overtone_token');
          }
        }
        
        this.notify(key, value);
      }
    }
  }
}

export default new Store();
