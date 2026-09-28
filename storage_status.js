(async () => {
  try {
    const response = await fetch('/api/storage-status', {cache:'no-store'});
    if (!response.ok) return;
    const status = await response.json();
    if (status.writes_enabled) return;
    const notice = document.createElement('p');
    notice.setAttribute('role','alert');
    notice.style.cssText = 'padding:16px;margin:16px;border:2px solid #e7ad53;border-radius:12px;background:#30281b;color:#fff';
    notice.textContent = status.message;
    document.body.prepend(notice);
  } catch (_) { /* Existing forms report server connectivity failures. */ }
})();
