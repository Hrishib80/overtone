import { createElement, escapeHtml, formatTime } from '../utils/dom.js';
import api from '../services/api.js';
import { createNavbar } from '../components/Navbar.js';
import router from '../services/router.js';

export default {
  render() {
    const page = createElement('div', { className: 'page chatlist-page' });
    
    const header = createElement('header', { className: 'header' });
    const title = createElement('h1', { className: 'header__title' }, 'Messages');
    header.appendChild(title);
    page.appendChild(header);
    
    const content = createElement('div', { className: 'chatlist-content' });
    page.appendChild(content);
    
    page.appendChild(createNavbar('/chat'));
    
    const loadConversations = async () => {
      try {
        const { conversations } = await api.getConversations();
        
        if (conversations.length === 0) {
          content.innerHTML = `
            <div class="empty-state">
              <p>No conversations yet</p>
            </div>
          `;
          return;
        }
        
        const list = createElement('ul', { className: 'chatlist' });
        
        conversations.forEach(conv => {
          const item = createElement('li', { 
            className: `chatlist-item ${conv.unreadCount > 0 ? 'unread' : ''}` 
          });
          
          item.onclick = () => {
            router.navigate(`/chat/${conv.matchId}`);
          };
          
          const avatar = createElement('img', { 
            className: 'chatlist-item__avatar',
            src: escapeHtml(conv.avatarUrl)
          });
          
          const details = createElement('div', { className: 'chatlist-item__details' });
          const nameRow = createElement('div', { className: 'chatlist-item__row' });
          const name = createElement('span', { className: 'chatlist-item__name' }, escapeHtml(conv.displayName));
          const time = createElement('span', { className: 'chatlist-item__time' }, formatTime(conv.lastMessageTime));
          
          nameRow.appendChild(name);
          nameRow.appendChild(time);
          
          const previewRow = createElement('div', { className: 'chatlist-item__row' });
          const preview = createElement('span', { className: 'chatlist-item__preview' }, escapeHtml(conv.lastMessagePreview));
          previewRow.appendChild(preview);
          
          if (conv.unreadCount > 0) {
            const badge = createElement('span', { className: 'badge new' }, conv.unreadCount.toString());
            previewRow.appendChild(badge);
          }
          
          details.appendChild(nameRow);
          details.appendChild(previewRow);
          
          item.appendChild(avatar);
          item.appendChild(details);
          
          list.appendChild(item);
        });
        
        content.appendChild(list);
      } catch (err) {
        content.innerHTML = '<div class="empty-state">Failed to load conversations</div>';
      }
    };
    
    loadConversations();
    
    return page;
  }
};
