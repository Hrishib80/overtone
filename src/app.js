import router from './services/router.js';
import store from './services/store.js';
import api from './services/api.js';

import LandingPage from './pages/LandingPage.js';
import { JoinPage, SignInPage } from './pages/AuthPage.js';
import OnboardingPage from './pages/OnboardingPage.js';
import PairPage from './pages/PairPage.js';
import MessagesPage from './pages/MessagesPage.js';
import TypePage from './pages/TypePage.js';
import ChosenPage from './pages/ChosenPage.js';
import ProfilePage from './pages/ProfilePage.js';
import SettingsPage from './pages/SettingsPage.js';
import ReviewPage from './pages/ReviewPage.js';
import SuspendedPage from './pages/SuspendedPage.js';
import WaitlistPage from './pages/WaitlistPage.js';
import AdminPage from './pages/AdminPage.js';
import { ADMIN_PATH } from './services/paths.js';

async function boot() {
  router.register({
    '/': LandingPage,
    '/join': JoinPage,
    '/signin': SignInPage,
    '/onboarding': OnboardingPage,
    '/pairs': PairPage,
    '/messages': MessagesPage,
    '/type': TypePage,
    '/chosen': ChosenPage,
    '/profile': ProfilePage,
    '/settings': SettingsPage,
    '/review': ReviewPage,
    '/suspended': SuspendedPage,
    '/waitlist': WaitlistPage,
    [ADMIN_PATH]: AdminPage,
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
