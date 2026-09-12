import { createElement, escapeHtml } from '../utils/dom.js';
import api from '../services/api.js';
import store from '../services/store.js';
import router from '../services/router.js';
import { createNavbar } from '../components/Navbar.js';

export default {
  render() {
    const page = createElement('div', { className: 'page profile-page' });
    
    const header = createElement('header', { className: 'header' });
    const title = createElement('h1', { className: 'header__title' }, 'Profile');
    header.appendChild(title);
    page.appendChild(header);
    
    const content = createElement('div', { className: 'profile-content' });
    page.appendChild(content);
    
    page.appendChild(createNavbar('/profile'));
    
    const loadProfile = async () => {
      try {
        const user = await api.getMe();
        
        const infoSection = createElement('div', { className: 'profile-header' });
        const avatar = createElement('img', { 
          className: 'profile-avatar',
          src: escapeHtml(user.avatar_url || '')
        });
        const name = createElement('h2', { className: 'profile-name' }, escapeHtml(user.display_name || 'User'));
        const email = createElement('p', { className: 'profile-email' }, escapeHtml(user.email || ''));
        
        infoSection.appendChild(avatar);
        infoSection.appendChild(name);
        infoSection.appendChild(email);
        content.appendChild(infoSection);
        
        if (user.prompts && user.prompts.length > 0) {
          const promptsSection = createElement('section', { className: 'profile-section' });
          const promptsTitle = createElement('h3', { className: 'profile-section__title' }, 'Your Prompts');
          promptsSection.appendChild(promptsTitle);
          
          user.prompts.forEach(p => {
            const card = createElement('div', { className: 'profile-prompt' });
            const q = createElement('p', { className: 'profile-prompt__question' }, escapeHtml(p.question));
            const a = createElement('p', { className: 'profile-prompt__answer' }, escapeHtml(p.answer));
            card.appendChild(q);
            card.appendChild(a);
            promptsSection.appendChild(card);
          });
          content.appendChild(promptsSection);
        }
        
        if (user.photos && user.photos.length > 0) {
          const photosSection = createElement('section', { className: 'profile-section' });
          const photosTitle = createElement('h3', { className: 'profile-section__title' }, 'Your Photos');
          photosSection.appendChild(photosTitle);
          
          const grid = createElement('div', { className: 'profile-photos' });
          user.photos.forEach(url => {
            const img = createElement('img', { className: 'profile-photo', src: escapeHtml(url) });
            grid.appendChild(img);
          });
          photosSection.appendChild(grid);
          content.appendChild(photosSection);
        }
        
        const actionsSection = createElement('div', { className: 'profile-actions' });
        
        const editBtn = createElement('button', { className: 'btn btn--secondary' }, 'Edit Profile');
        editBtn.onclick = () => router.navigate('/setup');
        
        const logoutBtn = createElement('button', { className: 'btn btn--primary profile-btn--logout' }, 'Log Out');
        logoutBtn.onclick = () => {
          store.setState({ token: null, currentUser: null });
          router.navigate('/auth');
        };
        
        actionsSection.appendChild(editBtn);
        actionsSection.appendChild(logoutBtn);
        content.appendChild(actionsSection);
        
      } catch (err) {
        content.innerHTML = '<div class="empty-state">Failed to load profile</div>';
      }
    };
    
    loadProfile();
    
    return page;
  }
};
