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

  router.init();
});
