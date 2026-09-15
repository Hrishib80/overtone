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
  register({ username, password, displayName, birthdate, inviteCode }) {
    return this.request('POST', '/api/auth/register', {
      username,
      password,
      display_name: displayName,
      birthdate,
      invite_code: inviteCode || null,
    });
  }

  login(username, password) {
    return this.request('POST', '/api/auth/login', { username, password });
  }

  checkUsername(name) {
    return this.request('GET', `/api/auth/username-available?name=${encodeURIComponent(name)}`);
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

  resubmitProfile() {
    return this.request('POST', '/api/profile/resubmit');
  }

  // ---- pairs ----
  getNextPair() {
    return this.request('GET', '/api/pairs/next');
  }

  decidePair(pairingId, chosenId) {
    return this.request('POST', `/api/pairs/${pairingId}/decide`, { chosen_id: chosenId });
  }

  // ---- connections ----
  getInbox() {
    return this.request('GET', '/api/connections');
  }

  /* Just the three numbers on the bar. The bar is on every signed-in screen,
     so this runs on every page load — `getInbox()` serialises a full profile
     for every unlocked person and every admirer to answer it, which is a cost
     with no relationship to three integers. */
  getCounts() {
    return this.request('GET', '/api/connections/counts');
  }

  sendRequest(subjectId, text) {
    return this.request('POST', '/api/connections/requests', { subject_id: subjectId, text });
  }

  getThread(connectionId) {
    return this.request('GET', `/api/connections/${connectionId}/messages`);
  }

  sendMessage(connectionId, text) {
    return this.request('POST', `/api/connections/${connectionId}/messages`, { text });
  }

  declineRequest(connectionId) {
    return this.request('POST', `/api/connections/${connectionId}/decline`);
  }

  markRead(connectionId) {
    return this.request('POST', `/api/connections/${connectionId}/read`);
  }

  // ---- account ----
  getInvite() {
    return this.request('GET', '/api/account/invite');
  }

  getConsent() {
    return this.request('GET', '/api/account/consent');
  }

  giveConsent() {
    return this.request('POST', '/api/account/consent');
  }

  withdrawConsent() {
    return this.request('DELETE', '/api/account/consent');
  }

  deleteAccount(password) {
    return this.request('POST', '/api/account/delete', { password });
  }

  // ---- safety ----
  getReportReasons() {
    return this.request('GET', '/api/safety/reasons');
  }

  blockUser(userId) {
    return this.request('POST', '/api/safety/blocks', { user_id: userId });
  }

  unblockUser(userId) {
    return this.request('DELETE', `/api/safety/blocks/${userId}`);
  }

  getBlocks() {
    return this.request('GET', '/api/safety/blocks');
  }

  reportUser(userId, { reason, note, context, mediaId, promptId, block = true }) {
    return this.request('POST', '/api/safety/reports', {
      user_id: userId,
      reason,
      note: note || null,
      context: context || null,
      media_id: mediaId || null,
      prompt_id: promptId || null,
      block,
    });
  }

  // ---- moderation (reviewers only; the API refuses everyone else) ----
  getReviewQueue() {
    return this.request('GET', '/api/moderation/queue');
  }

  getReviewSubject(userId) {
    return this.request('GET', `/api/moderation/subjects/${userId}`);
  }

  decideReport(userId, { action, note, mediaId }) {
    return this.request('POST', `/api/moderation/subjects/${userId}/decide`, {
      action,
      note,
      media_id: mediaId || null,
    });
  }

  reinstate(userId) {
    return this.request('POST', `/api/moderation/subjects/${userId}/reinstate`);
  }

  // ---- admin (admins only; the API answers 404 to everyone else) ----
  getWaitlist() {
    return this.request('GET', '/api/admin/waitlist');
  }

  getInvited() {
    return this.request('GET', '/api/admin/invited');
  }

  approveApplicant(userId) {
    return this.request('POST', `/api/admin/applicants/${userId}/approve`);
  }

  sendBackApplicant(userId, note) {
    return this.request('POST', `/api/admin/applicants/${userId}/send-back`, { note });
  }
}

export default new Api();
