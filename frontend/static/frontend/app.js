const apiBase = "/api";

// Theme Logic
const toggleTheme = () => {
  const root = document.documentElement;
  root.classList.toggle('light-mode');
  const isLight = root.classList.contains('light-mode');
  localStorage.setItem('theme', isLight ? 'light' : 'dark');
};

// Init Theme
document.addEventListener("DOMContentLoaded", () => {
  const savedTheme = localStorage.getItem('theme');
  if (savedTheme === 'light') {
    document.documentElement.classList.add('light-mode');
  }
});

const getToken = () => "";

const setStatus = (message) => {
  const status = document.getElementById("chat-status");
  if (status) status.textContent = message;
};

// Russian Error Translations
const errorTranslations = {
  'NetworkError': 'Ошибка сети. Проверьте подключение.',
  'Failed to fetch': 'Не удалось выполнить запрос. Ошибка сети.',
  'Chat is not assigned': 'Чат не назначен оператору.',
  'Operator not assigned': 'Оператор не назначен.',
  'Upload failed': 'Ошибка загрузки файла.',
  'Download failed': 'Ошибка скачивания файла.',
  'Internal Server Error': 'Внутренняя ошибка сервера.',
  'Not Found': 'Ресурс не найден.',
  'Forbidden': 'Доступ запрещен.',
  'Unauthorized': 'Необходима авторизация.'
};

const getRussianError = (msg) => {
  if (!msg) return 'Неизвестная ошибка';

  // Check exact matches
  if (errorTranslations[msg]) return errorTranslations[msg];

  // Check partial matches or patterns
  if (msg.includes('HTTP 429')) return 'GREEN-API временно ограничил частоту запросов. CRM повторит запросы с паузой; подождите немного и не запускайте загрузку повторно.';
  if (msg.includes('HTTP 404')) return 'Ресурс не найден (404).';
  if (msg.includes('HTTP 500')) return 'Ошибка сервера (500).';
  if (msg.includes('HTTP 403')) return 'Доступ запрещен (403).';
  if (msg.includes('Chat is not assigned')) return 'Чат не назначен оператору.';
  if (msg.includes('Media file does not exist')) return 'Файл удалён из временного хранилища. Прикрепите его заново.';
  if (msg.includes('100 MB') || msg.includes('100 МБ') || msg.includes('HTTP 413')) return 'Файл превышает допустимый размер 100 МБ.';
  if (msg.includes('Provider did not confirm')) return 'Мессенджер не подтвердил отправку файла. Повторите попытку.';
  if (msg.includes('No active Telegram client')) return 'Telegram-аккаунт сейчас не подключён.';

  return msg; // Return original if no translation found
};

const showNotification = (title, message, duration = 5000) => {
  console.log(`Notification: [${title}] ${message}`); // Debugging
  const container = document.getElementById('notification-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = 'notification-toast';
  toast.innerHTML = `
        <div class="notification-header">
            <div class="notification-title">${escapeHtml(title)}</div>
            <div class="notification-close" onclick="this.parentElement.parentElement.remove()">×</div>
        </div>
        <div class="notification-body">${escapeHtml(message)}</div>
    `;

  container.appendChild(toast);

  // Increase duration for important info
  const finalDuration = (title === 'Ошибка' || message.includes('активаци')) ? duration * 2 : duration;

  if (finalDuration > 0) {
    setTimeout(() => {
      if (toast.parentElement) {
        toast.style.animation = 'fadeOut 0.3s ease-out forwards';
        setTimeout(() => toast.remove(), 300);
      }
    }, finalDuration);
  }
};

const setError = (message, accountName = null) => {
  if (!message) return;

  // If it's an account error, only show it once
  if (accountName) {
    if (notifiedAccounts.has(accountName)) return;
    notifiedAccounts.add(accountName);
  }

  const russianMessage = getRussianError(message);
  showNotification('Ошибка', russianMessage);
};

const request = async (url, options = {}) => {
  const defaults = {
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken(),
    },
  };
  try {
    const response = await fetch(url, { ...defaults, ...options });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      console.error(`Request failed: ${url}`, errorData); // Debugging
      const rawError = errorData.error || errorData.detail || errorData.message || `HTTP ${response.status}`;
      throw new Error(rawError);
    }
    return response.json();
  } catch (e) {
    console.error(`Request error: ${url}`, e); // Debugging
    if (e.message === 'Failed to fetch') {
      throw new Error('NetworkError');
    }
    throw e;
  }
};

const getCsrfToken = () => {
  return document.querySelector('[name=csrfmiddlewaretoken]')?.value || "";
};

const escapeHtml = (value) => {
  const element = document.createElement("div");
  element.textContent = String(value ?? "");
  return element.innerHTML;
};

const normalizeList = (payload) => {
  if (Array.isArray(payload)) return payload;
  if (payload && Array.isArray(payload.results)) return payload.results;
  return [];
};

const renderTgsSticker = async (element) => {
  if (!element || element.dataset.loaded === '1') return;
  element.dataset.loaded = '1';
  try {
    const response = await fetch(element.dataset.src);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const bytes = await response.arrayBuffer();
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    const animationData = JSON.parse(await new Response(stream).text());
    if (!window.lottie) throw new Error('Lottie renderer unavailable');
    window.lottie.loadAnimation({container: element, renderer: 'svg', loop: true, autoplay: true, animationData});
  } catch (error) {
    element.innerHTML = '<span class="sticker-fallback">Анимированный стикер</span>';
    console.error('Could not render TGS sticker', error);
  }
};

const localizeError = (err) => {
  return getRussianError(err);
};

let currentChatId = null;
let lastRenderedMessageId = null;
let currentMedia = []; // Uploaded attachments for the next send
let messageSearchQuery = ""; // Current search query for messages
let allChats = [];
const supportedMessengerViews = new Set(["all", "telegram", "max", "whatsapp"]);
let currentMessenger = localStorage.getItem("messenger") || "all";
if (!supportedMessengerViews.has(currentMessenger)) currentMessenger = "all";
let archiveMode = false;
let replyTarget = null;
let contextChatId = null;
let messageRequestVersion = 0;
let messageFetchLimit = 100;

const getChatName = (chat) => chat?.title || chat?.username || chat?.first_name || "Без имени";
const getMessenger = (chat) => {
  const type = chat?.telegram_account?.account_type;
  return type === "whatsapp" || type === "max" ? type : "telegram";
};
const isChatInCurrentScope = (chat, messenger = currentMessenger) => (
  messenger === "all" || getMessenger(chat) === messenger
);
const getMessengerLabel = (messenger) => ({
  telegram: "Telegram",
  max: "MAX",
  whatsapp: "WhatsApp",
}[messenger] || "Мессенджер");
const getMessengerIcon = (messenger) => ({
  telegram: "send",
  max: "M",
  whatsapp: "chat",
}[messenger] || "forum");
const getAccountType = (chat) => chat?.telegram_account?.account_type || "personal";
const getAccountLabel = (chat) => ({
  bot: "Бот",
  personal: "Личный аккаунт",
  whatsapp: "WhatsApp",
  max: "MAX личный",
}[getAccountType(chat)] || "Канал");
const getChatActivityDate = (chat) => new Date(
  chat?.last_message_data?.telegram_date || chat?.last_message_at || chat?.updated_at || 0
);
const orderChats = (chats, messenger = currentMessenger) => chats
  .filter((chat) => isChatInCurrentScope(chat, messenger) && Boolean(chat.is_archived) === archiveMode)
  .sort((a, b) => {
    const typeDiff = messenger === "telegram"
      ? (getAccountType(a) === "bot" ? 0 : 1) - (getAccountType(b) === "bot" ? 0 : 1)
      : 0;
    return typeDiff || (getChatActivityDate(b) - getChatActivityDate(a));
  });

const setComposerEnabled = (enabled) => {
  ["message-input", "attach-btn", "emoji-btn", "send-button"].forEach((id) => {
    const element = document.getElementById(id);
    if (element) element.disabled = !enabled;
  });
};

const clearReplyTarget = () => {
  replyTarget = null;
  const panel = document.getElementById('reply-composer');
  if (panel) panel.hidden = true;
  const preview = document.getElementById('reply-preview');
  if (preview) preview.textContent = '';
};

const setReplyTarget = (message) => {
  if (!message || String(message.id).startsWith('delivery-')) return;
  replyTarget = message;
  const panel = document.getElementById('reply-composer');
  const title = document.getElementById('reply-title');
  const preview = document.getElementById('reply-preview');
  if (title) title.textContent = `Ответ: ${message.from_user_name || (message.is_outgoing ? 'вы' : 'собеседник')}`;
  if (preview) preview.textContent = message.text || message.media_caption || `[${message.message_type_display || 'Медиа'}]`;
  if (panel) panel.hidden = false;
  document.getElementById('message-input')?.focus();
};

const renderAccountHealth = (chats) => {
  const banner = document.getElementById("account-health");
  if (!banner) return;
  const uniqueAccounts = new Map();
  chats.filter((chat) => isChatInCurrentScope(chat)).forEach((chat) => {
    const account = chat.telegram_account;
    if (account && account.status !== "active") uniqueAccounts.set(String(account.id ?? account.name), account.name || "Аккаунт без названия");
  });
  const names = [...uniqueAccounts.values()];
  banner.hidden = names.length === 0;
  banner.replaceChildren();
  if (!names.length) return;
  const icon = document.createElement("i");
  icon.className = "material-icons";
  icon.textContent = "warning_amber";
  const info = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = names.length === 1 ? "Аккаунт требует внимания" : "Аккаунты требуют внимания";
  const details = document.createElement("span");
  details.textContent = names.join(", ");
  info.append(title, details);
  banner.append(icon, info);
};

const showConversationLoading = () => {
  const list = document.getElementById('message-list');
  if (!list) return;
  list.classList.add('is-loading');
  list.innerHTML = `
    <div class="conversation-skeleton" aria-label="Загрузка сообщений">
      <span></span><span></span><span></span><span></span>
    </div>`;
};

const selectChat = (id) => {
  const chat = allChats.find((item) => Number(item.id) === Number(id));
  if (!chat || !isChatInCurrentScope(chat) || Boolean(chat.is_archived) !== archiveMode) return;
  currentChatId = id;
  messageFetchLimit = 100;
  messageRequestVersion += 1;
  lastRenderedMessageId = null; // Reset for scroll
  messageSearchQuery = ""; // Reset search on chat change
  clearReplyTarget();
  const searchInput = document.getElementById("message-search-input");
  if (searchInput) searchInput.value = "";
  const searchContainer = document.getElementById("message-search-container");
  if (searchContainer) searchContainer.hidden = true;

  setActiveChat(chat);
  showConversationLoading();

  // Mark locally as read immediately
  const chatLi = document.querySelector(`#chat-list .chat-item[data-chat-id="${id}"]`);
  if (chatLi) {
    const indicator = chatLi.querySelector('.unread-indicator');
    if (indicator) indicator.remove();
  }

  // API call to mark as read on backend
  fetch(`${apiBase}/chats/${id}/mark_as_read/`, {
    method: 'POST',
    headers: {
      'X-CSRFToken': getCsrfToken()
    }
  }).catch(e => console.error("Failed to mark as read", e));

  document.querySelectorAll('#chat-list .chat-item').forEach(li => {
    if (Number(li.dataset.chatId) === id) li.classList.add('active');
    else li.classList.remove('active');
  });

  if (window.fetchMessagesGlobal) window.fetchMessagesGlobal(true);

  // Clear unread immediately on client side
  const chatItem = document.querySelector(`.chat-item[data-chat-id="${id}"]`);
  if (chatItem) {
    const unread = chatItem.querySelector('.unread-indicator');
    if (unread) unread.remove();
  }
};

const getInitials = (name) => {
  return name ? name.substring(0, 2).toUpperCase() : "??";
};

const formatChatListTimestamp = (value) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const today = new Date();
  const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const days = Math.round((startToday - startDate) / 86400000);
  if (days === 0) return date.toLocaleTimeString('ru-RU', {hour: '2-digit', minute: '2-digit'});
  if (days === 1) return 'вчера';
  if (days > 1 && days < 7) return date.toLocaleDateString('ru-RU', {weekday: 'short'}).replace('.', '');
  return date.toLocaleDateString('ru-RU', {
    day: '2-digit', month: '2-digit', ...(date.getFullYear() !== today.getFullYear() ? {year: '2-digit'} : {}),
  });
};

const closeChatContextMenu = () => {
  const menu = document.getElementById('chat-context-menu');
  if (menu) menu.hidden = true;
  contextChatId = null;
};

const showChatContextMenu = (event, chat) => {
  event.preventDefault();
  event.stopPropagation();
  contextChatId = chat.id;
  const menu = document.getElementById('chat-context-menu');
  const action = document.getElementById('chat-archive-action');
  if (!menu || !action) return;
  action.querySelector('i').textContent = chat.is_archived ? 'unarchive' : 'archive';
  action.querySelector('span').textContent = chat.is_archived ? 'Вернуть из архива' : 'Убрать в архив';
  menu.hidden = false;
  const width = 220;
  const height = 48;
  menu.style.left = `${Math.min(event.clientX, window.innerWidth - width - 8)}px`;
  menu.style.top = `${Math.min(event.clientY, window.innerHeight - height - 8)}px`;
};

const resetConversation = () => {
  messageRequestVersion += 1;
  currentChatId = null;
  lastRenderedMessageId = null;
  renderedMessageIds.clear();
  lastContentSnapshot = '';
  messageFetchLimit = 100;
  clearReplyTarget();
  setActiveChat(null);
  const sticky = document.getElementById('sticky-date-header');
  if (sticky) sticky.hidden = true;
  const list = document.getElementById('message-list');
  if (list) {
    list.classList.remove('is-loading');
    list.innerHTML = '<div class="empty-state"><i class="material-icons">forum</i><strong>Выберите диалог</strong><span>Здесь появится история сообщений</span></div>';
  }
};

const setArchiveMode = (enabled) => {
  archiveMode = Boolean(enabled);
  renderChats(allChats);
  const first = orderChats(allChats)[0];
  if (first) selectChat(first.id);
  else resetConversation();
};

const changeChatArchiveState = async (chat) => {
  const action = chat.is_archived ? 'unarchive' : 'archive';
  try {
    const result = await request(`${apiBase}/chats/${chat.id}/${action}/`, {method: 'POST'});
    const wasCurrent = Number(currentChatId) === Number(chat.id);
    allChats = allChats.map((item) => Number(item.id) === Number(chat.id)
      ? {...item, is_archived: Boolean(result.is_archived)}
      : item);
    closeChatContextMenu();
    renderChats(allChats);
    const first = orderChats(allChats)[0];
    if (wasCurrent || !orderChats(allChats).some((item) => Number(item.id) === Number(currentChatId))) {
      if (first) selectChat(first.id);
      else resetConversation();
    }
  } catch (error) {
    setError(error.message || 'Не удалось изменить состояние архива.');
  }
};

const renderChats = (chats) => {
  const chatList = document.getElementById('chat-list');
  const chatCount = document.getElementById('chat-count');
  if (!chatList) return;
  const messengerChats = chats.filter((chat) => isChatInCurrentScope(chat));
  const archivedCount = messengerChats.filter((chat) => chat.is_archived).length;
  const visibleChats = orderChats(chats);
  const archiveToggle = document.getElementById('archive-toggle');
  const archiveCount = document.getElementById('archive-count');
  const archiveSubtitle = document.getElementById('archive-subtitle');
  const listTitle = document.getElementById('chat-list-title');
  if (archiveToggle) {
    archiveToggle.classList.toggle('active', archiveMode);
    archiveToggle.setAttribute('aria-pressed', String(archiveMode));
  }
  if (archiveCount) archiveCount.textContent = String(archivedCount);
  if (archiveSubtitle) archiveSubtitle.textContent = archivedCount ? `${archivedCount} ${archivedCount === 1 ? 'диалог' : 'диалогов'}` : 'Нет архивных диалогов';
  if (listTitle) listTitle.textContent = archiveMode ? 'Архив' : 'Все диалоги';
  chatList.replaceChildren();
  if (chatCount) chatCount.textContent = String(visibleChats.length);

  if (!visibleChats.length) {
    const empty = document.createElement('li');
    empty.className = 'chat-list-empty';
    const channel = currentMessenger === 'all'
      ? 'из подключённых мессенджеров'
      : `из ${getMessengerLabel(currentMessenger)}`;
    empty.innerHTML = archiveMode
      ? '<i class="material-icons">inventory_2</i><strong>Архив пуст</strong><span>Заархивированные диалоги появятся здесь</span>'
      : `<i class="material-icons">inbox</i><strong>Нет активных диалогов</strong><span>Новые обращения ${channel} появятся здесь</span>`;
    chatList.appendChild(empty);
    return;
  }

  visibleChats.forEach((chat) => {
    const li = document.createElement('li');
    const accountType = getAccountType(chat);
    li.className = `chat-item chat-item--${accountType}${currentChatId === chat.id ? ' active' : ''}`;
    li.dataset.chatId = chat.id;
    const name = getChatName(chat);
    li.dataset.searchText = [
      name, chat.username, chat.first_name, chat.last_name,
      chat.last_message_preview, chat.last_message, chat.telegram_account?.name,
    ].filter(Boolean).join(' ').toLowerCase();
    const avatar = document.createElement('div');
    avatar.className = 'chat-avatar';
    avatar.textContent = getInitials(name);
    const content = document.createElement('div');
    content.className = 'chat-content-text';
    const top = document.createElement('div');
    top.className = 'chat-top';
    const title = document.createElement('span');
    title.className = 'chat-title';
    title.textContent = name;
    const time = document.createElement('span');
    time.className = 'chat-time';
    const lastDate = chat.last_message_data?.telegram_date || chat.last_message_at || chat.updated_at;
    if (lastDate) time.textContent = formatChatListTimestamp(lastDate);
    top.append(title, time);
    const source = document.createElement('div');
    source.className = 'chat-source-row';
    const messenger = getMessenger(chat);
    if (currentMessenger === 'all') {
      const messengerBadge = document.createElement('span');
      messengerBadge.className = `messenger-badge messenger-badge--${messenger}`;
      const messengerIcon = document.createElement('i');
      messengerIcon.className = messenger === 'max' ? 'messenger-letter-icon' : 'material-icons';
      messengerIcon.textContent = getMessengerIcon(messenger);
      messengerBadge.append(messengerIcon, document.createTextNode(getMessengerLabel(messenger)));
      source.appendChild(messengerBadge);
    }
    const badge = document.createElement('span');
    badge.className = `account-badge account-badge--${accountType}`;
    badge.innerHTML = `<i class="material-icons">${accountType === 'bot' ? 'smart_toy' : accountType === 'whatsapp' ? 'chat' : 'person'}</i>`;
    badge.append(document.createTextNode(getAccountLabel(chat)));
    const accountName = document.createElement('span');
    accountName.className = 'chat-account-name';
    accountName.textContent = chat.telegram_account?.name || (accountType === 'max' ? 'MAX' : accountType === 'whatsapp' ? 'WhatsApp' : 'Telegram');
    source.append(badge, accountName);
    const bottom = document.createElement('div');
    bottom.className = 'chat-bottom';
    const preview = document.createElement('span');
    preview.className = 'chat-last-message';
    preview.textContent = chat.last_message_preview || chat.last_message || 'Сообщений пока нет';
    bottom.appendChild(preview);
    if (chat.unread_count > 0) {
      const unread = document.createElement('span');
      unread.className = 'unread-indicator';
      unread.textContent = chat.unread_count > 99 ? '99+' : String(chat.unread_count);
      bottom.appendChild(unread);
    }
    content.append(top, source, bottom);
    li.append(avatar, content);
    li.addEventListener('click', () => selectChat(chat.id));
    li.addEventListener('contextmenu', (event) => showChatContextMenu(event, chat));
    chatList.appendChild(li);
  });
};
// Helper to get status icon
const getStatusIcon = (msg) => {
  const providerStatus = msg?.metadata?.provider_status;
  if (providerStatus === 'read' || providerStatus === 'delivered') return '✓✓';
  if (providerStatus === 'sent') return '✓';
  switch (msg?.status) {
    case 'pending': return '◷';
    case 'sent': return '✓';
    case 'received': return '✓✓';
    case 'failed': return '⚠';
    default: return '';
  }
};
const getStatusClass = (msg) => msg?.metadata?.provider_status === 'read' ? ' status-icon--read' : '';

const INLINE_IMAGE_MIMES = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif']);
const getMediaTypeFromMime = (mimeType = '') => {
  const normalized = String(mimeType).split(';', 1)[0].toLowerCase();
  if (INLINE_IMAGE_MIMES.has(normalized)) return 'photo';
  if (normalized.startsWith('video/')) return 'video';
  if (normalized.startsWith('audio/')) return 'voice';
  return 'document';
};
const formatBytes = (size = 0) => {
  if (size < 1024) return `${size} Б`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} КБ`;
  return `${(size / 1024 / 1024).toFixed(1)} МБ`;
};

let lastContentSnapshot = "";

// Media Viewer Logic
const mediaViewer = document.getElementById("media-viewer");
const mediaViewerImg = document.getElementById("media-viewer-img");
const mediaViewerVideo = document.getElementById("media-viewer-video");
const mediaViewerClose = document.querySelector(".media-viewer-close");

window.openMediaViewer = (url, type) => {
  if (!mediaViewer) return;

  mediaViewer.style.display = "flex";

  if (type === 'image') {
    mediaViewerImg.src = url;
    mediaViewerImg.style.display = "block";
    if (mediaViewerVideo) {
      mediaViewerVideo.style.display = "none";
      mediaViewerVideo.pause();
    }
  } else if (type === 'video') {
    mediaViewerVideo.src = url;
    mediaViewerVideo.style.display = "block";
    if (mediaViewerImg) mediaViewerImg.style.display = "none";
  }
};

if (mediaViewerClose) {
  mediaViewerClose.onclick = () => {
    mediaViewer.style.display = "none";
    if (mediaViewerVideo) {
      mediaViewerVideo.pause();
      mediaViewerVideo.src = "";
    }
    if (mediaViewerImg) mediaViewerImg.src = "";
  }
}

// Close on outside click
window.onclick = (event) => {
  if (event.target === mediaViewer) {
    mediaViewer.style.display = "none";
    if (mediaViewerVideo) mediaViewerVideo.pause();
  }
}

let renderedMessageIds = new Set();
let lastDateString = null;

const formatDateHeader = (date) => {
  const options = { month: 'long', day: 'numeric' };
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);

  if (date.toDateString() === today.toDateString()) return 'Сегодня';
  if (date.toDateString() === yesterday.toDateString()) return 'Вчера';
  if (date.getFullYear() !== today.getFullYear()) options.year = 'numeric';

  return date.toLocaleDateString('ru-RU', options);
};

const localDateKey = (value) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'unknown';
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
};

const rebuildMessageTimeline = (messageList, sortedMessages) => {
  messageList.querySelectorAll('.date-divider').forEach((node) => node.remove());
  let previousDate = null;
  sortedMessages.forEach((message) => {
    const node = messageList.querySelector(`.message[data-msg-id="${message.id}"]`);
    if (!node || node.hidden) return;
    const date = new Date(message.telegram_date);
    const dateKey = localDateKey(message.telegram_date);
    if (dateKey !== previousDate && dateKey !== 'unknown') {
      const divider = document.createElement('div');
      divider.className = 'date-divider';
      divider.dataset.date = dateKey;
      divider.innerHTML = `<span>${escapeHtml(formatDateHeader(date))}</span>`;
      messageList.appendChild(divider);
      previousDate = dateKey;
    }
    messageList.appendChild(node);
  });
  updateStickyDate();
};

// Sticky date logic
const updateStickyDate = () => {
  const messageList = document.getElementById("message-list");
  const stickyDateHeader = document.getElementById("sticky-date-header");
  if (!messageList || !stickyDateHeader) return;

  const dividers = Array.from(messageList.querySelectorAll('.date-divider'));
  let currentDivider = null;
  const timelineTop = messageList.getBoundingClientRect().top;

  for (const divider of dividers) {
    if (divider.getBoundingClientRect().top < timelineTop - 4) {
      currentDivider = divider;
    } else {
      break;
    }
  }

  const visibleDividerAtTop = dividers.some((divider) => {
    const top = divider.getBoundingClientRect().top;
    return top >= timelineTop - 4 && top <= timelineTop + 38;
  });
  if (visibleDividerAtTop) currentDivider = null;

  if (currentDivider) {
    stickyDateHeader.textContent = currentDivider.textContent;
    stickyDateHeader.hidden = false;
  } else {
    stickyDateHeader.hidden = true;
  }
};

const renderMessages = (messages, forceScroll = false) => {
  const messageList = document.getElementById("message-list");
  if (!messageList) return;

  // Ensure sticky header exists
  let stickyDateHeader = document.getElementById("sticky-date-header");
  if (!stickyDateHeader) {
    stickyDateHeader = document.createElement("div");
    stickyDateHeader.id = "sticky-date-header";
    stickyDateHeader.className = "sticky-date";
    messageList.parentElement.insertBefore(stickyDateHeader, messageList);
    messageList.addEventListener('scroll', updateStickyDate);
  }

  const isAtBottom = messageList.scrollHeight - messageList.scrollTop <= messageList.clientHeight + 150;
  const anchor = !forceScroll
    ? Array.from(messageList.querySelectorAll('.message')).find((node) => node.offsetTop + node.offsetHeight >= messageList.scrollTop)
    : null;
  const anchorOffset = anchor ? anchor.offsetTop - messageList.scrollTop : 0;

  if (forceScroll) {
    messageList.innerHTML = "";
    renderedMessageIds.clear();
    lastDateString = null;
  }

  if (messages.length === 0) {
    if (messageList.innerHTML === "") {
      messageList.innerHTML = '<div class="empty-state">Сообщений пока нет</div>';
    }
    messageList.classList.remove('is-loading');
    return;
  }

  // Remove empty state if messages exist
  const emptyState = messageList.querySelector('.empty-state');
  if (emptyState) emptyState.remove();

  let filtered = [...messages];
  if (messageSearchQuery) {
    const query = messageSearchQuery.toLowerCase();
    filtered = filtered.filter(msg => {
      const textMatch = (msg.text || "").toLowerCase().includes(query);
      const captionMatch = (msg.media_caption || "").toLowerCase().includes(query);
      return textMatch || captionMatch;
    });
  }

  document.getElementById('message-search-empty')?.remove();
  const visibleIds = new Set(filtered.map((msg) => String(msg.id)));
  messageList.querySelectorAll('.message').forEach((node) => {
    node.hidden = Boolean(messageSearchQuery) && !visibleIds.has(String(node.dataset.msgId));
  });

  if (filtered.length === 0 && messageSearchQuery) {
    const empty = document.createElement('div');
    empty.id = 'message-search-empty';
    empty.className = 'empty-state';
    empty.textContent = `По запросу «${messageSearchQuery}» ничего не найдено`;
    messageList.appendChild(empty);
    return;
  }

  const sorted = filtered.sort((a, b) => new Date(a.telegram_date) - new Date(b.telegram_date));

  sorted.forEach((msg) => {
    // Adopt the optimistic card in place. This prevents a remove/append jump when
    // the provider assigns the real message id or changes delivery status.
    const deliveryId = msg?.metadata?.delivery_id;
    if (deliveryId) {
      const syntheticId = `delivery-${deliveryId}`;
      const pending = messageList.querySelector(`.message[data-msg-id="${syntheticId}"]`);
      if (pending) {
        pending.dataset.msgId = String(msg.id);
        pending.classList.remove('animate-in');
        pending.hidden = false;
        renderedMessageIds.delete(syntheticId);
        renderedMessageIds.add(msg.id);
      }
    }

    // Existing message nodes are stable; only the status icon changes.
    if (renderedMessageIds.has(msg.id)) {
      const existing = messageList.querySelector(`.message[data-msg-id="${msg.id}"]`);
      if (existing) {
        existing.hidden = false;
        const icon = existing.querySelector('.status-icon');
        if (icon) {
          icon.textContent = getStatusIcon(msg);
          icon.classList.toggle('status-icon--read', getStatusClass(msg).includes('status-icon--read'));
        }
      }
      return;
    }

    const div = document.createElement("div");
    // Only animate if it's a new message during polling (not first load of chat)
    const animateClass = (!forceScroll && renderedMessageIds.size > 0) ? "animate-in" : "";
    div.className = `message ${msg.is_outgoing ? "message--outgoing" : "message--incoming"} ${msg.message_type === 'sticker' ? 'message--sticker' : ''} ${animateClass}`;
    div.dataset.msgId = msg.id;

    let content = `<div class="message__text">${escapeHtml(msg.text || "")}</div>`;
    if (msg.reply_to_preview) {
      content = `<div class="message__reply-preview">${escapeHtml(msg.reply_to_preview)}</div>` + content;
    }

    let timeStr = "";
    try {
      if (msg.telegram_date) {
        timeStr = new Date(msg.telegram_date).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
    } catch (e) { console.error("Invalid date", msg.telegram_date); }

    if (msg.media_file_path) {
      const mediaUrl = `/media/${msg.media_file_path}`;
      const safeUrl = mediaUrl.replace(/'/g, "\\'");

      if (msg.message_type === 'photo' || msg.message_type === 'sticker') {
        const extension = (msg.media_file_name || msg.media_file_path || '').split('.').pop().toLowerCase();
        if (msg.message_type === 'sticker' && extension === 'webm') {
          content = `<div class="message__media message__media--sticker"><video class="sticker-video" src="${mediaUrl}" autoplay loop muted playsinline></video></div>` + content;
        } else if (msg.message_type === 'sticker' && extension === 'tgs') {
          content = `<div class="message__media message__media--sticker tgs-sticker" data-src="${mediaUrl}"></div>` + content;
        } else {
          content = `<div class="message__media ${msg.message_type === 'sticker' ? 'message__media--sticker' : ''}"><img src="${mediaUrl}" class="message__media--image" onclick="openMediaViewer('${safeUrl}', 'image')" style="cursor: pointer; max-width: 100%;"></div>` + content;
        }
      } else if (msg.message_type === 'video') {
        content = `<div class="message__media" onclick="openMediaViewer('${safeUrl}', 'video')" style="cursor: pointer; position: relative;">
                             <video src="${mediaUrl}" style="max-width: 100%;"></video>
                             <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 30px;">▶️</div>
                           </div>` + content;
      } else if (msg.message_type === 'voice' || msg.message_type === 'audio') {
        content = `<div class="message__media message__media--audio"><audio class="message__audio" controls preload="metadata" src="${mediaUrl}"></audio></div>` + content;
      } else {
        content = `<div class="message__media"><a href="${mediaUrl}" download="${escapeHtml(msg.media_file_name || '')}" class="message__media--document"><i class="material-icons" style="font-size:16px;">description</i> ${escapeHtml(msg.media_file_name || 'Файл')}</a></div>` + content;
      }
    } else if (['photo', 'video', 'voice', 'audio', 'document', 'sticker'].includes(msg.message_type)) {
      content = `<div class="message__media"><button class="media-download-button" onclick="downloadViaApi(${msg.id}, this)">Загрузить ${escapeHtml(msg.message_type_display || 'файл')}</button></div>` + content;
    }

    div.innerHTML = `
        <button type="button" class="message-reply-button" aria-label="Ответить" title="Ответить"><i class="material-icons">reply</i></button>
        ${content}
        <span class="message__time">${timeStr}
           ${msg.is_outgoing ? `<span class="status-icon${getStatusClass(msg)}">${getStatusIcon(msg)}</span>` : ''}
        </span>
      `;
    const replyButton = div.querySelector('.message-reply-button');
    if (String(msg.id).startsWith('delivery-')) replyButton?.remove();
    else replyButton?.addEventListener('click', () => setReplyTarget(msg));
    div.hidden = Boolean(messageSearchQuery) && !visibleIds.has(String(msg.id));
    messageList.appendChild(div);
    div.querySelectorAll('.tgs-sticker').forEach(renderTgsSticker);
    renderedMessageIds.add(msg.id);
  });

  rebuildMessageTimeline(messageList, sorted);
  messageList.classList.remove('is-loading');

  if (forceScroll) {
    messageList.scrollTop = messageList.scrollHeight;
  } else if (isAtBottom) {
    messageList.scrollTo({top: messageList.scrollHeight, behavior: 'smooth'});
  } else if (anchor?.isConnected) {
    messageList.scrollTop = anchor.offsetTop - anchorOffset;
  }
};


const removePendingDelivery = (deliveryId) => {
  if (!deliveryId) return;
  const syntheticId = "delivery-" + deliveryId;
  document.querySelector('.message[data-msg-id="' + syntheticId + '"]')?.remove();
  renderedMessageIds.delete(syntheticId);
};
window.downloadViaApi = async (msgId, button) => {
  const originalLabel = button?.textContent;
  try {
    if (button) {
      button.disabled = true;
      button.textContent = 'Загрузка…';
    }
    setStatus('Загружаем файл…');
    const response = await fetch(`${apiBase}/messages/${msgId}/download_media/`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    await window.fetchMessagesGlobal?.(true);
    setStatus('Файл загружен');
  } catch (error) {
    setError(error.message || 'Не удалось загрузить файл.');
    setStatus('');
    if (button) {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  }
};const setActiveChat = (chat) => {
  const accountType = getAccountType(chat);
  const active = document.getElementById("active-chat");
  if (active) active.textContent = chat ? getChatName(chat) : "Выберите диалог";
  setStatus(chat ? getAccountLabel(chat) + " · " + (chat.telegram_account?.name || (accountType === "max" ? "MAX" : accountType === "whatsapp" ? "WhatsApp" : "Telegram")) : "Сообщения выбранного чата появятся здесь");
  setComposerEnabled(Boolean(chat) && isChatInCurrentScope(chat));
  const historyButton = document.getElementById('import-history-btn');
  if (historyButton) historyButton.hidden = !chat || getAccountType(chat) === 'bot';
};
const validateSelectedFile = (file) => {
  if (!file || file.size === 0) throw new Error('Нельзя загрузить пустой файл.');
  if (file.size > 100 * 1024 * 1024) throw new Error(`Файл «${file.name}» превышает лимит 100 МБ.`);
};

const renderUploadPreview = () => {
  const preview = document.getElementById('upload-preview');
  const list = document.getElementById('upload-preview-list');
  if (!preview || !list) return;
  list.replaceChildren();
  currentMedia.forEach((media, index) => {
    const chip = document.createElement('div');
    chip.className = 'upload-preview-content';
    chip.innerHTML = `<i class="material-icons">description</i><span class="upload-name"></span><span class="upload-size"></span><button type="button" class="icon-btn close-preview" aria-label="Убрать файл"><i class="material-icons">close</i></button>`;
    chip.querySelector('.upload-name').textContent = media.name;
    chip.querySelector('.upload-size').textContent = formatBytes(media.size);
    chip.querySelector('button').addEventListener('click', () => clearUpload(index));
    list.appendChild(chip);
  });
  preview.hidden = currentMedia.length === 0;
};

const uploadSingleFile = async (file) => {
  validateSelectedFile(file);
  const formData = new FormData();
  formData.append('file', file, file.name);
  const response = await fetch(`${apiBase}/upload/`, {
    method: 'POST',
    body: formData,
    headers: {'X-CSRFToken': getCsrfToken()},
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Не удалось загрузить «${file.name}» (HTTP ${response.status}).`);
  return {
    path: data.file_path,
    name: data.file_name,
    contentType: data.content_type,
    size: data.file_size,
  };
};

const handleFilesUpload = async (fileList) => {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const remaining = 10 - currentMedia.length;
  if (remaining <= 0) {
    setError('К одному сообщению можно прикрепить не более 10 файлов.');
    return;
  }
  const selected = files.slice(0, remaining);
  if (selected.length < files.length) setError('Добавлены первые 10 файлов. Остальные не выбраны.');

  setStatus(`Загружаем файлы: 0/${selected.length}`);
  let completed = 0;
  const results = await Promise.allSettled(selected.map(async (file) => {
    const result = await uploadSingleFile(file);
    completed += 1;
    setStatus(`Загружаем файлы: ${completed}/${selected.length}`);
    return result;
  }));
  const errors = [];
  results.forEach((result) => {
    if (result.status === 'fulfilled') currentMedia.push(result.value);
    else errors.push(result.reason?.message || 'Неизвестная ошибка загрузки.');
  });
  renderUploadPreview();
  setStatus('');
  if (errors.length) setError(errors.join(' '));
};

const clearUpload = (index = null) => {
  if (Number.isInteger(index)) currentMedia.splice(index, 1);
  else currentMedia = [];
  const input = document.getElementById('media-upload');
  if (input) input.value = '';
  renderUploadPreview();
};

const EMOJIS = [...'😀 😃 😄 😁 😆 😅 😂 😊 🙂 🙃 😉 😍 🥰 😘 😎 🤓 🤔 🤗 🤭 🤫 😴 😢 😭 😡 🤯 👍 👎 👌 🙏 👏 💪 ❤️ 🧡 💛 💚 💙 💜 🖤 🤍 🔥 🎉 ✅ ❌ ⚠️ 💬 📎 📷 🎥 🎵 🚀'.split(' ')];
const insertEmoji = (input, emoji) => {
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? start;
  input.setRangeText(emoji, start, end, 'end');
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.focus();
};
document.addEventListener("DOMContentLoaded", () => {
  const openModal = (id) => { const modal = document.getElementById(id); if (modal) modal.hidden = false; };
  const closeModal = (id) => { const modal = document.getElementById(id); if (modal) modal.hidden = true; };
  document.querySelectorAll('[data-close-modal]').forEach((button) => button.addEventListener('click', () => closeModal(button.dataset.closeModal)));

  const twoMonthsAgo = new Date();
  twoMonthsAgo.setMonth(twoMonthsAgo.getMonth() - 2);
  const defaultSince = twoMonthsAgo.toISOString().slice(0, 10);
  const sinceInput = document.getElementById('chat-import-since');
  if (sinceInput) sinceInput.value = defaultSince;

  document.getElementById('custom-chat-date')?.addEventListener('change', (event) => {
    if (sinceInput) sinceInput.disabled = !event.target.checked;
  });
  document.getElementById('history-all')?.addEventListener('change', (event) => {
    const count = document.getElementById('history-count');
    if (count) count.disabled = event.target.checked;
  });
  document.getElementById('import-history-btn')?.addEventListener('click', () => openModal('import-history-modal'));
  document.getElementById('import-chats-btn')?.addEventListener('click', () => openModal('import-chats-modal'));

  const monitorImportJobs = (jobIds, kind) => {
    const timer = setInterval(async () => {
      try {
        const jobs = await Promise.all(jobIds.map((id) => request(`${apiBase}/history-imports/${id}/`)));
        const failed = jobs.find((job) => job.status === 'failed');
        if (failed) {
          clearInterval(timer);
          setError(failed.error || 'Не удалось загрузить историю.');
          return;
        }
        if (jobs.every((job) => job.status === 'completed')) {
          clearInterval(timer);
          const createdMessages = jobs.reduce((sum, job) => sum + Number(job.result?.created_messages || 0), 0);
          const createdChats = jobs.reduce((sum, job) => sum + Number(job.result?.created_chats || 0), 0);
          const hasProviderStats = jobs.some((job) => job.result?.available_chats !== undefined);
          const availableChats = jobs.reduce((sum, job) => sum + Number(job.result?.available_chats || 0), 0);
          if (kind === 'chats') {
            const summary = hasProviderStats
              ? `За выбранный период найдено диалогов: ${availableChats}. Добавлено новых: ${createdChats}, сообщений: ${createdMessages}.`
              : `Добавлено чатов: ${createdChats}, сообщений: ${createdMessages}.`;
            showNotification('Чаты загружены', summary, 7000);
            await fetchChats();
          } else {
            showNotification('История загружена', `Добавлено сообщений: ${createdMessages}.`, 7000);
            await fetchMessages(false);
          }
          setStatus('Загрузка завершена');
        }
      } catch (error) {
        clearInterval(timer);
        setError(error.message);
      }
    }, 2000);
  };

  document.getElementById('import-history-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!currentChatId) return;
    const loadAll = document.getElementById('history-all')?.checked;
    const count = Number(document.getElementById('history-count')?.value || 100);
    try {
      const job = await request(`${apiBase}/chats/${currentChatId}/import_history/`, {method: 'POST', body: JSON.stringify({all: loadAll, count})});
      messageFetchLimit = loadAll ? 10000 : Math.max(100, count);
      closeModal('import-history-modal');
      setStatus('Загружаем прошлые сообщения…');
      showNotification('Загрузка началась', loadAll ? 'Загружаем всю доступную историю в фоне.' : `Загружаем до ${count} сообщений в фоне.`, 5000);
      monitorImportJobs([job.id], 'history');
    } catch (error) { setError(error.message); }
  });

  document.getElementById('import-chats-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const custom = document.getElementById('custom-chat-date')?.checked;
    try {
      const result = await request(`${apiBase}/accounts/import_chats/`, {method: 'POST', body: JSON.stringify({messenger: currentMessenger, since: custom ? sinceInput?.value : defaultSince})});
      closeModal('import-chats-modal');
      setStatus('Загружаем личные чаты…');
      showNotification('Загрузка началась', 'Личные чаты и по 5 последних сообщений загружаются в фоне.', 5000);
      monitorImportJobs((result.jobs || []).map((job) => job.id), 'chats');
    } catch (error) { setError(error.message); }
  });

  const fetchChats = async () => {
    try {
      const response = await fetch(`${apiBase}/chats/`);
      const data = await response.json();
      const chats = normalizeList(data);
      allChats = chats;
      renderAccountHealth(chats);
      renderChats(chats);
      const orderedChats = orderChats(chats);
      if (orderedChats.length > 0 && !currentChatId) {
        selectChat(orderedChats[0].id);
      }
    } catch (e) {
      console.error("Critical error in fetchChats:", e);
      lastAccountsSnapshot = ""; // Reset on critical error too
    }
  };

  window.activateAccount = async (id, name) => {
    try {
      showNotification('Информация', `Пожалуйста, подождите, аккаунт ${name} активируется...`, 3000);
      setStatus(`Activating ${name}...`);

      // Force immediate visual update if possible
      const btn = event?.target;
      if (btn && btn.tagName === 'BUTTON') {
        const card = btn.closest('.account-notification-card');
        if (card) {
          card.classList.add('authenticating');
          card.querySelector('.account-notification-header span').textContent = `${name}: Активация...`;
          card.querySelector('.material-icons').textContent = 'sync';
          card.querySelector('.material-icons').classList.add('spin');
          btn.remove();
          const progress = document.createElement('div');
          progress.className = 'activation-progress';
          progress.textContent = 'Пожалуйста, подождите...';
          card.appendChild(progress);
        }
      }

      await request(`${apiBase}/accounts/${id}/start/`, { method: 'POST' });
      // Reset notification tracking for this account immediately
      notifiedAccounts.delete(name);
      // Next poll will confirm authenticating status
      fetchChats();
    } catch (e) {
      setError(e.message);
    } finally {
      setStatus("Online");
    }
  };

  const fetchMessages = async (forceScroll = false) => {
    if (!currentChatId) return;
    const requestedChatId = currentChatId;
    const requestVersion = messageRequestVersion;
    try {
      const searchParam = messageSearchQuery ? `&search=${encodeURIComponent(messageSearchQuery)}` : "";
      const resp = await request(`${apiBase}/messages/by_chat/?chat_id=${requestedChatId}&page_size=${messageFetchLimit}${searchParam}`);
      if (requestVersion !== messageRequestVersion || Number(requestedChatId) !== Number(currentChatId)) return;
      const msgs = normalizeList(resp);


      // Better snapshot: id + status + updated_at + text length + media path
      const snapshot = msgs.map(m => `${m.id}-${m.status}-${m.updated_at}-${(m.text || '').length}-${m.media_file_path || ''}`).join('|') + `[search:${messageSearchQuery}]`;

      if (snapshot !== lastContentSnapshot || forceScroll) {
        renderMessages(msgs, forceScroll);
        lastContentSnapshot = snapshot;
      }
    } catch (e) {
      if (requestVersion !== messageRequestVersion) return;
      document.getElementById('message-list')?.classList.remove('is-loading');
      console.error('Failed to load messages', e);
    }
  };

  window.fetchMessagesGlobal = fetchMessages;

  document.getElementById('archive-toggle')?.addEventListener('click', () => setArchiveMode(!archiveMode));
  document.getElementById('reply-cancel')?.addEventListener('click', clearReplyTarget);
  document.getElementById('chat-archive-action')?.addEventListener('click', () => {
    const chat = allChats.find((item) => Number(item.id) === Number(contextChatId));
    if (chat) changeChatArchiveState(chat);
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('#chat-context-menu')) closeChatContextMenu();
  });
  window.addEventListener('blur', closeChatContextMenu);
  window.addEventListener('resize', closeChatContextMenu);

  document.documentElement.dataset.messenger = currentMessenger;
  document.querySelectorAll(".messenger-option").forEach((button) => {
    const isActive = button.dataset.messenger === currentMessenger;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
    button.addEventListener("click", () => {
      if (button.dataset.messenger === currentMessenger) return;
      currentMessenger = button.dataset.messenger;
      localStorage.setItem("messenger", currentMessenger);
      document.documentElement.dataset.messenger = currentMessenger;
      document.querySelectorAll(".messenger-option").forEach((option) => {
        const active = option === button;
        option.classList.toggle("active", active);
        option.setAttribute("aria-pressed", String(active));
      });
      resetConversation();
      renderAccountHealth(allChats);
      renderChats(allChats);
      const first = orderChats(allChats)[0];
      if (first) selectChat(first.id);
    });
  });
  // Attachments and emoji
  const fileInput = document.getElementById('media-upload');
  const attachBtn = document.getElementById('attach-btn');
  const clearBtn = document.getElementById('clear-upload');
  const emojiBtn = document.getElementById('emoji-btn');
  const emojiPicker = document.getElementById('emoji-picker');
  const messageInput = document.getElementById('message-input');

  attachBtn?.addEventListener('click', () => fileInput?.click());
  fileInput?.addEventListener('change', async (event) => {
    await handleFilesUpload(event.target.files);
    event.target.value = '';
  });
  clearBtn?.addEventListener('click', () => clearUpload());

  if (emojiPicker && emojiBtn && messageInput) {
    EMOJIS.forEach((emoji) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'emoji-option';
      option.textContent = emoji;
      option.setAttribute('aria-label', `Вставить ${emoji}`);
      option.addEventListener('click', () => insertEmoji(messageInput, emoji));
      emojiPicker.appendChild(option);
    });
    emojiBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      emojiPicker.hidden = !emojiPicker.hidden;
      emojiBtn.setAttribute('aria-expanded', String(!emojiPicker.hidden));
    });
    emojiPicker.addEventListener('click', (event) => event.stopPropagation());
    document.addEventListener('click', () => { emojiPicker.hidden = true; });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') emojiPicker.hidden = true;
    });
  }
  document.getElementById("send-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById('message-input');
    const text = input.value.trim();
    if (!currentChatId || (!text && currentMedia.length === 0)) return;

    const pendingMedia = currentMedia.map((media) => ({...media}));
    const payload = {text, media_paths: pendingMedia.map((media) => media.path)};
    const selectedReply = replyTarget;
    const endpoint = selectedReply
      ? `${apiBase}/messages/${selectedReply.id}/reply/`
      : `${apiBase}/chats/${currentChatId}/send_message/`;
    try {
      const result = await request(endpoint, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      input.value = '';
      clearUpload();
      clearReplyTarget();
      const deliveries = result.deliveries || [{id: result.delivery_id, media_path: pendingMedia[0]?.path || null}];
      const optimistic = deliveries.filter((item) => item.id).map((delivery, index) => {
        const media = pendingMedia[index];
        return {
          id: `delivery-${delivery.id}`,
          text: index === 0 ? text : '',
          message_type: media ? getMediaTypeFromMime(media.contentType) : 'text',
          media_file_path: media?.path || null,
          media_file_name: media?.name || null,
          status: 'pending',
          metadata: {delivery_id: delivery.id},
          reply_to_preview: selectedReply ? (selectedReply.text || selectedReply.media_caption || '[Медиа]') : null,
          is_outgoing: true,
          telegram_date: new Date(Date.now() + index).toISOString(),
        };
      });
      renderMessages(optimistic, false);
    } catch (error) {
      setError(localizeError(error.message));
    }
  });
  // Search filter (client side simple)
  document.getElementById("chat-search")?.addEventListener("input", (e) => {
    const val = e.target.value.trim().toLowerCase();
    document.querySelectorAll('#chat-list .chat-item').forEach(li => {
      li.hidden = Boolean(val) && !li.dataset.searchText.includes(val);
    });
  });

  // Message search filter
  const searchBtn = document.getElementById("message-search-btn");
  const searchContainer = document.getElementById("message-search-container");
  const searchInput = document.getElementById("message-search-input");
  const searchClose = document.getElementById("message-search-close");

  if (searchBtn && searchContainer && searchInput) {
    searchBtn.addEventListener("click", () => {
      const isVisible = !searchContainer.hidden;
      searchContainer.hidden = isVisible;
      searchBtn.setAttribute('aria-expanded', String(!isVisible));
      if (!isVisible) searchInput.focus();
      else {
        messageSearchQuery = "";
        searchInput.value = "";
        fetchMessages(true);
      }
    });

    let searchTimer = null;
    searchInput.addEventListener("input", (e) => {
      messageSearchQuery = e.target.value.trim();
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => fetchMessages(!messageSearchQuery), 180);
    });

    searchClose.addEventListener("click", () => {
      searchContainer.hidden = true;
      searchBtn.setAttribute('aria-expanded', 'false');
      messageSearchQuery = "";
      searchInput.value = "";
      fetchMessages(true);
    });
  }

  // Server-driven updates. HTTP polling remains only as a slow safety net.
  let realtimeSocket = null;
  let reconnectAttempt = 0;
  let reconnectTimer = null;
  let refreshTimer = null;

  const scheduleRealtimeRefresh = (chatId = null) => {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => {
      fetchChats();
      if (!chatId || Number(chatId) === Number(currentChatId)) {
        fetchMessages(false);
      }
    }, 80);
  };

  const connectRealtime = () => {
    if (realtimeSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(realtimeSocket.readyState)) return;
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    realtimeSocket = new WebSocket(`${protocol}://${window.location.host}/ws/messages/`);

    realtimeSocket.addEventListener("open", () => {
      reconnectAttempt = 0;
      clearTimeout(reconnectTimer);
      scheduleRealtimeRefresh(currentChatId);
    });

    realtimeSocket.addEventListener("message", (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "new_message") {
          scheduleRealtimeRefresh(payload.message?.chat || payload.message?.chat_id);
        } else if (payload.type === "chat_updated") {
          scheduleRealtimeRefresh(payload.chat?.id);
        } else if (payload.type === "delivery_updated") {
          const pending = document.querySelector(`.message[data-msg-id="delivery-${payload.delivery?.id}"]`);
          const icon = pending?.querySelector('.status-icon');
          if (icon) icon.textContent = payload.delivery?.status === "failed" ? "⚠" : payload.delivery?.status === "sent" ? "✓" : "◷";
          if (payload.delivery?.status === "sent") {
            scheduleRealtimeRefresh(payload.delivery.chat_id);
          } else if (payload.delivery?.status === "failed") {
            setError(payload.delivery.last_error || "Не удалось отправить сообщение. Повторите попытку.");
          }
        } else if (payload.type === "initial_chats" || payload.type === "initial_chats_end") {
          scheduleRealtimeRefresh(currentChatId);
        }
      } catch (error) {
        console.error("Invalid realtime event", error);
      }
    });

    realtimeSocket.addEventListener("close", () => {
      realtimeSocket = null;
      const delay = Math.min(30000, 1000 * (2 ** reconnectAttempt));
      reconnectAttempt += 1;
      reconnectTimer = setTimeout(connectRealtime, delay);
    });

    realtimeSocket.addEventListener("error", () => {
      if (realtimeSocket) realtimeSocket.close();
    });
  };

  // The fallback only reconciles CRM state; it never triggers Telegram API calls.
  const startFallbackPolling = () => {
    setInterval(() => {
      if (document.hidden) return;
      fetchChats();
      fetchMessages(false);
    }, 60000);
  };

  window.addEventListener("online", connectRealtime);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      scheduleRealtimeRefresh(currentChatId);
      connectRealtime();
    }
  });

  fetchChats();
  connectRealtime();
  startFallbackPolling();
});
