import router from './services/router.js';
import store from './services/store.js';
import api from './services/api.js';

import LandingPage from './pages/LandingPage.js';
import { JoinPage, SignInPage } from './pages/AuthPage.js';
import VerifyPage from './pages/VerifyPage.js';
import OnboardingPage from './pages/OnboardingPage.js';
import PairPage from './pages/PairPage.js';
import InboxPage from './pages/InboxPage.js';
import SettingsPage from './pages/SettingsPage.js';
import ReviewPage from './pages/ReviewPage.js';
import SuspendedPage from './pages/SuspendedPage.js';

async function boot() {
  router.register({
    '/': LandingPage,
    '/join': JoinPage,
    '/signin': SignInPage,
    '/verify': VerifyPage,
    '/onboarding': OnboardingPage,
    '/pairs': PairPage,
    '/inbox': InboxPage,
    '/settings': SettingsPage,
    '/review': ReviewPage,
    '/suspended': SuspendedPage,
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
