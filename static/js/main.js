// SoulWay — общие утилиты

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
        top: 20px;
        right: 20px;
        padding: 0.85rem 1.25rem;
        background: ${type === 'success' ? '#16a34a' : '#dc2626'};
        color: white;
        border-radius: 10px;
        z-index: 9999;
        font-family: 'DM Sans', sans-serif;
        font-size: 0.875rem;
        font-weight: 500;
        box-shadow: 0 4px 16px rgba(0,0,0,0.15);
        animation: swNotifIn 0.25s ease;
    `;
    notification.textContent = message;
    document.body.appendChild(notification);

    setTimeout(() => {
        notification.style.animation = 'swNotifOut 0.25s ease forwards';
        setTimeout(() => notification.remove(), 260);
    }, 3000);
}

const style = document.createElement('style');
style.textContent = `
    @keyframes swNotifIn {
        from { transform: translateX(120%); opacity: 0; }
        to   { transform: translateX(0);    opacity: 1; }
    }
    @keyframes swNotifOut {
        from { transform: translateX(0);    opacity: 1; }
        to   { transform: translateX(120%); opacity: 0; }
    }
`;
document.head.appendChild(style);

console.log('SoulWay ✨');