import { WebRTCManager } from './webrtc.js';
import store from './store.js';

class CallManager {
  constructor() {
    this.state = {
      active: false,
      type: 'voice',
      peerId: null,
      peerName: '',
      peerAvatarUrl: '',
      isMuted: false,
      isCameraOn: false,
      isIncoming: false,
      isConnecting: false,
      callDuration: 0,
      videoConsent: { local: false, remote: false }
    };

    this.signalingWs = null;
    this.rtc = null;
    this.timer = null;
    this.listeners = new Set();
    this.videoRequestListeners = new Set();
    this.signalingBound = false;
  }

  notify() {
    this.listeners.forEach(cb => cb({ ...this.state }));
  }

  onStateChange(callback) {
    this.listeners.add(callback);
    return () => this.listeners.delete(callback);
  }

  onVideoRequest(callback) {
    this.videoRequestListeners.add(callback);
    return () => this.videoRequestListeners.delete(callback);
  }

  bindSignaling(ws) {
    if (this.signalingBound && this.signalingWs === ws) return;
    this.signalingWs = ws;
    this.signalingBound = true;

    ws.on('call-request', (data) => this.handleIncomingCall(data));
    ws.on('call-accept', () => this.handleCallAccepted());
    ws.on('call-reject', () => this.endCall());
    ws.on('call-end', () => this.endCall());

    ws.on('sdp-offer', async (data) => {
      if (!this.rtc?.pc) return;
      const answer = await this.rtc.handleOffer(data.sdp);
      this.signalingWs.send('sdp-answer', {
        target_user_id: this.state.peerId,
        sdp: answer
      });
      this.state.isConnecting = false;
      this.startTimer();
      this.notify();
    });

    ws.on('sdp-answer', async (data) => {
      if (!this.rtc?.pc) return;
      await this.rtc.handleAnswer(data.sdp);
      this.state.isConnecting = false;
      this.notify();
    });

    ws.on('ice-candidate', async (data) => {
      if (data.candidate && this.rtc) {
        await this.rtc.addIceCandidate(data.candidate);
      }
    });

    ws.on('video-request', () => {
      this.videoRequestListeners.forEach(cb => cb(this.state.peerId));
    });

    ws.on('video-accept', async () => {
      this.state.videoConsent.remote = true;
      if (this.state.videoConsent.local && !this.state.isCameraOn && this.rtc) {
        await this.rtc.addLocalVideo();
        this.state.isCameraOn = true;
      }
      this.notify();
    });

    ws.on('video-reject', () => {
      this.state.videoConsent.local = false;
      this.notify();
    });
  }

  async initiateCall({ peerId, type = 'voice', ws, peerName = '', peerAvatarUrl = '' }) {
    if (this.state.active) return;

    this.bindSignaling(ws);
    this.state = {
      ...this.state,
      active: true,
      type,
      peerId,
      peerName,
      peerAvatarUrl,
      isIncoming: false,
      isConnecting: true,
      isMuted: false,
      isCameraOn: false,
      callDuration: 0,
      videoConsent: { local: type === 'video', remote: false }
    };

    this.rtc = new WebRTCManager();
    try {
      await this.rtc.addLocalAudio();
      if (type === 'video') {
        await this.rtc.addLocalVideo();
        this.state.isCameraOn = true;
      }
    } catch (e) {
      console.error('Media access denied', e);
      this.endCall();
      return;
    }

    this.rtc.createConnection(ws);
    ws.send('call-request', {
      target_user_id: peerId,
      callType: type,
      peerName: store.getState().currentUser?.display_name || 'Someone',
      peerAvatarUrl: store.getState().currentUser?.avatar_url || ''
    });
    this.notify();
  }

  handleIncomingCall(data) {
    const myId = store.getState().currentUser?.id;
    if (!myId || data.from_user_id === myId) return;
    if (this.state.active) {
      this.signalingWs?.send('call-reject', { target_user_id: data.from_user_id });
      return;
    }

    this.state = {
      active: true,
      type: data.callType || 'voice',
      peerId: data.from_user_id,
      peerName: data.peerName || 'Incoming call',
      peerAvatarUrl: data.peerAvatarUrl || '',
      isMuted: false,
      isCameraOn: false,
      isIncoming: true,
      isConnecting: false,
      callDuration: 0,
      videoConsent: { local: false, remote: false }
    };
    this.notify();
  }

  async acceptCall() {
    if (!this.state.isIncoming || !this.signalingWs) return;

    this.state.isIncoming = false;
    this.state.isConnecting = true;

    this.rtc = new WebRTCManager();
    try {
      await this.rtc.addLocalAudio();
      if (this.state.type === 'video') {
        await this.rtc.addLocalVideo();
        this.state.isCameraOn = true;
        this.state.videoConsent.local = true;
      }
    } catch (e) {
      console.error('Media access denied', e);
      this.rejectCall(this.state.peerId);
      return;
    }

    this.rtc.createConnection(this.signalingWs);
    this.signalingWs.send('call-accept', { target_user_id: this.state.peerId });
    this.notify();
  }

  async handleCallAccepted() {
    if (this.state.isIncoming || !this.rtc) return;

    this.state.isConnecting = false;
    const offer = await this.rtc.createOffer();
    this.signalingWs.send('sdp-offer', {
      target_user_id: this.state.peerId,
      sdp: offer
    });
    this.startTimer();
    this.notify();
  }

  rejectCall() {
    if (this.signalingWs && this.state.peerId) {
      this.signalingWs.send('call-reject', { target_user_id: this.state.peerId });
    }
    this.endCall();
  }

  endCall() {
    if (this.signalingWs && this.state.peerId) {
      this.signalingWs.send('call-end', { target_user_id: this.state.peerId });
    }
    if (this.rtc) {
      this.rtc.close();
      this.rtc = null;
    }
    this.stopTimer();

    this.state = {
      active: false,
      type: 'voice',
      peerId: null,
      peerName: '',
      peerAvatarUrl: '',
      isMuted: false,
      isCameraOn: false,
      isIncoming: false,
      isConnecting: false,
      callDuration: 0,
      videoConsent: { local: false, remote: false }
    };
    this.notify();
  }

  toggleMic() {
    if (this.rtc) {
      const enabled = this.rtc.toggleMic();
      this.state.isMuted = !enabled;
      this.notify();
    }
  }

  async toggleCamera() {
    if (!this.rtc) return;
    if (!this.state.isCameraOn && !this.state.videoConsent.remote) {
      this.requestVideo();
      return;
    }
    this.state.isCameraOn = this.rtc.toggleCamera();
    this.notify();
  }

  requestVideo() {
    this.state.videoConsent.local = true;
    this.signalingWs?.send('video-request', { target_user_id: this.state.peerId });
    this.notify();
  }

  async acceptVideo() {
    this.state.videoConsent.remote = true;
    this.state.videoConsent.local = true;
    if (this.rtc) {
      await this.rtc.addLocalVideo();
      this.state.isCameraOn = true;
      this.state.type = 'video';
    }
    this.signalingWs?.send('video-accept', { target_user_id: this.state.peerId });
    this.notify();
  }

  rejectVideo() {
    this.state.videoConsent.local = false;
    this.state.videoConsent.remote = false;
    this.signalingWs?.send('video-reject', { target_user_id: this.state.peerId });
    this.notify();
  }

  startTimer() {
    this.stopTimer();
    this.timer = setInterval(() => {
      this.state.callDuration += 1;
      this.notify();
    }, 1000);
  }

  stopTimer() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  getFormattedDuration() {
    const m = Math.floor(this.state.callDuration / 60).toString().padStart(2, '0');
    const s = (this.state.callDuration % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  }
}

export default new CallManager();
