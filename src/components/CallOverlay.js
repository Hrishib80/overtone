import { createElement, escapeHtml } from '../utils/dom.js';

const icons = {
  mic: '<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5zm6 6c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>',
  micOff: '<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M19 11h-1.7c0 .74-.16 1.43-.43 2.05l1.23 1.23c.56-.98.9-2.09.9-3.28zm-4.02.17c0-.06.02-.11.02-.17V5c0-1.66-1.34-3-3-3S9 3.34 9 5v.18l5.98 5.99zM4.27 3L3 4.27l6.01 6.01V11c0 1.66 1.33 3 2.99 3 .22 0 .44-.03.65-.08l1.66 1.66c-.71.33-1.5.52-2.31.52-2.76 0-5.3-2.1-5.3-5.1H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c.91-.13 1.77-.45 2.54-.9L19.73 21 21 19.73 4.27 3z"/></svg>',
  camera: '<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/></svg>',
  cameraOff: '<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M21 6.5l-4 4V7c0-.55-.45-1-1-1H9.82L21 17.18V6.5zM3.27 2L2 3.27 4.73 6H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.21 0 .39-.08.54-.18L19.73 21 21 19.73 3.27 2z"/></svg>',
  flipCamera: '<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M9 12c0 1.66 1.34 3 3 3s3-1.34 3-3-1.34-3-3-3-3 1.34-3 3zm13-2V6c0-1.1-.9-2-2-2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2v-4l-2-2zm-2 8H4V6h16v12z"/></svg>',
  endCall: '<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M6.62 10.79c1.44 2.83 3.76 5.15 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z"/></svg>' // Using phone icon
};

function formatDuration(seconds) {
  const m = Math.floor(seconds / 60).toString().padStart(2, '0');
  const s = (seconds % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

export function createCallOverlay(callState, { onToggleMic, onToggleCamera, onFlipCamera, onEndCall, onRequestVideo, onAcceptCall, onDeclineCall }) {
  const overlay = createElement('div', { className: 'call-screen' });
  
  let remoteVideo, localVideo;

  const render = (state) => {
    overlay.innerHTML = '';
    
    if (state.type === 'video') {
      remoteVideo = createElement('video', { className: 'call-screen__remote', autoplay: true, playsinline: true });
      localVideo = createElement('video', { className: 'call-screen__local', autoplay: true, playsinline: true, muted: true });
      overlay.appendChild(remoteVideo);
      overlay.appendChild(localVideo);
    } else {
      const voiceContainer = createElement('div', { className: 'call-screen__voice-container' });
      const avatar = createElement('img', { 
        className: 'call-screen__avatar' + (state.isConnecting || state.isIncoming ? ' pulsing' : ''),
        src: escapeHtml(state.peerAvatarUrl)
      });
      const name = createElement('h2', { className: 'call-screen__name' }, escapeHtml(state.peerName));
      
      let statusText = '';
      if (state.isIncoming) statusText = 'Incoming call...';
      else if (state.isConnecting) statusText = 'Connecting...';
      else statusText = formatDuration(state.callDuration || 0);
      
      const status = createElement('div', { className: 'call-screen__status' }, statusText);
      
      voiceContainer.appendChild(avatar);
      voiceContainer.appendChild(name);
      voiceContainer.appendChild(status);
      overlay.appendChild(voiceContainer);
    }
    
    if (state.isIncoming) {
      const incomingControls = createElement('div', { className: 'call-screen__incoming-controls' });
      
      const declineBtn = createElement('button', { className: 'call-btn call-btn--decline muted' });
      declineBtn.innerHTML = icons.endCall;
      declineBtn.onclick = onDeclineCall || onEndCall;
      
      const acceptBtn = createElement('button', { className: 'call-btn call-btn--accept' });
      acceptBtn.innerHTML = icons.endCall; // Use CSS to rotate
      acceptBtn.onclick = onAcceptCall;
      
      incomingControls.appendChild(declineBtn);
      incomingControls.appendChild(acceptBtn);
      overlay.appendChild(incomingControls);
    } else {
      const controls = createElement('div', { className: 'call-screen__controls' });
      
      if (state.type === 'video' && state.isCameraOn) {
        const flipBtn = createElement('button', { className: 'call-btn' });
        flipBtn.innerHTML = icons.flipCamera;
        flipBtn.onclick = onFlipCamera;
        controls.appendChild(flipBtn);
      }
      
      const camBtn = createElement('button', { className: `call-btn ${!state.isCameraOn ? 'muted' : ''}` });
      camBtn.innerHTML = state.isCameraOn ? icons.camera : icons.cameraOff;
      camBtn.onclick = onToggleCamera;
      controls.appendChild(camBtn);
      
      const micBtn = createElement('button', { className: `call-btn ${state.isMuted ? 'muted' : ''}` });
      micBtn.innerHTML = state.isMuted ? icons.micOff : icons.mic;
      micBtn.onclick = onToggleMic;
      controls.appendChild(micBtn);
      
      const endBtn = createElement('button', { className: 'call-btn call-btn--end muted' });
      endBtn.innerHTML = icons.endCall;
      endBtn.onclick = onEndCall;
      controls.appendChild(endBtn);
      
      overlay.appendChild(controls);
    }
  };

  render(callState);

  overlay.update = (newState) => {
    Object.assign(callState, newState);
    render(callState);
  };
  
  overlay.setLocalStream = (stream) => {
    if (localVideo) localVideo.srcObject = stream;
  };
  
  overlay.setRemoteStream = (stream) => {
    if (remoteVideo) remoteVideo.srcObject = stream;
  };
  
  overlay.destroy = () => {
    overlay.remove();
  };
  
  return overlay;
}
