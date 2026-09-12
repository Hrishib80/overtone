import router from './services/router.js';
import store from './services/store.js';
import api from './services/api.js';

import LandingPage from './pages/LandingPage.js';
import { JoinPage, SignInPage } from './pages/AuthPage.js';
import VerifyPage from './pages/VerifyPage.js';
import OnboardingPage from './pages/OnboardingPage.js';
import StatusPage from './pages/StatusPage.js';
import PairPage from './pages/PairPage.js';

async function boot() {
  router.register({
    '/': LandingPage,
    '/join': JoinPage,
    '/signin': SignInPage,
    '/verify': VerifyPage,
    '/onboarding': OnboardingPage,
    '/status': StatusPage,
    '/pairs': PairPage,
  });

  // Resolve the account before the first render, so the router never has to
  // guess where a signed-in visitor belongs.
  if (store.getState().token) {
    try {
      store.setState({ me: await api.getMe() });
    } catch {
      store.signOut();
    }
  }

  router.start();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
