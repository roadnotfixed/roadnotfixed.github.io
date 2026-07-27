(() => {
  const dialog = document.querySelector('[data-photo-lightbox]');
  const items = Array.from(document.querySelectorAll('[data-gallery-item]'));

  if (!dialog || !items.length || typeof dialog.showModal !== 'function') {
    return;
  }

  const image = dialog.querySelector('[data-lightbox-image]');
  const caption = dialog.querySelector('[data-lightbox-caption]');
  const original = dialog.querySelector('[data-lightbox-original]');
  const previous = dialog.querySelector('[data-lightbox-prev]');
  const next = dialog.querySelector('[data-lightbox-next]');
  let currentIndex = 0;

  function render(index) {
    currentIndex = (index + items.length) % items.length;
    const item = items[currentIndex];
    image.src = item.dataset.gallerySrc;
    image.alt = item.dataset.galleryAlt || '';
    caption.textContent = item.dataset.galleryCaption || '';
    caption.hidden = !caption.textContent;
    original.href = item.href;
  }

  function open(index) {
    render(index);
    dialog.showModal();
  }

  items.forEach((item, index) => {
    item.addEventListener('click', (event) => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
        return;
      }
      event.preventDefault();
      open(index);
    });
  });

  previous.addEventListener('click', () => render(currentIndex - 1));
  next.addEventListener('click', () => render(currentIndex + 1));

  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });

  dialog.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowLeft') {
      render(currentIndex - 1);
    } else if (event.key === 'ArrowRight') {
      render(currentIndex + 1);
    }
  });

  dialog.addEventListener('close', () => {
    image.removeAttribute('src');
  });
})();
