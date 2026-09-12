import { createElement, escapeHtml } from '../utils/dom.js';

export function createMatchModal(myProfile, theirProfile, { onSendMessage, onKeepSwiping }) {
  const overlay = createElement('div', { className: 'modal-overlay match-overlay' });
  
  const content = createElement('div', { className: 'match-content' });
  
  const title = createElement('h1', { className: 'match-title' }, "It's a Match!");
  content.appendChild(title);
  
  const avatars = createElement('div', { className: 'match-avatars' });
  
  const myAvatar = createElement('img', { 
    className: 'match-avatar match-avatar--left',
    src: escapeHtml(myProfile.avatar_url)
  });
  
  const heart = createElement('div', { className: 'match-heart' }, '❤️');
  
  const theirAvatar = createElement('img', { 
    className: 'match-avatar match-avatar--right',
    src: escapeHtml(theirProfile.avatarUrl)
  });
  
  avatars.appendChild(myAvatar);
  avatars.appendChild(heart);
  avatars.appendChild(theirAvatar);
  content.appendChild(avatars);
  
  const buttons = createElement('div', { className: 'match-buttons' });
  
  const sendBtn = createElement('button', { className: 'btn btn--primary' }, 'Send a Message');
  sendBtn.onclick = () => {
    onSendMessage();
  };
  
  const keepSwipingBtn = createElement('button', { className: 'btn btn--secondary' }, 'Keep Swiping');
  keepSwipingBtn.onclick = () => {
    onKeepSwiping();
  };
  
  buttons.appendChild(sendBtn);
  buttons.appendChild(keepSwipingBtn);
  content.appendChild(buttons);
  
  overlay.appendChild(content);
  
  // Confetti effect on mount
  if (window.confetti) {
    window.confetti({
      particleCount: 100,
      spread: 70,
      origin: { y: 0.6 },
      colors: ['#ff4b4b', '#ffffff']
    });
  }
  
  overlay.destroy = () => {
    overlay.remove();
  };
  
  return overlay;
}
