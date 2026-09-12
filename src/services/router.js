import store from './store.js';

class Router {
  constructor() {
    this.routes = {};
    this.currentPath = null;
    this.activePage = null;
  }

  registerRoutes(routesMap) {
    this.routes = routesMap;
  }

  init() {
    window.addEventListener('popstate', () => {
      this.handleRoute(window.location.pathname);
    });
    this.handleRoute(window.location.pathname);
  }

  navigate(path) {
    if (this.currentPath === path) return;
    window.history.pushState(null, '', path);
    this.handleRoute(path);
  }

  matchRoute(path) {
    for (const route in this.routes) {
      if (route === path) return { component: this.routes[route], params: {} };
      
      const routeParts = route.split('/');
      const pathParts = path.split('/');
      
      if (routeParts.length === pathParts.length) {
        let match = true;
        const params = {};
        
        for (let i = 0; i < routeParts.length; i++) {
          if (routeParts[i].startsWith(':')) {
            params[routeParts[i].slice(1)] = pathParts[i];
          } else if (routeParts[i] !== pathParts[i]) {
            match = false;
            break;
          }
        }
        
        if (match) {
          return { component: this.routes[route], params };
        }
      }
    }
    return null;
  }

  async handleRoute(path) {
    const token = store.getState().token || localStorage.getItem('overtone_token');
    
    // Auth Guard
    if (!token && path !== '/auth') {
      this.navigate('/auth');
      return;
    }
    if (token && path === '/auth') {
      this.navigate('/');
      return;
    }

    const matched = this.matchRoute(path);
    if (!matched) {
      console.warn('Route not found:', path);
      this.navigate('/');
      return;
    }

    this.currentPath = path;
    const { component, params } = matched;
    
    const renderPage = async () => {
      const appRoot = document.getElementById('app');
      
      if (this.activePage && typeof this.activePage.destroy === 'function') {
        this.activePage.destroy();
      }
      
      appRoot.innerHTML = '';
      
      const pageElement = await component.render(params);
      this.activePage = pageElement;
      
      appRoot.appendChild(pageElement);
      
      // Setup lifecycle hooks if needed
      if (component.onMount) {
        component.onMount();
      }
    };

    if (document.startViewTransition) {
      document.startViewTransition(() => renderPage());
    } else {
      await renderPage();
    }
  }
}

export default new Router();
