import store from './store.js';

function baseUrl() {
  const configured = (import.meta.env.VITE_API_URL || '').trim().replace(/\/+$/, '');
  // Method paths already carry /api; tolerate an accidental /api suffix here.
  return configured.endsWith('/api') ? configured.slice(0, -4) : configured;
}

export class ApiError extends Error {
  constructor(message, { status, code, fields } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.fields = fields || [];
  }
}

class Api {
  constructor() {
    this.base = baseUrl();
  }

  async request(method, path, body) {
    const headers = { 'Content-Type': 'application/json' };
    const token = store.getState().token;
    if (token) headers.Authorization = `Bearer ${token}`;

    let response;
    try {
      response = await fetch(`${this.base}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new ApiError("Can't reach Overtone. Check your connection.", { status: 0 });
    }

    if (response.status === 401) {
      store.setState({ token: null, me: null });
      throw new ApiError('Your session has expired. Sign in again.', { status: 401 });
    }

    const payload = await response.json().catch(() => null);

    if (!response.ok) {
      const error = payload?.error || {};
      throw new ApiError(error.message || `Something went wrong (${response.status}).`, {
        status: response.status,
        code: error.code,
        fields: payload?.fields,
      });
    }

    return payload;
  }

  // ---- auth ----
  register({ email, password, displayName, birthdate }) {
    return this.request('POST', '/api/auth/register', {
      email,
      password,
      display_name: displayName,
      birthdate,
    });
  }

  verifyEmail(token) {
    return this.request('POST', '/api/auth/verify-email', { token });
  }

  login(email, password) {
    return this.request('POST', '/api/auth/login', { email, password });
  }

  getMe() {
    return this.request('GET', '/api/auth/me');
  }

  // ---- profile ----
  getOptions() {
    return this.request('GET', '/api/profile/options');
  }

  getProfile() {
    return this.request('GET', '/api/profile');
  }

  patchProfile(changes) {
    return this.request('PATCH', '/api/profile', changes);
  }

  setPrompts(answers) {
    return this.request('PUT', '/api/profile/prompts', { answers });
  }

  submitProfile() {
    return this.request('POST', '/api/profile/submit');
  }
}

export default new Api();
