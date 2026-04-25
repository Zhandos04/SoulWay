// SoulWay — утилиты

function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('ru-RU', {
        year: 'numeric', month: 'long', day: 'numeric',
        hour: '2-digit', minute: '2-digit'
    });
}

function showNotification(message, type = 'success') {
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        top: 24px;
        right: 24px;
        padding: 0.9rem 1.4rem;
        background: ${type === 'success' ? '#2F5438' : '#c53030'};
        color: #F5F0E8;
        border-radius: 999px;
        z-index: 9999;
        font-family: 'Plus Jakarta Sans', sans-serif;
        font-size: 0.875rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        box-shadow: 0 8px 24px rgba(28,25,23,0.18);
        animation: swNotifIn 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
        display: flex;
        align-items: center;
        gap: 8px;
    `;
    notification.innerHTML = `<span>${type === 'success' ? '✓' : '✕'}</span> ${message}`;
    document.body.appendChild(notification);

    setTimeout(() => {
        notification.style.animation = 'swNotifOut 0.25s ease forwards';
        setTimeout(() => notification.remove(), 260);
    }, 3000);
}

console.log('SoulWay ✨');