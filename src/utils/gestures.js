export function enableSwipe(card, options = {}) {
  const {
    onSwipeLeft,
    onSwipeRight,
    onDrag,
    threshold = 100
  } = options;

  let startX = 0;
  let startY = 0;
  let currentDeltaX = 0;
  let isDragging = false;
  let scrollEl = null;

  const handlePointerDown = (e) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    if (e.target.closest('button, a, input, textarea')) return;

    scrollEl = card.querySelector('.swipe-card__scroll');
    if (scrollEl && scrollEl.scrollTop > 8) return;

    startX = e.clientX;
    startY = e.clientY;
    isDragging = true;
    card.classList.add('dragging');
    card.setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e) => {
    if (!isDragging) return;

    const deltaX = e.clientX - startX;
    const deltaY = e.clientY - startY;

    if (Math.abs(deltaY) > Math.abs(deltaX) && Math.abs(deltaY) > 12) {
      isDragging = false;
      card.classList.remove('dragging');
      card.removeAttribute('data-drag-dir');
      card.style.transform = '';
      card.releasePointerCapture(e.pointerId);
      return;
    }

    currentDeltaX = deltaX;
    const rotate = currentDeltaX * 0.08;
    card.style.transform = `translateX(${currentDeltaX}px) rotate(${rotate}deg)`;
    card.dataset.dragDir = currentDeltaX > 0 ? 'right' : 'left';

    if (onDrag) onDrag(currentDeltaX);
  };

  const handlePointerUp = (e) => {
    if (!isDragging) return;
    isDragging = false;
    card.classList.remove('dragging');
    card.removeAttribute('data-drag-dir');
    card.releasePointerCapture(e.pointerId);

    if (Math.abs(currentDeltaX) > threshold) {
      if (currentDeltaX > 0) {
        card.classList.add('swipe-right');
        if (onSwipeRight) onSwipeRight();
      } else {
        card.classList.add('swipe-left');
        if (onSwipeLeft) onSwipeLeft();
      }
    } else {
      card.style.transform = '';
      if (onDrag) onDrag(0);
    }

    currentDeltaX = 0;
  };

  card.addEventListener('pointerdown', handlePointerDown);
  card.addEventListener('pointermove', handlePointerMove);
  card.addEventListener('pointerup', handlePointerUp);
  card.addEventListener('pointercancel', handlePointerUp);

  return () => {
    card.removeEventListener('pointerdown', handlePointerDown);
    card.removeEventListener('pointermove', handlePointerMove);
    card.removeEventListener('pointerup', handlePointerUp);
    card.removeEventListener('pointercancel', handlePointerUp);
  };
}
