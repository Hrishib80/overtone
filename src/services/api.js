import store from './store.js';
import router from './router.js';

function getApiBaseUrl() {
  const configuredUrl = (import.meta.env.VITE_API_URL || '').trim().replace(/\/+$/, '');

  // API methods already include /api. This also accepts an accidentally
  // configured /api suffix without producing /api/api/auth/login.
  return configuredUrl.endsWith('/api')
    ? configuredUrl.slice(0, -4)
    : configuredUrl;
}

class Api {
  constructor() {
    this.baseUrl = getApiBaseUrl();
  }

  async request(method, path, body = null) {
    const headers = {
      'Content-Type': 'application/json'
    };

    const token = store.getState().token;
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const options = { method, headers };

    if (body) {
      options.body = JSON.stringify(body);
    }

    try {
      const response = await fetch(`${this.baseUrl}${path}`, options);

      if (response.status === 401) {
        store.setState({ token: null, currentUser: null });
        router.navigate('/auth');
        const err = new Error('Unauthorized');
        err.status = 401;
        throw err;
      }

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const err = new Error(errorData.detail || `API Error: ${response.status}`);
        err.status = response.status;
        throw err;
      }

      return await response.json();
    } catch (error) {
      console.error('API Request failed:', error);
      throw error;
    }
  }

  // ---- Auth (backend prefix: /api/auth) ----
  register(email, password, displayName) {
    return this.request('POST', '/api/auth/register', {
      email,
      password,
      display_name: displayName,
    });
  }

  login(email, password) {
    return this.request('POST', '/api/auth/login', { email, password });
  }

  getMe() {
    return this.request('GET', '/api/auth/me');
  }

  // ---- Matching (backend prefix: /api) ----
  getMatchCandidates(userId, limit = 15) {
    return this.request('POST', '/api/match', {
      user_id: userId,
      limit,
    });
  }

  sendLike(toUserId, targetType, targetId, commentText = null) {
    return this.request('POST', '/api/like', {
      to_user_id: toUserId,
      target_type: targetType,
      target_id: targetId,
      comment_text: commentText,
    });
  }

  getReceivedLikes() {
    return this.request('GET', '/api/likes/received');
  }

  getMatches() {
    return this.request('GET', '/api/matches');
  }

  ingestProfile(profileData) {
    return this.request('POST', '/api/ingest_profile', profileData);
  }

  async uploadMedia(file) {
    const formData = new FormData();
    formData.append('file', file);
    
    const token = store.getState().token;
    const headers = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    
    const res = await fetch(`${this.baseUrl}/api/upload_media`, {
      method: 'POST',
      headers,
      body: formData
    });
    
    if (!res.ok) throw new Error('Failed to upload media');
    return res.json();
  }

  // ---- Chat (backend prefix: /api/chat) ----
  getChatMessages(matchId, page = 1, limit = 50) {
    return this.request('GET', `/api/chat/${matchId}/messages?page=${page}&limit=${limit}`);
  }

  markChatRead(matchId) {
    return this.request('POST', `/api/chat/${matchId}/read`);
  }

  getConversations() {
    return this.request('GET', '/api/chat/conversations');
  }
}

export default new Api();
