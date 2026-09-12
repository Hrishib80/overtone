export class WebRTCManager {
  constructor() {
    this.pc = null;
    this.localStream = new MediaStream();
    this.remoteStream = new MediaStream();
    this.onRemoteTrackCallback = null;

    const turnUrl = (import.meta.env.VITE_TURN_URL || '').trim();
    const turnUsername = (import.meta.env.VITE_TURN_USERNAME || '').trim();
    const turnCredential = (import.meta.env.VITE_TURN_CREDENTIAL || '').trim();
    const iceServers = [{ urls: 'stun:stun.l.google.com:19302' }];

    if (turnUrl && turnUsername && turnCredential) {
      iceServers.push({
        urls: turnUrl,
        username: turnUsername,
        credential: turnCredential
      });
    }

    this.config = {
      iceServers
    };
  }

  createConnection(signalingWs) {
    this.pc = new RTCPeerConnection(this.config);

    this.pc.onicecandidate = (event) => {
      if (event.candidate) {
        signalingWs.send('ice-candidate', { candidate: event.candidate.toJSON() });
      }
    };

    this.pc.ontrack = (event) => {
      event.streams[0].getTracks().forEach(track => {
        this.remoteStream.addTrack(track);
      });
      if (this.onRemoteTrackCallback) {
        this.onRemoteTrackCallback(this.remoteStream);
      }
    };

    // Add local tracks that already exist
    this.localStream.getTracks().forEach(track => {
      this.pc.addTrack(track, this.localStream);
    });
  }

  async createOffer() {
    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);
    return this.pc.localDescription;
  }

  async handleOffer(sdp) {
    await this.pc.setRemoteDescription(new RTCSessionDescription(sdp));
    const answer = await this.pc.createAnswer();
    await this.pc.setLocalDescription(answer);
    return this.pc.localDescription;
  }

  async handleAnswer(sdp) {
    await this.pc.setRemoteDescription(new RTCSessionDescription(sdp));
  }

  async addIceCandidate(candidate) {
    if (this.pc) {
      await this.pc.addIceCandidate(new RTCIceCandidate(candidate));
    }
  }

  async addLocalAudio() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const track = stream.getAudioTracks()[0];
      this.localStream.addTrack(track);
      if (this.pc) {
        this.pc.addTrack(track, this.localStream);
      }
      return track;
    } catch (err) {
      console.error('Error accessing audio:', err);
      throw err;
    }
  }

  async addLocalVideo() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true });
      const track = stream.getVideoTracks()[0];
      this.localStream.addTrack(track);
      if (this.pc) {
        this.pc.addTrack(track, this.localStream);
      }
      return track;
    } catch (err) {
      console.error('Error accessing video:', err);
      throw err;
    }
  }

  toggleCamera() {
    const videoTrack = this.localStream.getVideoTracks()[0];
    if (videoTrack) {
      videoTrack.enabled = !videoTrack.enabled;
      return videoTrack.enabled;
    }
    return false;
  }

  toggleMic() {
    const audioTrack = this.localStream.getAudioTracks()[0];
    if (audioTrack) {
      audioTrack.enabled = !audioTrack.enabled;
      return audioTrack.enabled;
    }
    return false;
  }

  async flipCamera() {
    // Usually toggles facingMode between user and environment
    const videoTrack = this.localStream.getVideoTracks()[0];
    if (!videoTrack) return;

    const currentSettings = videoTrack.getSettings();
    const facingMode = currentSettings.facingMode === 'user' ? 'environment' : 'user';

    try {
      videoTrack.stop();
      this.localStream.removeTrack(videoTrack);
      
      const newStream = await navigator.mediaDevices.getUserMedia({ 
        video: { facingMode } 
      });
      const newTrack = newStream.getVideoTracks()[0];
      this.localStream.addTrack(newTrack);

      if (this.pc) {
        const sender = this.pc.getSenders().find(s => s.track.kind === 'video');
        if (sender) {
          sender.replaceTrack(newTrack);
        }
      }
    } catch (e) {
      console.error('Error flipping camera:', e);
    }
  }

  onRemoteTrack(callback) {
    this.onRemoteTrackCallback = callback;
  }

  async getStats() {
    if (!this.pc) return null;
    return await this.pc.getStats();
  }

  close() {
    this.localStream.getTracks().forEach(track => track.stop());
    if (this.pc) {
      this.pc.close();
      this.pc = null;
    }
    this.localStream = new MediaStream();
    this.remoteStream = new MediaStream();
  }
}
