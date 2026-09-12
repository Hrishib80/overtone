import { createElement, escapeHtml } from '../utils/dom.js';
import api from '../services/api.js';
import router from '../services/router.js';
import { createChatBubble } from '../components/ChatBubble.js';
import { createTypingIndicator } from '../components/TypingIndicator.js';
import { createNavbar } from '../components/Navbar.js';
import callManager from '../services/callManager.js';
import { WebSocketClient } from '../services/websocket.js';
import store from '../services/store.js';

const icons = {
  back: '<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>',
  phone: '<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M6.62 10.79c1.44 2.83 3.76 5.15 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z"/></svg>',
  video: '<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/></svg>',
  send: '<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>'
};

export default {
  render(params) {
    const matchId = params.id;
    let peerProfile = null;
    let typingTimeout;
    let typingDebounce = null;
    const ws = new WebSocketClient();
    
    const page = createElement('div', { className: 'page chat-page' });
    const chatView = createElement('div', { className: 'chat-view' });
    
    const header = createElement('header', { className: 'chat-header' });
    const backBtn = createElement('button', { className: 'chat-header__btn chat-header__btn--back' });
    backBtn.innerHTML = icons.back;
    backBtn.onclick = () => router.navigate('/chat');
    
    const userInfo = createElement('div', { className: 'chat-header__user' });
    const avatar = createElement('img', { className: 'chat-header__avatar' });
    const infoText = createElement('div', { className: 'chat-header__info' });
    const name = createElement('h2', { className: 'chat-header__name' });
    const status = createElement('span', { className: 'chat-header__status' });
    infoText.appendChild(name);
    infoText.appendChild(status);
    userInfo.appendChild(avatar);
    userInfo.appendChild(infoText);
    
    const actions = createElement('div', { className: 'chat-header__actions' });
    const voiceBtn = createElement('button', { className: 'chat-header__btn' });
    voiceBtn.innerHTML = icons.phone;
    voiceBtn.onclick = () => {
      if (peerProfile) {
        callManager.initiateCall({
          peerId: peerProfile.id,
          type: 'voice',
          ws,
          peerName: peerProfile.displayName,
          peerAvatarUrl: peerProfile.avatarUrl
        });
      }
    };
    const videoBtn = createElement('button', { className: 'chat-header__btn' });
    videoBtn.innerHTML = icons.video;
    videoBtn.onclick = () => {
      if (peerProfile) {
        callManager.initiateCall({
          peerId: peerProfile.id,
          type: 'video',
          ws,
          peerName: peerProfile.displayName,
          peerAvatarUrl: peerProfile.avatarUrl
        });
      }
    };
    actions.appendChild(voiceBtn);
    actions.appendChild(videoBtn);
    
    header.appendChild(backBtn);
    header.appendChild(userInfo);
    header.appendChild(actions);
    chatView.appendChild(header);
    
    const messagesArea = createElement('ul', { className: 'chat-messages' });
    chatView.appendChild(messagesArea);
    
    let typingIndicatorNode = null;
    
    const scrollToBottom = () => {
      requestAnimationFrame(() => {
        messagesArea.scrollTop = messagesArea.scrollHeight;
      });
    };
    
    const inputArea = createElement('form', { className: 'chat-input-bar' });
    const inputField = createElement('input', { 
      type: 'text', 
      className: 'chat-input__field',
      placeholder: 'Type a message...'
    });
    const sendBtn = createElement('button', { 
      type: 'submit', 
      className: 'icon-btn chat-input__send'
    });
    sendBtn.innerHTML = icons.send;
    
    inputArea.appendChild(inputField);
    inputArea.appendChild(sendBtn);
    chatView.appendChild(inputArea);

    page.appendChild(chatView);
    page.appendChild(createNavbar('/chat'));
    
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const msgId = entry.target.dataset.id;
          if (msgId) {
            ws.send('read-receipt', { matchId, messageId: msgId });
            observer.unobserve(entry.target);
          }
        }
      });
    }, { root: messagesArea, threshold: 1.0 });

    const addMessage = (msg) => {
      const bubble = createChatBubble(msg);
      bubble.dataset.id = msg.id;
      if (!msg.isMine) {
        observer.observe(bubble);
      }
      messagesArea.appendChild(bubble);
      scrollToBottom();
    };

    const loadData = async () => {
      try {
        const { messages, peer } = await api.getChatMessages(matchId);
        peerProfile = peer;
        
        // Set avatar src directly — don't escapeHtml on URLs (double-encodes &)
        avatar.src = peer.avatarUrl || '';
        name.textContent = peer.displayName || 'Unknown';
        status.textContent = peer.isOnline ? 'Online' : 'Offline';
        
        messages.forEach(addMessage);
        
        if (ws.state !== 'connected') ws.connect(matchId);
        callManager.bindSignaling(ws);
        
        ws.on('chat-message', (data) => {
          if (data.matchId === matchId) {
            addMessage(data.message);
          }
        });
        ws.on('typing', (data) => {
          if (data.matchId === matchId) {
            if (!typingIndicatorNode) {
              typingIndicatorNode = createTypingIndicator();
              messagesArea.appendChild(typingIndicatorNode);
              scrollToBottom();
            }
            clearTimeout(typingTimeout);
            typingTimeout = setTimeout(() => {
              if (typingIndicatorNode) {
                typingIndicatorNode.remove();
                typingIndicatorNode = null;
              }
            }, 3000);
          }
        });
        
      } catch (err) {
        console.error("Failed to load chat", err);
      }
    };
    
    // Debounced typing indicator — send at most once per second
    inputField.addEventListener('input', () => {
      if (!typingDebounce) {
        ws.send('typing', { matchId });
        typingDebounce = setTimeout(() => {
          typingDebounce = null;
        }, 1000);
      }
    });
    
    inputArea.onsubmit = (e) => {
      e.preventDefault();
      const text = inputField.value.trim();
      if (!text) return;
      
      const msg = {
        id: Date.now().toString(),
        text,
        isMine: true,
        timestamp: Date.now(),
        status: 'sent'
      };
      
      ws.send('chat-message', { matchId, message: msg });
      addMessage(msg);
      
      inputField.value = '';
    };
    
    loadData();
    
    // Cleanup on page destroy — disconnect WS, observer, and timers
    page.destroy = () => {
      observer.disconnect();
      ws.disconnect();
      clearTimeout(typingTimeout);
      clearTimeout(typingDebounce);
    };
    
    return page;
  }
};
