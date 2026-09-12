import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import { createNavbar } from '../components/Navbar.js';
import { createProfileCard, animateCardAction } from '../components/ProfileCard.js';
import { createMatchModal } from '../components/MatchModal.js';
import { enableSwipe } from '../utils/gestures.js';
import store from '../services/store.js';
import router from '../services/router.js';

const icons = {
  close: '<svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>',
  heart: '<svg viewBox="0 0 24 24" width="30" height="30" fill="currentColor"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'
};

export default {
  render() {
    const page = createElement('div', { className: 'page discover-page' });

    const header = createElement('header', { className: 'header' });
    header.appendChild(createElement('h1', { className: 'header__title' }, 'Discover'));
    page.appendChild(header);

    const cardStack = createElement('div', { className: 'card-stack' });
    page.appendChild(cardStack);

    const actions = createElement('div', { className: 'discover-actions' });
    const passBtn = createElement('button', {
      className: 'action-btn action-btn--pass',
      title: 'Pass',
      disabled: true
    });
    passBtn.innerHTML = icons.close;

    const likeBtn = createElement('button', {
      className: 'action-btn action-btn--like',
      title: 'Like',
      disabled: true
    });
    likeBtn.innerHTML = icons.heart;

    actions.appendChild(passBtn);
    actions.appendChild(likeBtn);
    page.appendChild(actions);
    page.appendChild(createNavbar('/'));

    let candidates = [];
    let swipeCleanup = null;
    let actionLocked = false;

    const setActionsEnabled = (enabled) => {
      passBtn.disabled = !enabled;
      likeBtn.disabled = !enabled;
    };

    const handleAction = async (profile, action) => {
      if (actionLocked) return;
      actionLocked = true;
      setActionsEnabled(false);

      candidates.shift();

      if (action === 'like') {
        try {
          const res = await api.sendLike(profile.id, 'profile', profile.id);
          if (res.matched) {
            const myProfile = store.getState().currentUser;
            const modal = createMatchModal(myProfile, profile, {
              onSendMessage: () => {
                modal.destroy();
                router.navigate(`/chat/${res.match_id}`);
              },
              onKeepSwiping: () => modal.destroy()
            });
            document.getElementById('modal-root').appendChild(modal);
          }
        } catch (e) {
          console.error(e);
        }
      }

      setTimeout(() => {
        actionLocked = false;
        renderStack();
      }, 320);
    };

    const bindTopCard = () => {
      if (swipeCleanup) swipeCleanup();
      swipeCleanup = null;

      const topCard = cardStack.querySelector('.swipe-card:first-child');
      if (!topCard || candidates.length === 0) {
        setActionsEnabled(false);
        return;
      }

      const profile = candidates[0];
      setActionsEnabled(true);

      passBtn.onclick = () => {
        animateCardAction(topCard, 'pass');
        handleAction(profile, 'pass');
      };
      likeBtn.onclick = () => {
        animateCardAction(topCard, 'like');
        handleAction(profile, 'like');
      };

      swipeCleanup = enableSwipe(topCard, {
        onSwipeLeft: () => handleAction(profile, 'pass'),
        onSwipeRight: () => handleAction(profile, 'like')
      });
    };

    const renderStack = () => {
      if (swipeCleanup) swipeCleanup();
      swipeCleanup = null;
      cardStack.innerHTML = '';

      if (candidates.length === 0) {
        setActionsEnabled(false);
        cardStack.innerHTML = `
          <div class="empty-state">
            <div class="empty-state__illustration">🌟</div>
            <p>No more profiles nearby</p>
            <p style="font-size:var(--fs-sm);color:var(--color-text-muted)">Check back later for new matches</p>
          </div>
        `;
        return;
      }

      const visible = candidates.slice(0, 3);
      visible.forEach((profile, index) => {
        const card = createProfileCard(profile);
        if (index > 0) {
          card.style.transform = `scale(${1 - index * 0.04}) translateY(${index * 8}px)`;
        }
        cardStack.appendChild(card);
      });

      bindTopCard();
    };

    const loadCandidates = async () => {
      cardStack.innerHTML = '<div class="card-skeleton shimmer"></div>';
      setActionsEnabled(false);

      try {
        const currentUser = store.getState().currentUser;
        if (!currentUser) {
          cardStack.innerHTML = '<div class="empty-state"><p>Setting up your profile...</p></div>';
          return;
        }

        candidates = (await api.getMatchCandidates(currentUser.id)).matches || [];
        renderStack();
      } catch (err) {
        cardStack.innerHTML = '<div class="empty-state">Failed to load profiles</div>';
      }
    };

    page.destroy = () => {
      if (swipeCleanup) swipeCleanup();
    };

    loadCandidates();
    return page;
  }
};
