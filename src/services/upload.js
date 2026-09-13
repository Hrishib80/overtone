import api, { ApiError } from './api.js';

/* Three steps, and the bytes never touch our API:
   ask for a signed URL, PUT straight to storage, then confirm so the
   server can verify the upload landed and queue the work. */

export async function uploadFile(file, { kind, durationMs = null, replaces = null, onProgress } = {}) {
  // `replaces` travels on both calls: the first so the photo cap lets a
  // swap through when all the slots are full, the second so the new photo
  // takes the old one's position and the old one goes in the same step.
  const ticket = await api.request('POST', '/api/media/upload-url', {
    kind,
    content_type: file.type,
    byte_size: file.size,
    replaces,
  });

  onProgress?.('uploading');

  let response;
  try {
    response = await fetch(ticket.upload_url, {
      method: 'PUT',
      headers: { 'Content-Type': file.type },
      body: file,
    });
  } catch {
    throw new ApiError('The upload was interrupted. Check your connection and try again.');
  }

  if (!response.ok) {
    throw new ApiError("That upload didn't go through. Try again.", { status: response.status });
  }

  onProgress?.('confirming');

  const confirmed = await api.request('POST', `/api/media/${ticket.asset_id}/confirm`, {
    duration_ms: durationMs,
    replaces,
  });

  return { assetId: ticket.asset_id, ...confirmed };
}

/** Human wording for the quality gate's machine reasons. */
export const GATE_REASONS = {
  no_face: "We couldn't find a face in that photo.",
  multiple_faces: 'That photo has more than one person in it.',
  face_too_small: 'Your face is too small in that photo — try a closer shot.',
  low_confidence: "That photo isn't clear enough. Try better light.",
  unreadable_image: "We couldn't read that file.",
  consent_withdrawn: 'You withdrew permission to read faces while this was uploading.',
  too_large: 'That file is too large.',
};

export function gateMessage(reason) {
  return GATE_REASONS[reason] || 'That photo was rejected.';
}
