import router from './services/router.js';
import store from './services/store.js';
import api from './services/api.js';

// Pages
import AuthPage from './pages/AuthPage.js';
import ProfileSetupPage from './pages/ProfileSetupPage.js';
import DiscoverPage from './pages/DiscoverPage.js';
import MatchesPage from './pages/MatchesPage.js';
import ChatListPage from './pages/ChatListPage.js';
import ChatPage from './pages/ChatPage.js';
import ProfilePage from './pages/ProfilePage.js';

document.addEventListener('DOMContentLoaded', async () => {
  // Register routes
  router.registerRoutes({
    '/auth': AuthPage,
    '/setup': ProfileSetupPage,
    '/': DiscoverPage,
    '/discover': DiscoverPage,
    '/matches': MatchesPage,
    '/chat': ChatListPage,
    '/chat/:id': ChatPage,
    '/profile': ProfilePage,
  });

  // Intercept data-link clicks for SPA navigation
  document.addEventListener('click', (e) => {
    const link = e.target.closest('[data-link]');
    if (link) {
      e.preventDefault();
      const href = link.getAttribute('href');
      if (href) router.navigate(href);
    }
  });

  // Check auth and initialize
  const token = store.getState().token;
  if (token) {
    try {
      const user = await api.getMe();
      store.setState({ currentUser: user });
    } catch (err) {
      store.setState({ token: null, currentUser: null });
    }
  }

  // Global Call Overlay logic
  let activeOverlay = null;
  let consentModal = null;
  import('./services/callManager.js').then(module => {
    const callManager = module.default;
    Promise.all([
      import('./components/CallOverlay.js'),
      import('./components/VideoConsentModal.js')
    ]).then(([overlayModule, consentModule]) => {
      callManager.onVideoRequest(() => {
        if (!consentModal) {
          consentModal = consentModule.createVideoConsentModal(activeOverlay ? 'Peer' : 'Someone', {
            onAccept: () => {
              callManager.acceptVideo();
              consentModal.destroy();
              consentModal = null;
            },
            onDecline: () => {
              callManager.rejectVideo();
              consentModal.destroy();
              consentModal = null;
            }
          });
          document.getElementById('modal-root').appendChild(consentModal);
        }
      });

      callManager.onStateChange((state) => {
        if (state.active && !activeOverlay) {
          activeOverlay = overlayModule.createCallOverlay(
            {
              ...state,
              peerName: state.peerName || 'Calling...',
              peerAvatarUrl: state.peerAvatarUrl || ''
            },
            {
              onToggleMic: () => callManager.toggleMic(),
              onToggleCamera: () => callManager.toggleCamera(),
              onFlipCamera: () => callManager.rtc?.flipCamera && callManager.rtc.flipCamera(),
              onEndCall: () => callManager.endCall(),
              onRequestVideo: () => callManager.requestVideo(),
              onAcceptCall: () => callManager.acceptCall(),
              onDeclineCall: () => callManager.rejectCall(),
            }
          );
          document.body.appendChild(activeOverlay);
        } else if (state.active && activeOverlay) {
          activeOverlay.update(state);
          if (callManager.rtc) {
            activeOverlay.setLocalStream(callManager.rtc.localStream);
            activeOverlay.setRemoteStream(callManager.rtc.remoteStream);
          }
        } else if (!state.active && activeOverlay) {
          activeOverlay.destroy();
          activeOverlay = null;
          if (consentModal) {
            consentModal.destroy();
            consentModal = null;
          }
        }
      });
    });
  });

  router.init();
});
