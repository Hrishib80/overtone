import { createElement, escapeHtml } from '../utils/dom.js';
import api from '../services/api.js';
import { createNavbar } from '../components/Navbar.js';
import router from '../services/router.js';

export default {
  render() {
    const page = createElement('div', { className: 'page matches-page' });
    
    const header = createElement('header', { className: 'header' });
    const title = createElement('h1', { className: 'header__title' }, 'Matches');
    header.appendChild(title);
    page.appendChild(header);
    
    const content = createElement('div', { className: 'matches-content' });
    page.appendChild(content);
    
    page.appendChild(createNavbar('/matches'));
    
    const loadMatches = async () => {
      try {
        const { matches } = await api.getMatches();
        
        if (matches.length === 0) {
          content.innerHTML = `
            <div class="empty-state">
              <div class="empty-state__icon">❤️</div>
              <p>No matches yet</p>
            </div>
          `;
          return;
        }
        
        const grid = createElement('div', { className: 'matches-grid' });
        
        matches.forEach(match => {
          const item = createElement('div', { 
            className: `match-item ${match.isNew ? 'match-item--new' : ''}` 
          });
          
          item.onclick = () => {
            router.navigate(`/chat/${match.id}`);
          };
          
          const avatar = createElement('img', { 
            className: 'match-item__avatar',
            src: escapeHtml(match.avatarUrl)
          });
          
          const name = createElement('span', { className: 'match-item__name' }, escapeHtml(match.displayName));
          
          item.appendChild(avatar);
          item.appendChild(name);
          grid.appendChild(item);
        });
        
        content.appendChild(grid);
      } catch (err) {
        content.innerHTML = '<div class="empty-state">Failed to load matches</div>';
      }
    };
    
    loadMatches();
    
    return page;
  }
};
