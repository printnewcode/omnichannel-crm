const apiBase = "/api";
const notifiedAccounts = new Set();
const accountStartPending = new Map();
const accountStartFailures = new Map();
let accountHealthAccounts = [];
let accountHealthOpen = false;

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
      const requestError = new Error(rawError);
      requestError.data = errorData;
      throw requestError;
    }
    if (response.status === 204) return null;
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

const renderSpecialMessage = (special) => {
  if (!special || !special.kind) return "";
  if (special.kind === 'location') {
    const latitude = Number(special.latitude);
    const longitude = Number(special.longitude);
    const hasPoint = Number.isFinite(latitude) && Number.isFinite(longitude);
    const title = special.name || special.address || 'Геопозиция';
    const details = [special.address, hasPoint ? `${latitude.toFixed(5)}, ${longitude.toFixed(5)}` : 'Координаты недоступны']
      .filter((value, index, values) => value && values.indexOf(value) === index)
      .map(value => `<span>${escapeHtml(value)}</span>`).join('');
    const mapLink = hasPoint
      ? `<a class="message-special__action" href="https://yandex.ru/maps/?pt=${longitude},${latitude}&z=16&l=map" target="_blank" rel="noopener noreferrer">Открыть на карте</a>`
      : '';
    return `<div class="message-special message-special--location"><i class="material-icons">location_on</i><div><strong>${escapeHtml(title)}</strong>${details}${mapLink}</div></div>`;
  }
  if (special.kind === 'contact') {
    const contacts = Array.isArray(special.contacts) ? special.contacts : [];
    const rows = contacts.length ? contacts.map(contact => {
      const phone = String(contact.phone || '');
      const safePhone = phone.replace(/[^+\d]/g, '');
      return `<div class="message-special__contact"><i class="material-icons">person</i><div><strong>${escapeHtml(contact.name || 'Контакт')}</strong>${phone ? `<a href="tel:${safePhone}">${escapeHtml(phone)}</a>` : '<span>Номер не указан</span>'}</div></div>`;
    }).join('') : '<span>Данные контакта недоступны</span>';
    return `<div class="message-special message-special--contact">${rows}</div>`;
  }
  if (special.kind === 'poll') {
    const options = Array.isArray(special.options) ? special.options : [];
    const rows = options.map(option => `<div class="message-special__poll-option"><span></span>${escapeHtml(option)}</div>`).join('');
    const update = special.is_update ? '<small>Обновление результатов</small>' : '';
    return `<div class="message-special message-special--poll"><div class="message-special__poll-title"><i class="material-icons">poll</i><strong>${escapeHtml(special.question || 'Опрос')}</strong></div>${rows || '<span>Варианты ответа недоступны</span>'}${update}</div>`;
  }
  if (special.kind === 'group_invite') {
    const rawUrl = String(special.url || '');
    const safeUrl = /^https?:\/\//i.test(rawUrl) ? rawUrl : '';
    return `<div class="message-special"><i class="material-icons">group_add</i><div><strong>${escapeHtml(special.title || 'Приглашение в группу')}</strong>${safeUrl ? `<a class="message-special__action" href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">Открыть приглашение</a>` : ''}</div></div>`;
  }
  if (special.kind === 'dice') {
    return `<div class="message-special message-special--dice"><strong>${escapeHtml(special.emoji || '🎲')}</strong>${special.value ? `<span>Выпало: ${escapeHtml(special.value)}</span>` : ''}</div>`;
  }
  if (special.kind === 'service') {
    return `<div class="message-special message-special--service"><i class="material-icons">info</i><span>${escapeHtml(special.label || 'Служебное событие')}</span></div>`;
  }
  if (special.kind === 'reaction') {
    return `<div class="message-special message-special--service"><i class="material-icons">add_reaction</i><span>Реакция ${escapeHtml(special.emoji || '')}</span></div>`;
  }
  return `<div class="message-special message-special--unsupported"><i class="material-icons">help_outline</i><span>${escapeHtml(special.label || 'Неподдерживаемый тип сообщения')}</span></div>`;
};

const createIdempotencyKey = () => {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else bytes.forEach((_, index) => { bytes[index] = Math.floor(Math.random() * 256); });
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
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
let messageFetchLimit = 50;
let messageFetchController = null;
let chatFetchPromise = null;
let chatRefreshQueued = false;
let chatNextUrl = null;
let chatArchiveCount = 0;
let chatFetchGeneration = 0;
let sendInFlight = false;
let aiRuntime = null;

const getChatName = (chat) => chat?.display_name || chat?.title || chat?.username || chat?.first_name || "Без имени";
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

const accountTypePresentation = (account) => ({
  personal: { label: 'Telegram', icon: 'send' },
  bot: { label: 'Telegram-бот', icon: 'smart_toy' },
  whatsapp: { label: 'WhatsApp', icon: 'chat' },
  max: { label: 'MAX', icon: 'forum' },
}[account.account_type] || { label: account.account_type_display || 'Мессенджер', icon: 'forum' });

const accountHealthReason = (account) => {
  const id = String(account.id);
  if (accountStartFailures.has(id)) return accountStartFailures.get(id);
  if (accountStartPending.has(id)) return 'Запускаем подключение…';
  if (account.last_error) return account.last_error;
  if (account.status === 'inactive') return 'Аккаунт выключен.';
  if (account.status === 'authenticating') return 'Ожидается завершение авторизации.';
  return account.status_display || 'Аккаунт недоступен.';
};

const renderAccountHealth = (accounts = accountHealthAccounts) => {
  const banner = document.getElementById("account-health");
  if (!banner) return;
  const visibleAccounts = accounts.filter((account) => (
    account.status !== 'active'
    || accountStartPending.has(String(account.id))
    || accountStartFailures.has(String(account.id))
  ));
  banner.hidden = visibleAccounts.length === 0;
  banner.replaceChildren();
  if (!visibleAccounts.length) {
    accountHealthOpen = false;
    return;
  }

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'header-icon-button account-health-trigger';
  trigger.setAttribute('aria-label', `Не работают аккаунты: ${visibleAccounts.length}`);
  trigger.setAttribute('aria-expanded', String(accountHealthOpen));
  trigger.dataset.tooltip = `Не работают аккаунты: ${visibleAccounts.length}`;
  trigger.title = `Не работают аккаунты: ${visibleAccounts.length}`;
  const icon = document.createElement('i');
  icon.className = 'material-icons';
  icon.textContent = 'warning_amber';
  const badge = document.createElement('span');
  badge.className = 'account-health-badge';
  badge.textContent = String(visibleAccounts.length);
  trigger.append(icon, badge);

  const popover = document.createElement('div');
  popover.className = 'account-health-popover';
  popover.hidden = !accountHealthOpen;

  const heading = document.createElement('div');
  heading.className = 'account-health-heading';
  const headingCopy = document.createElement('div');
  const title = document.createElement('strong');
  title.textContent = 'Подключения требуют внимания';
  const subtitle = document.createElement('span');
  subtitle.textContent = `Не работают аккаунты: ${visibleAccounts.length}`;
  headingCopy.append(title, subtitle);
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'account-health-close';
  close.setAttribute('aria-label', 'Закрыть');
  close.innerHTML = '<i class="material-icons">close</i>';
  heading.append(headingCopy, close);

  const list = document.createElement('div');
  list.className = 'account-health-list';
  visibleAccounts.forEach((account) => {
    const id = String(account.id);
    const presentation = accountTypePresentation(account);
    const row = document.createElement('div');
    row.className = `account-health-row status-${account.status}`;

    const providerIcon = document.createElement('span');
    providerIcon.className = `account-health-provider account-health-provider--${account.account_type}`;
    const providerIconGlyph = document.createElement('i');
    providerIconGlyph.className = 'material-icons';
    providerIconGlyph.textContent = presentation.icon;
    providerIcon.append(providerIconGlyph);

    const copy = document.createElement('div');
    copy.className = 'account-health-copy';
    const name = document.createElement('strong');
    name.textContent = account.name || 'Аккаунт без названия';
    const meta = document.createElement('span');
    meta.textContent = `${presentation.label} · ${accountHealthReason(account)}`;
    copy.append(name, meta);

    const actions = document.createElement('div');
    actions.className = 'account-health-actions';
    if (account.can_start || accountStartFailures.has(id)) {
      const start = document.createElement('button');
      start.type = 'button';
      start.className = 'account-health-start';
      start.disabled = accountStartPending.has(id);
      start.innerHTML = `<i class="material-icons">${start.disabled ? 'sync' : 'play_arrow'}</i><span>${start.disabled ? 'Запускаем…' : 'Запустить'}</span>`;
      start.addEventListener('click', () => startMessengerAccount(account));
      actions.append(start);
    }
    if (account.admin_url && (accountStartFailures.has(id) || account.status === 'error')) {
      const adminLink = document.createElement('a');
      adminLink.className = 'account-health-admin';
      adminLink.href = account.admin_url;
      adminLink.target = '_blank';
      adminLink.rel = 'noopener';
      adminLink.textContent = 'Открыть админку';
      actions.append(adminLink);
    }
    const content = document.createElement('div');
    content.className = 'account-health-content';
    content.append(copy, actions);
    row.append(providerIcon, content);
    list.append(row);
  });
  popover.append(heading, list);
  banner.append(trigger, popover);

  const setOpen = (open) => {
    accountHealthOpen = open;
    popover.hidden = !open;
    trigger.setAttribute('aria-expanded', String(open));
  };
  trigger.addEventListener('click', (event) => {
    event.stopPropagation();
    setOpen(!accountHealthOpen);
  });
  close.addEventListener('click', () => setOpen(false));
};

document.addEventListener('click', (event) => {
  const banner = document.getElementById('account-health');
  if (accountHealthOpen && banner && !banner.contains(event.target)) {
    accountHealthOpen = false;
    const popover = banner.querySelector('.account-health-popover');
    const trigger = banner.querySelector('.account-health-trigger');
    if (popover) popover.hidden = true;
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
  }
});

const fetchAccountHealth = async ({ quiet = false } = {}) => {
  try {
    const payload = await request(`${apiBase}/accounts/health/`);
    accountHealthAccounts = Array.isArray(payload.accounts) ? payload.accounts : [];
    accountHealthAccounts.forEach((account) => {
      if (account.status === 'active' && !accountStartPending.has(String(account.id))) {
        accountStartFailures.delete(String(account.id));
      }
    });
    renderAccountHealth();
    return accountHealthAccounts;
  } catch (error) {
    if (!quiet) console.error('Account health check failed', error);
    return accountHealthAccounts;
  }
};

const waitForAccountStart = async (account, requestedAt) => {
  const id = String(account.id);
  const startedAt = new Date(requestedAt).getTime();
  for (let attempt = 0; attempt < 13; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const accounts = await fetchAccountHealth({ quiet: true });
    const fresh = accounts.find((item) => String(item.id) === id);
    if (!fresh) break;
    if (fresh.status === 'error') {
      throw new Error(fresh.last_error || 'Фоновый процесс не смог подключить аккаунт.');
    }
    const activityAt = fresh.last_activity ? new Date(fresh.last_activity).getTime() : 0;
    if (fresh.status === 'active' && activityAt >= startedAt) return;
  }
  throw new Error('Подключение не подтвердилось вовремя. Проверьте аккаунт в админке.');
};

const startMessengerAccount = async (account) => {
  const id = String(account.id);
  if (accountStartPending.has(id)) return;
  accountStartFailures.delete(id);
  accountStartPending.set(id, true);
  renderAccountHealth();
  try {
    const result = await request(`${apiBase}/accounts/${account.id}/start/`, { method: 'POST' });
    if (result.status === 'starting' && result.requested_at) {
      await waitForAccountStart(account, result.requested_at);
    }
    accountStartPending.delete(id);
    accountStartFailures.delete(id);
    await fetchAccountHealth({ quiet: true });
    showNotification('Готово', `Аккаунт «${account.name}» запущен.`, 3500);
  } catch (error) {
    accountStartPending.delete(id);
    const message = `${getRussianError(error.message)} Зайдите в админку и проверьте настройки аккаунта.`;
    accountStartFailures.set(id, message);
    if (error.data?.account) {
      accountHealthAccounts = accountHealthAccounts.map((item) => (
        String(item.id) === id ? error.data.account : item
      ));
    }
    renderAccountHealth();
    showNotification('Ошибка запуска', message, 8000);
  }
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
  localStorage.setItem(
    `last-chat:${currentMessenger}:${archiveMode ? 'archive' : 'active'}`,
    String(id),
  );
  messageFetchLimit = 50;
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
  messageFetchLimit = 50;
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
  resetConversation();
  window.fetchChatsGlobal?.({reset: true});
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

const updateDisplayedChatCount = () => {
  const counter = document.getElementById('chat-count');
  if (counter) counter.textContent = String(document.querySelectorAll('#chat-list .chat-item').length);
};

const renderChats = (chats) => {
  const chatList = document.getElementById('chat-list');
  const chatCount = document.getElementById('chat-count');
  if (!chatList) return;
  const visibleChats = orderChats(chats);
  const archiveToggle = document.getElementById('archive-toggle');
  const archiveCount = document.getElementById('archive-count');
  const archiveSubtitle = document.getElementById('archive-subtitle');
  const listTitle = document.getElementById('chat-list-title');
  if (archiveToggle) {
    archiveToggle.classList.toggle('active', archiveMode);
    archiveToggle.setAttribute('aria-pressed', String(archiveMode));
  }
  if (archiveCount) archiveCount.textContent = String(chatArchiveCount);
  if (archiveSubtitle) archiveSubtitle.textContent = chatArchiveCount ? `${chatArchiveCount} ${chatArchiveCount === 1 ? 'диалог' : 'диалогов'}` : 'Нет архивных диалогов';
  if (listTitle) listTitle.textContent = archiveMode ? 'Архив' : 'Все диалоги';
  chatList.replaceChildren();
  if (chatCount) chatCount.textContent = '0';

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
      chat.system_name, chat.google_contact_name, chat.last_message_preview,
      chat.last_message, chat.telegram_account?.name,
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
    if (chat.google_contact_name && chat.system_name && chat.google_contact_name !== chat.system_name) {
      const systemName = document.createElement('span');
      systemName.className = 'chat-system-name';
      systemName.textContent = chat.system_name;
      systemName.title = 'Имя в мессенджере';
      source.appendChild(systemName);
    }
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
    const indicators = document.createElement('span');
    indicators.className = 'chat-row-indicators';
    if (chat.needs_human_attention) {
      const attention = document.createElement('span');
      attention.className = 'human-attention-indicator';
      attention.title = 'Требуется ответ администратора';
      attention.setAttribute('aria-label', 'Требуется ответ администратора');
      attention.innerHTML = '<i class="material-icons">support_agent</i>';
      indicators.appendChild(attention);
    }
    const chatAIState = getEffectiveChatAIState(chat);
    const chatAIIndicator = document.createElement('span');
    chatAIIndicator.className = `chat-ai-list-status is-${chatAIState.status}`;
    chatAIIndicator.title = chatAIState.reason;
    chatAIIndicator.setAttribute('aria-label', chatAIState.reason);
    chatAIIndicator.innerHTML = '<i class="material-icons">smart_toy</i>';
    indicators.appendChild(chatAIIndicator);
    if (chat.unread_count > 0) {
      const unread = document.createElement('span');
      unread.className = 'unread-indicator';
      unread.textContent = chat.unread_count > 99 ? '99+' : String(chat.unread_count);
      indicators.appendChild(unread);
    }
    bottom.appendChild(indicators);
    content.append(top, source, bottom);
    li.append(avatar, content);
    li.addEventListener('click', () => selectChat(chat.id));
    li.addEventListener('contextmenu', (event) => showChatContextMenu(event, chat));
    chatList.appendChild(li);
  });
  updateDisplayedChatCount();
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
const MESSAGE_REACTIONS = ['👍', '❤️', '🔥', '👏', '😁', '🎉', '😢', '🤔', '👎'];

const displayReaction = (emoji) => String(emoji || '').startsWith('custom:') ? '✨' : emoji;

const applyOptimisticReaction = (msg, emoji) => {
  const reactions = (Array.isArray(msg.reactions) ? msg.reactions : []).map((item) => ({...item}));
  if (reactions.some((item) => item.emoji === emoji && item.chosen)) return false;
  reactions.forEach((item) => {
    if (item.chosen) {
      item.chosen = false;
      item.count = Math.max(0, Number(item.count || 0) - 1);
    }
  });
  let target = reactions.find((item) => item.emoji === emoji);
  if (!target) {
    target = {emoji, count: 0, chosen: false};
    reactions.push(target);
  }
  target.count = Number(target.count || 0) + 1;
  target.chosen = true;
  msg.reactions = reactions.filter((item) => Number(item.count || 0) > 0);
  return true;
};

const submitReaction = async (msg, emoji, node) => {
  if (!msg.can_react || !applyOptimisticReaction(msg, emoji)) return;
  updateMessageReactions(node, msg);
  try {
    await request(`${apiBase}/messages/${msg.id}/react/`, {
      method: 'POST',
      body: JSON.stringify({emoji}),
    });
  } catch (error) {
    setError(error.message);
    window.fetchMessagesGlobal?.(false);
  }
};

const updateMessageReactions = (node, msg) => {
  if (!node) return;
  const row = node.querySelector('.message-reactions');
  if (row) {
    row.replaceChildren();
    (Array.isArray(msg.reactions) ? msg.reactions : []).forEach((reaction) => {
      const badge = document.createElement(msg.can_react && !String(reaction.emoji).startsWith('custom:') ? 'button' : 'span');
      if (badge.tagName === 'BUTTON') badge.type = 'button';
      badge.className = `message-reaction${reaction.chosen ? ' chosen' : ''}`;
      badge.title = String(reaction.emoji).startsWith('custom:') ? 'Пользовательская реакция Telegram' : String(reaction.emoji);
      badge.textContent = `${displayReaction(reaction.emoji)} ${Number(reaction.count || 1)}`;
      if (badge.tagName === 'BUTTON') badge.addEventListener('click', (event) => {
        event.stopPropagation();
        submitReaction(msg, reaction.emoji, node);
      });
      row.appendChild(badge);
    });
    row.hidden = row.childElementCount === 0;
  }

  const picker = node.querySelector('.reaction-picker');
  const trigger = node.querySelector('.message-reaction-button');
  if (!msg.can_react) {
    trigger?.remove();
    picker?.remove();
    return;
  }
  if (picker && picker.childElementCount === 0) {
    MESSAGE_REACTIONS.forEach((emoji) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.textContent = emoji;
      option.title = `Поставить реакцию ${emoji}`;
      option.addEventListener('click', (event) => {
        event.stopPropagation();
        picker.hidden = true;
        submitReaction(msg, emoji, node);
      });
      picker.appendChild(option);
    });
  }
  if (trigger && !trigger.dataset.bound) {
    trigger.dataset.bound = '1';
    trigger.addEventListener('click', (event) => {
      event.stopPropagation();
      document.querySelectorAll('.reaction-picker').forEach((item) => {
        if (item !== picker) item.hidden = true;
      });
      if (picker) picker.hidden = !picker.hidden;
    });
  }
};

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
const messageIdsToRerender = new Set();
const activeMediaDownloads = new Set();

const mediaDownloadState = (msg) => {
  const state = msg?.metadata?.media_download || {};
  const updatedAt = Date.parse(state.updated_at || '');
  const recent = Number.isFinite(updatedAt) && Date.now() - updatedAt < 240000;
  return {
    status: state.status || '',
    active: ['queued', 'downloading'].includes(state.status) && recent,
    failed: state.status === 'failed' || (['queued', 'downloading'].includes(state.status) && !recent),
  };
};

const messageContentVersion = (msg) => JSON.stringify([
  msg.message_type,
  msg.text || '',
  msg.media_caption || '',
  msg.media_file_path || '',
  msg.reply_to_preview || '',
  msg.special_content || null,
  msg.forward_info || null,
  msg?.metadata?.media_download || null,
]);

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

const rebuildMessageTimeline = (messageList) => {
  messageList.querySelectorAll('.date-divider').forEach((node) => node.remove());
  const messageNodes = Array.from(messageList.querySelectorAll('.message')).sort((a, b) => (
    new Date(a.dataset.messageDate || 0) - new Date(b.dataset.messageDate || 0)
  ));
  let previousDate = null;
  messageNodes.forEach((node) => {
    const dateValue = node.dataset.messageDate;
    const date = new Date(dateValue);
    const dateKey = localDateKey(dateValue);
    if (node.hidden) {
      messageList.appendChild(node);
      return;
    }
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

const renderMessages = (messages, forceScroll = false, preservePosition = false) => {
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
  const anchorId = anchor?.dataset.msgId;
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
    const contentVersion = messageContentVersion(msg);
    // Adopt the optimistic card in place. This prevents a remove/append jump when
    // the provider assigns the real message id or changes delivery status.
    const deliveryId = msg?.metadata?.delivery_id;
    if (deliveryId) {
      const syntheticId = `delivery-${deliveryId}`;
      const pending = messageList.querySelector(`.message[data-msg-id="${syntheticId}"]`);
      if (pending) {
        pending.dataset.msgId = String(msg.id);
        pending.dataset.messageDate = msg.telegram_date || pending.dataset.messageDate;
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
        if (
          messageIdsToRerender.has(String(msg.id))
          || existing.dataset.contentVersion !== contentVersion
        ) {
          existing.remove();
          renderedMessageIds.delete(msg.id);
          messageIdsToRerender.delete(String(msg.id));
        } else {
          existing.hidden = false;
          existing.dataset.messageDate = msg.telegram_date || existing.dataset.messageDate;
          const icon = existing.querySelector('.status-icon');
          if (icon) {
            icon.textContent = getStatusIcon(msg);
            icon.classList.toggle('status-icon--read', getStatusClass(msg).includes('status-icon--read'));
          }
          updateMessageReactions(existing, msg);
        }
      }
      if (renderedMessageIds.has(msg.id)) return;
    }

    const div = document.createElement("div");
    // Only animate if it's a new message during polling (not first load of chat)
    const animateClass = (!forceScroll && renderedMessageIds.size > 0) ? "animate-in" : "";
    div.className = `message ${msg.is_outgoing ? "message--outgoing" : "message--incoming"} ${msg.message_type === 'sticker' ? 'message--sticker' : ''} ${animateClass}`;
    div.dataset.msgId = msg.id;
    div.dataset.messageDate = msg.telegram_date || new Date().toISOString();
    div.dataset.contentVersion = contentVersion;

    const specialContent = renderSpecialMessage(msg.special_content);
    const showRegularText = Boolean(msg.text) && (!specialContent || msg.special_content?.kind === 'unsupported');
    let content = `${specialContent}${showRegularText ? `<div class="message__text">${escapeHtml(msg.text)}</div>` : ''}`;
    const downloadableMediaTypes = ['photo', 'video', 'voice', 'audio', 'document', 'sticker'];
    if (!content && !downloadableMediaTypes.includes(msg.message_type)) {
      content = `<div class="message__text message__text--muted">Сообщение без текста</div>`;
    }
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
    } else if (downloadableMediaTypes.includes(msg.message_type)) {
      const download = mediaDownloadState(msg);
      if (download.active) {
        content = `<div class="message__media"><button class="media-download-button media-download-button--loading" type="button" disabled><i class="material-icons spin">sync</i><span>Файл загружается…</span></button></div>` + content;
      } else {
        const label = download.failed ? 'Повторить загрузку' : 'Загрузить файл';
        content = `<div class="message__media"><button class="media-download-button" type="button" onclick="downloadViaApi(${msg.id}, this)">${label}</button></div>` + content;
      }
    }

    if (msg.forward_info?.is_forwarded) {
      const forwardedLabel = msg.forward_info.from_name
        ? `Переслано от ${escapeHtml(msg.forward_info.from_name)}`
        : 'Переслано';
      content = `<div class="message__forwarded"><i class="material-icons" aria-hidden="true">forward</i><span>${forwardedLabel}</span></div>` + content;
    }

    div.innerHTML = `
        <button type="button" class="message-reply-button" aria-label="Ответить" title="Ответить"><i class="material-icons">reply</i></button>
        <button type="button" class="message-reaction-button" aria-label="Добавить реакцию" title="Добавить реакцию"><i class="material-icons">add_reaction</i></button>
        <div class="reaction-picker" hidden></div>
        ${content}
        <div class="message-reactions" hidden></div>
        <span class="message__time">${timeStr}
           ${msg.is_outgoing ? `<span class="status-icon${getStatusClass(msg)}">${getStatusIcon(msg)}</span>` : ''}
        </span>
      `;
    const replyButton = div.querySelector('.message-reply-button');
    if (String(msg.id).startsWith('delivery-')) replyButton?.remove();
    else replyButton?.addEventListener('click', () => setReplyTarget(msg));
    updateMessageReactions(div, msg);
    div.hidden = Boolean(messageSearchQuery) && !visibleIds.has(String(msg.id));
    messageList.appendChild(div);
    div.querySelectorAll('.tgs-sticker').forEach(renderTgsSticker);
    renderedMessageIds.add(msg.id);
  });

  rebuildMessageTimeline(messageList);
  messageList.classList.remove('is-loading');

  const currentAnchor = anchorId
    ? messageList.querySelector(`.message[data-msg-id="${anchorId}"]`)
    : null;
  if (forceScroll) {
    messageList.scrollTop = messageList.scrollHeight;
  } else if (preservePosition && currentAnchor) {
    messageList.scrollTop = currentAnchor.offsetTop - anchorOffset;
  } else if (!isAtBottom && currentAnchor) {
    messageList.scrollTop = currentAnchor.offsetTop - anchorOffset;
  } else if (isAtBottom) {
    messageList.scrollTo({top: messageList.scrollHeight, behavior: 'smooth'});
  }
};


const removePendingDelivery = (deliveryId) => {
  if (!deliveryId) return;
  const syntheticId = "delivery-" + deliveryId;
  document.querySelector('.message[data-msg-id="' + syntheticId + '"]')?.remove();
  renderedMessageIds.delete(syntheticId);
};
window.downloadViaApi = async (msgId, button) => {
  const downloadKey = String(msgId);
  if (activeMediaDownloads.has(downloadKey)) return;
  activeMediaDownloads.add(downloadKey);
  setStatus(`Загружаем файлы: ${activeMediaDownloads.size}`);
  const originalLabel = button?.textContent;
  try {
    if (button) {
      button.disabled = true;
      button.textContent = 'Загрузка…';
    }
    setStatus('Загружаем файл…');
    let completed = false;
    for (let attempt = 0; attempt < 48; attempt += 1) {
      const suffix = attempt ? '?poll=1' : '';
      const response = await fetch(`${apiBase}/messages/${msgId}/download_media/${suffix}`);
      const data = await response.json().catch(() => ({}));
      if (response.status === 202) {
        if (button) button.textContent = 'Загружаем в фоне…';
        await new Promise((resolve) => window.setTimeout(resolve, 4000));
        continue;
      }
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      completed = true;
      break;
    }
    if (!completed) {
      throw new Error('Telegram всё ещё готовит файл. Попробуйте открыть его немного позже.');
    }
    messageIdsToRerender.add(downloadKey);
    await window.fetchMessagesGlobal?.(false, {preservePosition: true});
    setStatus('Файл загружен');
  } catch (error) {
    setError(error.message || 'Не удалось загрузить файл.');
    setStatus('');
    if (button) {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  } finally {
    activeMediaDownloads.delete(downloadKey);
    setStatus(activeMediaDownloads.size ? `Загружаем файлы: ${activeMediaDownloads.size}` : '');
  }
};
const getEffectiveChatAIState = (chat) => {
  if (!chat) return {active: false, paused: false, status: 'global-disabled', reason: 'ИИ-автоответчик выключен'};
  if (chat.ai_disabled) {
    return {active: false, paused: false, status: 'disabled', reason: 'ИИ отключён для этого диалога до ручного включения'};
  }
  const pausedUntil = chat.ai_paused_until ? new Date(chat.ai_paused_until) : null;
  if (pausedUntil && !Number.isNaN(pausedUntil.getTime()) && pausedUntil > new Date()) {
    return {
      active: false,
      paused: true,
      status: 'paused',
      reason: `ИИ временно отключён до ${pausedUntil.toLocaleString('ru-RU', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'})}`,
    };
  }
  if (!aiRuntime) {
    return {
      active: Boolean(chat.ai_active),
      paused: chat.ai_status === 'paused',
      status: String(chat.ai_status || (chat.ai_active ? 'active' : 'global-disabled')).replaceAll('_', '-'),
      reason: chat.ai_status_reason || 'ИИ-автоответчик выключен',
    };
  }
  if (aiRuntime.global_status === 'paused') {
    const globalPausedUntil = aiRuntime.paused_until ? new Date(aiRuntime.paused_until) : null;
    const untilText = globalPausedUntil && !Number.isNaN(globalPausedUntil.getTime())
      ? ` до ${globalPausedUntil.toLocaleString('ru-RU', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'})}`
      : '';
    return {active: false, paused: true, status: 'global-paused', reason: `ИИ временно выключен во всём проекте${untilText}`};
  }
  if (!(aiRuntime.effective_enabled ?? aiRuntime.enabled)) return {active: false, paused: false, status: 'global-disabled', reason: 'ИИ-автоответчик выключен во всём проекте'};
  if (aiRuntime.operator_present && !aiRuntime.online_override_enabled) {
    return {active: false, paused: false, status: 'operator-paused', reason: 'ИИ приостановлен: администратор работает в CRM'};
  }
  return {active: true, paused: false, status: 'active', reason: 'Работает ИИ-автоответчик'};
};
const setActiveChat = (chat) => {
  const accountType = getAccountType(chat);
  const active = document.getElementById("active-chat");
  if (active) active.textContent = chat ? getChatName(chat) : "Выберите диалог";
  const accountStatus = chat
    ? getAccountLabel(chat) + " · " + (chat.telegram_account?.name || (accountType === "max" ? "MAX" : accountType === "whatsapp" ? "WhatsApp" : "Telegram"))
    : "Сообщения выбранного чата появятся здесь";
  const systemName = chat?.google_contact_name && chat?.system_name && chat.google_contact_name !== chat.system_name
    ? `Имя в мессенджере: ${chat.system_name} · `
    : '';
  setStatus(systemName + accountStatus);
  setComposerEnabled(Boolean(chat) && isChatInCurrentScope(chat));
  const historyButton = document.getElementById('import-history-btn');
  if (historyButton) historyButton.hidden = !chat || getAccountType(chat) === 'bot';
  const aiStatus = document.getElementById('chat-ai-status');
  if (aiStatus) {
    const aiState = getEffectiveChatAIState(chat);
    aiStatus.hidden = !chat;
    aiStatus.classList.toggle('is-active', aiState.active);
    aiStatus.classList.toggle('is-paused', aiState.status === 'paused');
    aiStatus.classList.toggle('is-disabled', aiState.status === 'disabled');
    aiStatus.classList.toggle('is-unavailable', ['global-disabled', 'global-paused', 'operator-paused'].includes(aiState.status));
    aiStatus.title = aiState.active
      ? `${aiState.reason}. Нажмите, чтобы отключить для этого диалога.`
      : ['paused', 'disabled'].includes(aiState.status)
        ? `${aiState.reason}. Нажмите, чтобы включить.`
        : aiState.reason;
    aiStatus.setAttribute('aria-label', aiStatus.title);
  }
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
  let chatAIControlAction = null;
  const configureChatAIControlModal = (chat, aiState) => {
    const title = document.getElementById('chat-ai-control-title');
    const description = document.getElementById('chat-ai-control-description');
    const options = document.getElementById('chat-ai-disable-options');
    const confirm = document.getElementById('chat-ai-control-confirm');
    if (aiState.active) {
      chatAIControlAction = 'disable';
      title.textContent = `Отключить ИИ для «${getChatName(chat)}»?`;
      description.textContent = 'Выберите временное отключение или отключение до ручного включения.';
      options.hidden = false;
      confirm.textContent = 'Отключить ИИ';
      confirm.classList.add('danger-button');
      confirm.classList.remove('primary-button');
      const temporary = document.querySelector('input[name="chat-ai-disable-type"][value="temporary"]');
      const hours = document.getElementById('chat-ai-disable-hours');
      if (temporary) temporary.checked = true;
      if (hours) {
        hours.value = '1';
        hours.disabled = false;
      }
    } else {
      chatAIControlAction = 'enable';
      title.textContent = `Включить ИИ для «${getChatName(chat)}»?`;
      description.textContent = aiState.status === 'disabled'
        ? 'Автоответчик снова сможет отвечать на новые входящие сообщения.'
        : 'Временная пауза будет отменена досрочно.';
      options.hidden = true;
      confirm.textContent = 'Включить ИИ';
      confirm.classList.remove('danger-button');
      confirm.classList.add('primary-button');
    }
    openModal('chat-ai-control-modal');
  };

  document.getElementById('chat-ai-status')?.addEventListener('click', () => {
    const chat = allChats.find((item) => Number(item.id) === Number(currentChatId));
    if (!chat) return;
    const aiState = getEffectiveChatAIState(chat);
    if (['global-disabled', 'global-paused', 'operator-paused'].includes(aiState.status)) {
      showNotification('ИИ-автоответчик', aiState.reason);
      return;
    }
    configureChatAIControlModal(chat, aiState);
  });

  document.querySelectorAll('input[name="chat-ai-disable-type"]').forEach((radio) => radio.addEventListener('change', () => {
    const hours = document.getElementById('chat-ai-disable-hours');
    if (hours) hours.disabled = document.querySelector('input[name="chat-ai-disable-type"]:checked')?.value !== 'temporary';
  }));

  document.getElementById('chat-ai-control-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const chat = allChats.find((item) => Number(item.id) === Number(currentChatId));
    if (!chat || !chatAIControlAction) return;
    const button = document.getElementById('chat-ai-control-confirm');
    const payload = {mode: 'enabled'};
    if (chatAIControlAction === 'disable') {
      const disableType = document.querySelector('input[name="chat-ai-disable-type"]:checked')?.value || 'temporary';
      payload.mode = disableType === 'permanent' ? 'disabled' : 'paused';
      if (payload.mode === 'paused') payload.hours = Number(document.getElementById('chat-ai-disable-hours')?.value || 0);
    }
    if (button) button.disabled = true;
    try {
      const result = await request(`${apiBase}/chats/${chat.id}/set_ai_mode/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      allChats = allChats.map((item) => Number(item.id) === Number(chat.id)
        ? {...item, ai_disabled: Boolean(result.ai_disabled), ai_paused_until: result.ai_paused_until}
        : item);
      const updatedChat = allChats.find((item) => Number(item.id) === Number(chat.id));
      renderChats(allChats);
      setActiveChat(updatedChat);
      closeModal('chat-ai-control-modal');
      showNotification('ИИ-автоответчик', result.message);
      await fetchChats();
    } catch (error) {
      setError(error.message);
    } finally {
      if (button) button.disabled = false;
    }
  });

  const updateAIRuntimeControls = () => {
    const button = document.getElementById('ai-override-btn');
    if (!button || !aiRuntime) return;
    const override = Boolean(aiRuntime.online_override_enabled);
    const effectiveEnabled = Boolean(aiRuntime.effective_enabled ?? aiRuntime.enabled);
    button.disabled = !effectiveEnabled;
    button.classList.toggle('is-active', override);
    button.querySelector('span').textContent = override ? 'Отключить ИИ-автоответчик' : 'Включить ИИ-автоответчик';
    button.title = !effectiveEnabled
      ? (aiRuntime.global_status === 'paused' ? 'ИИ временно выключен во всём проекте' : 'ИИ выключен во всём проекте')
      : override
        ? 'ИИ отвечает, если администратор не ответил вовремя'
        : 'Временно разрешить ИИ отвечать, пока администратор работает в CRM';
    const globalButton = document.getElementById('ai-global-control-btn');
    if (globalButton) {
      const globalStatus = aiRuntime.global_status || (aiRuntime.enabled ? 'active' : 'disabled');
      globalButton.classList.toggle('is-active', globalStatus === 'active');
      globalButton.classList.toggle('is-paused', globalStatus === 'paused');
      globalButton.classList.toggle('is-disabled', globalStatus === 'disabled');
      globalButton.title = globalStatus === 'active'
        ? 'ИИ работает во всём проекте. Нажмите, чтобы отключить.'
        : globalStatus === 'paused'
          ? 'ИИ временно выключен во всём проекте. Нажмите, чтобы включить.'
          : 'ИИ выключен во всём проекте. Нажмите, чтобы включить.';
      globalButton.setAttribute('aria-label', globalButton.title);
    }
    renderChats(allChats);
    const selectedChat = allChats.find((chat) => Number(chat.id) === Number(currentChatId));
    if (selectedChat) setActiveChat(selectedChat);
  };

  const fillAISettings = (data) => {
    aiRuntime = data;
    document.getElementById('ai-enabled').checked = Boolean(data.enabled);
    document.getElementById('ai-base-prompt').value = data.base_prompt || '';
    document.getElementById('ai-company-info').value = data.company_information || '';
    document.getElementById('ai-fallback-text').value = data.fallback_text || '';
    document.getElementById('ai-offline-delay').value = data.offline_delay_seconds || 30;
    document.getElementById('ai-online-delay').value = data.online_delay_seconds || 60;
    document.getElementById('ai-manual-pause').value = data.manual_pause_minutes || 60;
    document.getElementById('ai-context-limit').value = data.context_message_limit || 20;
    document.getElementById('ai-operator-idle').value = data.operator_idle_seconds || 90;
    document.getElementById('ai-message-max-age').value = data.max_incoming_age_minutes || 10;
    updateAIRuntimeControls();
  };

  const loadAISettings = async () => {
    try {
      fillAISettings(await request(`${apiBase}/ai/settings/`));
    } catch (error) {
      console.error('Could not load AI settings', error);
    }
  };

  document.getElementById('ai-settings-btn')?.addEventListener('click', async () => {
    await loadAISettings();
    openModal('ai-settings-modal');
  });
  document.getElementById('ai-settings-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const data = await request(`${apiBase}/ai/settings/`, {
        method: 'PATCH',
        body: JSON.stringify({
          enabled: document.getElementById('ai-enabled').checked,
          base_prompt: document.getElementById('ai-base-prompt').value,
          company_information: document.getElementById('ai-company-info').value,
          fallback_text: document.getElementById('ai-fallback-text').value,
          offline_delay_seconds: Number(document.getElementById('ai-offline-delay').value),
          online_delay_seconds: Number(document.getElementById('ai-online-delay').value),
          manual_pause_minutes: Number(document.getElementById('ai-manual-pause').value),
          context_message_limit: Number(document.getElementById('ai-context-limit').value),
          operator_idle_seconds: Number(document.getElementById('ai-operator-idle').value),
          max_incoming_age_minutes: Number(document.getElementById('ai-message-max-age').value),
        }),
      });
      fillAISettings(data);
      closeModal('ai-settings-modal');
      showNotification('Настройки ИИ', 'Настройки сохранены.');
      await fetchChats();
    } catch (error) {
      setError(error.message);
    }
  });
  document.getElementById('ai-override-btn')?.addEventListener('click', async () => {
    if (!(aiRuntime?.effective_enabled ?? aiRuntime?.enabled)) return;
    try {
      const result = await request(`${apiBase}/ai/online-override/`, {
        method: 'POST',
        body: JSON.stringify({enabled: !aiRuntime.online_override_enabled}),
      });
      aiRuntime.online_override_enabled = Boolean(result.enabled);
      updateAIRuntimeControls();
      await fetchChats();
    } catch (error) {
      setError(error.message);
    }
  });

  let aiGlobalControlAction = null;
  const configureAIGlobalControlModal = () => {
    if (!aiRuntime) return;
    const active = Boolean(aiRuntime.effective_enabled ?? aiRuntime.enabled);
    const title = document.getElementById('ai-global-control-title');
    const description = document.getElementById('ai-global-control-description');
    const options = document.getElementById('ai-global-disable-options');
    const confirm = document.getElementById('ai-global-control-confirm');
    aiGlobalControlAction = active ? 'disable' : 'enable';
    if (active) {
      title.textContent = 'Отключить ИИ во всём проекте?';
      description.textContent = 'ИИ перестанет отвечать во всех диалогах и мессенджерах.';
      options.hidden = false;
      confirm.textContent = 'Отключить ИИ';
      confirm.classList.add('danger-button');
      confirm.classList.remove('primary-button');
      const temporary = document.querySelector('input[name="ai-global-disable-type"][value="temporary"]');
      const hours = document.getElementById('ai-global-disable-hours');
      if (temporary) temporary.checked = true;
      if (hours) { hours.value = '1'; hours.disabled = false; }
    } else {
      title.textContent = 'Включить ИИ во всём проекте?';
      description.textContent = aiRuntime.global_status === 'paused'
        ? 'Временная пауза будет отменена досрочно.'
        : 'ИИ снова сможет отвечать на новые входящие сообщения.';
      options.hidden = true;
      confirm.textContent = 'Включить ИИ';
      confirm.classList.remove('danger-button');
      confirm.classList.add('primary-button');
    }
    openModal('ai-global-control-modal');
  };
  document.getElementById('ai-global-control-btn')?.addEventListener('click', configureAIGlobalControlModal);
  document.querySelectorAll('input[name="ai-global-disable-type"]').forEach((radio) => radio.addEventListener('change', () => {
    const hours = document.getElementById('ai-global-disable-hours');
    if (hours) hours.disabled = document.querySelector('input[name="ai-global-disable-type"]:checked')?.value !== 'temporary';
  }));
  document.getElementById('ai-global-control-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!aiGlobalControlAction) return;
    const confirm = document.getElementById('ai-global-control-confirm');
    const payload = {mode: 'enabled'};
    if (aiGlobalControlAction === 'disable') {
      const disableType = document.querySelector('input[name="ai-global-disable-type"]:checked')?.value || 'temporary';
      payload.mode = disableType === 'permanent' ? 'disabled' : 'paused';
      if (payload.mode === 'paused') payload.hours = Number(document.getElementById('ai-global-disable-hours')?.value || 0);
    }
    if (confirm) confirm.disabled = true;
    try {
      fillAISettings(await request(`${apiBase}/ai/global-mode/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      }));
      closeModal('ai-global-control-modal');
      showNotification('ИИ-автоответчик', payload.mode === 'enabled' ? 'ИИ включён во всём проекте.' : 'ИИ отключён во всём проекте.');
      await fetchChats();
    } catch (error) {
      setError(error.message);
    } finally {
      if (confirm) confirm.disabled = false;
    }
  });

  const presenceTabId = sessionStorage.getItem('crm-presence-tab-id') || createIdempotencyKey();
  sessionStorage.setItem('crm-presence-tab-id', presenceTabId);
  let lastOperatorActivityAt = Date.now();
  const operatorIsActuallyActive = () => {
    const idleSeconds = Math.max(30, Number(aiRuntime?.operator_idle_seconds || 90));
    return !document.hidden
      && document.hasFocus()
      && Date.now() - lastOperatorActivityAt <= idleSeconds * 1000;
  };
  const sendPresence = async () => {
    try {
      const state = await request(`${apiBase}/ai/presence/`, {
        method: 'POST',
        body: JSON.stringify({tab_id: presenceTabId, is_visible: operatorIsActuallyActive()}),
      });
      if (aiRuntime) {
        aiRuntime.operator_present = state.operator_present;
        aiRuntime.online_override_enabled = state.online_override_enabled;
        aiRuntime.enabled = state.enabled;
        aiRuntime.effective_enabled = state.effective_enabled;
        aiRuntime.paused_until = state.paused_until;
        aiRuntime.global_status = state.global_status;
        updateAIRuntimeControls();
      }
    } catch (error) {
      console.debug('Presence heartbeat failed', error);
    }
  };
  setInterval(sendPresence, 20000);
  document.addEventListener('visibilitychange', sendPresence);
  window.addEventListener('blur', sendPresence);
  window.addEventListener('focus', () => {
    lastOperatorActivityAt = Date.now();
    sendPresence();
  });
  ['pointerdown', 'keydown', 'touchstart', 'scroll'].forEach((eventName) => {
    document.addEventListener(eventName, () => {
      lastOperatorActivityAt = Date.now();
    }, {passive: true});
  });

  const pollGoogleContactsSync = () => {
    let attempts = 0;
    const timer = setInterval(async () => {
      attempts += 1;
      try {
        const state = await request(`${apiBase}/google-contacts/status/`);
        if (!state.sync_in_progress || attempts >= 60) {
          clearInterval(timer);
          if (state.last_error) setError(state.last_error);
          else {
            const result = state.last_result || {};
            showNotification('Google Контакты', `Синхронизация завершена. Сопоставлено чатов: ${result.matched_chats || 0}.`, 7000);
            await fetchChats({reset: true});
          }
        }
      } catch (error) {
        clearInterval(timer);
        setError(error.message);
      }
    }, 2000);
  };
  document.getElementById('google-contacts-btn')?.addEventListener('click', async () => {
    try {
      const state = await request(`${apiBase}/google-contacts/status/`);
      if (!state.configured) throw new Error('Добавьте GOOGLE_CLIENT_ID и GOOGLE_CLIENT_SECRET в .env.');
      if (!state.connected) {
        window.location.href = `${apiBase}/google-contacts/connect/`;
        return;
      }
      await request(`${apiBase}/google-contacts/sync/`, {method: 'POST'});
      showNotification('Google Контакты', 'Синхронизация запущена в фоне.');
      pollGoogleContactsSync();
    } catch (error) {
      setError(error.message);
    }
  });

  const query = new URLSearchParams(window.location.search);
  if (query.get('google_contacts_connected')) {
    showNotification('Google Контакты', 'Аккаунт подключён. Нажмите кнопку контактов для первой синхронизации.', 7000);
    history.replaceState({}, '', window.location.pathname);
  } else if (query.get('google_contacts_error')) {
    setError(query.get('google_contacts_error'));
    history.replaceState({}, '', window.location.pathname);
  }
  loadAISettings().then(sendPresence);

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

  let chatRestorePromise = null;
  const restoreChatSelection = (chats) => {
    if (currentChatId || chatRestorePromise) return;
    const orderedChats = orderChats(chats);
    const search = (document.getElementById('chat-search')?.value || '').trim();
    const storageKey = `last-chat:${currentMessenger}:${archiveMode ? 'archive' : 'active'}`;
    const savedId = !search ? Number(localStorage.getItem(storageKey)) : 0;
    const loaded = savedId && orderedChats.find((chat) => Number(chat.id) === savedId);
    if (loaded) {
      selectChat(loaded.id);
      return;
    }
    if (!savedId) {
      if (orderedChats[0]) selectChat(orderedChats[0].id);
      return;
    }

    const generation = chatFetchGeneration;
    chatRestorePromise = request(`${apiBase}/chats/${savedId}/`)
      .then((chat) => {
        if (
          generation !== chatFetchGeneration
          || currentChatId
          || !isChatInCurrentScope(chat)
          || Boolean(chat.is_archived) !== archiveMode
        ) return;
        allChats = [...allChats.filter((item) => Number(item.id) !== savedId), chat];
        renderChats(allChats);
        selectChat(chat.id);
      })
      .catch(() => {
        localStorage.removeItem(storageKey);
        if (!currentChatId && orderedChats[0]) selectChat(orderedChats[0].id);
      })
      .finally(() => { chatRestorePromise = null; });
  };

  const applyFetchedChats = (chats) => {
    allChats = chats;
    renderChats(chats);
    const selectedChat = chats.find((chat) => Number(chat.id) === Number(currentChatId));
    if (selectedChat) setActiveChat(selectedChat);
    restoreChatSelection(chats);
  };

  const getChatsUrl = () => {
    const params = new URLSearchParams({
      page_size: '50',
      messenger: currentMessenger,
      archived: archiveMode ? '1' : '0',
    });
    const search = (document.getElementById('chat-search')?.value || '').trim();
    if (search) params.set('search', search);
    return `${apiBase}/chats/?${params.toString()}`;
  };

  const fetchChats = ({reset = false, loadMore = false} = {}) => {
    if (reset) {
      chatFetchGeneration += 1;
      chatNextUrl = getChatsUrl();
      allChats = [];
      renderChats(allChats);
    }
    if (chatFetchPromise) {
      chatRefreshQueued = true;
      return chatFetchPromise;
    }

    const generation = chatFetchGeneration;
    const requestUrl = loadMore ? chatNextUrl : getChatsUrl();
    if (!requestUrl) return Promise.resolve();
    chatFetchPromise = (async () => {
      try {
        const response = await fetch(requestUrl);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (generation !== chatFetchGeneration) return;
        const received = normalizeList(data);
        chatArchiveCount = Number(data?.archive_count || 0);
        if (loadMore) {
          const merged = new Map(allChats.map((chat) => [Number(chat.id), chat]));
          received.forEach((chat) => merged.set(Number(chat.id), chat));
          chatNextUrl = typeof data?.next === 'string' ? data.next : null;
          applyFetchedChats([...merged.values()]);
        } else if (allChats.length === 0 || reset) {
          chatNextUrl = typeof data?.next === 'string' ? data.next : null;
          applyFetchedChats(received);
        } else {
          // A realtime refresh updates the newest rows but retains pages that
          // the operator already loaded, avoiding sidebar jumps.
          const merged = new Map(allChats.map((chat) => [Number(chat.id), chat]));
          received.forEach((chat) => merged.set(Number(chat.id), chat));
          applyFetchedChats([...merged.values()]);
        }
      } catch (error) {
        console.error("Critical error in fetchChats:", error);
      }
    })();

    chatFetchPromise.finally(() => {
      chatFetchPromise = null;
      if (chatRefreshQueued) {
        chatRefreshQueued = false;
        setTimeout(() => fetchChats(), 250);
      }
    });
    return chatFetchPromise;
  };
  window.fetchChatsGlobal = fetchChats;

  const fetchMessages = async (forceScroll = false, options = {}) => {
    if (!currentChatId) return;
    if (messageFetchController) messageFetchController.abort();
    const controller = new AbortController();
    messageFetchController = controller;
    const requestedChatId = currentChatId;
    const requestVersion = messageRequestVersion;
    try {
      const searchParam = messageSearchQuery ? `&search=${encodeURIComponent(messageSearchQuery)}` : "";
      const resp = await request(`${apiBase}/messages/by_chat/?chat_id=${requestedChatId}&page_size=${messageFetchLimit}${searchParam}`, {
        signal: controller.signal,
      });
      if (requestVersion !== messageRequestVersion || Number(requestedChatId) !== Number(currentChatId)) return;
      const msgs = normalizeList(resp);


      // Better snapshot: id + status + updated_at + text length + media path
      const snapshot = msgs.map(m => `${m.id}-${m.status}-${m.updated_at}-${(m.text || '').length}-${m.media_file_path || ''}-${JSON.stringify(m.reactions || [])}`).join('|') + `[search:${messageSearchQuery}]`;

      if (snapshot !== lastContentSnapshot || forceScroll) {
        renderMessages(msgs, forceScroll, Boolean(options.preservePosition));
        lastContentSnapshot = snapshot;
      }
    } catch (e) {
      if (e.name === 'AbortError') return;
      if (requestVersion !== messageRequestVersion) return;
      document.getElementById('message-list')?.classList.remove('is-loading');
      console.error('Failed to load messages', e);
    } finally {
      if (messageFetchController === controller) messageFetchController = null;
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
    if (!event.target.closest('.reaction-picker') && !event.target.closest('.message-reaction-button')) {
      document.querySelectorAll('.reaction-picker').forEach((picker) => { picker.hidden = true; });
    }
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
      fetchChats({reset: true});
    });
  });
  // Attachments and emoji
  const fileInput = document.getElementById('media-upload');
  const attachBtn = document.getElementById('attach-btn');
  const clearBtn = document.getElementById('clear-upload');
  const emojiBtn = document.getElementById('emoji-btn');
  const emojiPicker = document.getElementById('emoji-picker');
  const messageInput = document.getElementById('message-input');

  // Provider-neutral quick replies. The selected text is inserted for review,
  // never sent automatically, so the same interaction is safe for every messenger.
  const quickReplyShell = document.getElementById('quick-reply-shell');
  const quickReplyMenu = document.getElementById('quick-reply-menu');
  const quickReplySettingsButton = document.getElementById('quick-reply-settings-btn');
  const quickReplySettingsList = document.getElementById('quick-reply-settings-list');
  const quickReplyAddButton = document.getElementById('quick-reply-add-btn');
  const quickReplyEditor = document.getElementById('quick-reply-editor');
  const quickReplyEditorTitle = document.getElementById('quick-reply-editor-title');
  const quickReplyCommand = document.getElementById('quick-reply-command');
  const quickReplyText = document.getElementById('quick-reply-text');
  const quickReplyFormError = document.getElementById('quick-reply-form-error');
  let quickReplies = [];
  let quickReplyMatches = [];
  let quickReplyActiveIndex = -1;
  let quickReplyEditingId = null;

  const closeQuickReplyMenu = () => {
    if (quickReplyShell) quickReplyShell.hidden = true;
    quickReplyMatches = [];
    quickReplyActiveIndex = -1;
  };

  const insertQuickReply = (reply) => {
    if (!messageInput || !reply) return;
    messageInput.value = reply.text;
    messageInput.dispatchEvent(new Event('input', {bubbles: true}));
    closeQuickReplyMenu();
    messageInput.focus();
    messageInput.setSelectionRange(messageInput.value.length, messageInput.value.length);
  };

  const renderQuickReplyMenu = () => {
    if (!quickReplyMenu) return;
    quickReplyMenu.replaceChildren();
    if (!quickReplyMatches.length) {
      const empty = document.createElement('div');
      empty.className = 'quick-reply-empty';
      empty.textContent = quickReplies.length ? 'Нет подходящих быстрых ответов' : 'Быстрых ответов пока нет';
      quickReplyMenu.appendChild(empty);
      return;
    }
    quickReplyMatches.forEach((reply, index) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'quick-reply-option';
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(index === quickReplyActiveIndex));
      option.classList.toggle('is-active', index === quickReplyActiveIndex);
      const command = document.createElement('strong');
      command.textContent = reply.command;
      const preview = document.createElement('span');
      preview.textContent = reply.text;
      option.append(command, preview);
      option.addEventListener('mouseenter', () => {
        quickReplyActiveIndex = index;
        [...quickReplyMenu.querySelectorAll('.quick-reply-option')].forEach((item, itemIndex) => {
          item.classList.toggle('is-active', itemIndex === index);
          item.setAttribute('aria-selected', String(itemIndex === index));
        });
      });
      option.addEventListener('click', () => insertQuickReply(reply));
      quickReplyMenu.appendChild(option);
    });
    if (quickReplyActiveIndex >= 0) {
      quickReplyMenu.children[quickReplyActiveIndex]?.scrollIntoView({block: 'nearest'});
    }
  };

  const updateQuickReplyMenu = () => {
    if (!messageInput || !quickReplyShell) return;
    const query = messageInput.value.trim().toLowerCase();
    if (!/^\/[^\s]*$/.test(query)) {
      closeQuickReplyMenu();
      return;
    }
    quickReplyMatches = quickReplies.filter((reply) => reply.command.startsWith(query));
    quickReplyActiveIndex = quickReplyMatches.length ? 0 : -1;
    renderQuickReplyMenu();
    quickReplyShell.hidden = false;
  };

  const quickReplyErrorText = (error) => {
    const data = error?.data || {};
    const detail = data.command?.[0] || data.text?.[0] || data.non_field_errors?.[0] || data.detail;
    return detail || error?.message || 'Не удалось сохранить быстрый ответ.';
  };

  const renderQuickReplySettings = () => {
    if (!quickReplySettingsList) return;
    quickReplySettingsList.replaceChildren();
    if (!quickReplies.length) {
      const empty = document.createElement('div');
      empty.className = 'quick-reply-settings-empty';
      empty.textContent = 'Добавьте первый быстрый ответ';
      quickReplySettingsList.appendChild(empty);
      return;
    }
    quickReplies.forEach((reply) => {
      const item = document.createElement('div');
      item.className = 'quick-reply-settings-item';
      const copy = document.createElement('div');
      copy.className = 'quick-reply-settings-copy';
      const command = document.createElement('strong');
      command.textContent = reply.command;
      const text = document.createElement('span');
      text.textContent = reply.text;
      copy.append(command, text);
      const actions = document.createElement('div');
      actions.className = 'quick-reply-settings-actions';
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'quick-reply-item-button';
      edit.title = `Изменить ${reply.command}`;
      edit.setAttribute('aria-label', edit.title);
      edit.innerHTML = '<i class="material-icons">edit</i>';
      edit.addEventListener('click', () => showQuickReplyEditor(reply));
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'quick-reply-item-button quick-reply-item-button--delete';
      remove.title = `Удалить ${reply.command}`;
      remove.setAttribute('aria-label', remove.title);
      remove.innerHTML = '<i class="material-icons">delete_outline</i>';
      remove.addEventListener('click', async () => {
        if (remove.dataset.confirm !== '1') {
          remove.dataset.confirm = '1';
          remove.title = `Нажмите ещё раз, чтобы удалить ${reply.command}`;
          remove.setAttribute('aria-label', remove.title);
          remove.innerHTML = '<i class="material-icons">delete_forever</i>';
          item.classList.add('is-confirming');
          setTimeout(() => {
            if (!remove.isConnected) return;
            remove.dataset.confirm = '0';
            remove.title = `Удалить ${reply.command}`;
            remove.setAttribute('aria-label', remove.title);
            remove.innerHTML = '<i class="material-icons">delete_outline</i>';
            item.classList.remove('is-confirming');
          }, 4000);
          return;
        }
        try {
          await request(`${apiBase}/quick-replies/${reply.id}/`, {method: 'DELETE'});
          quickReplies = quickReplies.filter((item) => item.id !== reply.id);
          if (quickReplyEditingId === reply.id) hideQuickReplyEditor();
          renderQuickReplySettings();
          updateQuickReplyMenu();
        } catch (error) {
          setError(quickReplyErrorText(error));
        }
      });
      actions.append(edit, remove);
      item.append(copy, actions);
      quickReplySettingsList.appendChild(item);
    });
  };

  const hideQuickReplyEditor = () => {
    quickReplyEditingId = null;
    if (quickReplyEditor) quickReplyEditor.hidden = true;
    if (quickReplyFormError) quickReplyFormError.hidden = true;
  };

  const showQuickReplyEditor = (reply = null) => {
    quickReplyEditingId = reply?.id || null;
    if (quickReplyEditorTitle) quickReplyEditorTitle.textContent = reply ? 'Изменить быстрый ответ' : 'Новый быстрый ответ';
    if (quickReplyCommand) quickReplyCommand.value = reply?.command || '/';
    if (quickReplyText) quickReplyText.value = reply?.text || '';
    if (quickReplyFormError) quickReplyFormError.hidden = true;
    if (quickReplyEditor) quickReplyEditor.hidden = false;
    quickReplyCommand?.focus();
  };

  const loadQuickReplies = async () => {
    if (!quickReplyShell) return;
    try {
      quickReplies = normalizeList(await request(`${apiBase}/quick-replies/`));
      quickReplies.sort((a, b) => a.command.localeCompare(b.command, 'ru'));
      renderQuickReplySettings();
      const settingsModal = document.getElementById('quick-reply-settings-modal');
      if (messageInput?.value.startsWith('/') && settingsModal?.hidden !== false) updateQuickReplyMenu();
    } catch (error) {
      console.error('Could not load quick replies', error);
    }
  };

  if (messageInput && quickReplyShell) {
    messageInput.addEventListener('input', updateQuickReplyMenu);
    messageInput.addEventListener('keydown', (event) => {
      if (quickReplyShell.hidden) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        if (!quickReplyMatches.length) return;
        event.preventDefault();
        const direction = event.key === 'ArrowDown' ? 1 : -1;
        quickReplyActiveIndex = (quickReplyActiveIndex + direction + quickReplyMatches.length) % quickReplyMatches.length;
        renderQuickReplyMenu();
      } else if (event.key === 'Enter' && quickReplyActiveIndex >= 0) {
        event.preventDefault();
        insertQuickReply(quickReplyMatches[quickReplyActiveIndex]);
      } else if (event.key === 'Escape') {
        event.preventDefault();
        closeQuickReplyMenu();
      }
    });
    document.getElementById('send-form')?.addEventListener('submit', closeQuickReplyMenu);
    document.addEventListener('click', (event) => {
      if (!event.target.closest('#quick-reply-shell') && event.target !== messageInput) closeQuickReplyMenu();
    });
    quickReplySettingsButton?.addEventListener('click', () => {
      closeQuickReplyMenu();
      hideQuickReplyEditor();
      renderQuickReplySettings();
      openModal('quick-reply-settings-modal');
    });
    quickReplyAddButton?.addEventListener('click', () => showQuickReplyEditor());
    document.getElementById('quick-reply-editor-cancel')?.addEventListener('click', hideQuickReplyEditor);
    quickReplyEditor?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const submit = quickReplyEditor.querySelector('[type="submit"]');
      if (submit) submit.disabled = true;
      if (quickReplyFormError) quickReplyFormError.hidden = true;
      const endpoint = quickReplyEditingId
        ? `${apiBase}/quick-replies/${quickReplyEditingId}/`
        : `${apiBase}/quick-replies/`;
      try {
        await request(endpoint, {
          method: quickReplyEditingId ? 'PATCH' : 'POST',
          body: JSON.stringify({command: quickReplyCommand.value, text: quickReplyText.value}),
        });
        await loadQuickReplies();
        hideQuickReplyEditor();
      } catch (error) {
        if (quickReplyFormError) {
          quickReplyFormError.textContent = quickReplyErrorText(error);
          quickReplyFormError.hidden = false;
        }
      } finally {
        if (submit) submit.disabled = false;
      }
    });
    loadQuickReplies();
  }

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
    if (sendInFlight) return;
    const input = document.getElementById('message-input');
    const text = input.value.trim();
    if (!currentChatId || (!text && currentMedia.length === 0)) return;

    const pendingMedia = currentMedia.map((media) => ({...media}));
    const idempotencyKey = createIdempotencyKey();
    const payload = {
      text,
      media_paths: pendingMedia.map((media) => media.path),
      idempotency_key: idempotencyKey,
    };
    const selectedReply = replyTarget;
    const endpoint = selectedReply
      ? `${apiBase}/messages/${selectedReply.id}/reply/`
      : `${apiBase}/chats/${currentChatId}/send_message/`;
    try {
      sendInFlight = true;
      const sendButton = document.getElementById('send-button');
      if (sendButton) sendButton.disabled = true;
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
    } finally {
      sendInFlight = false;
      const sendButton = document.getElementById('send-button');
      if (sendButton) sendButton.disabled = !currentChatId;
    }
  });
  let chatSearchTimer = null;
  document.getElementById("chat-search")?.addEventListener("input", () => {
    clearTimeout(chatSearchTimer);
    chatSearchTimer = setTimeout(() => {
      resetConversation();
      fetchChats({reset: true});
    }, 300);
  });

  document.getElementById('chat-list')?.addEventListener('scroll', (event) => {
    const list = event.currentTarget;
    if (chatNextUrl && list.scrollTop + list.clientHeight >= list.scrollHeight - 240) {
      fetchChats({loadMore: true});
    }
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
      const isReconnect = reconnectAttempt > 0;
      reconnectAttempt = 0;
      clearTimeout(reconnectTimer);
      // Initial HTTP loading is already running. Refresh only after an actual
      // reconnect so the first chats and messages are not requested twice.
      if (isReconnect) scheduleRealtimeRefresh(currentChatId);
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
      fetchAccountHealth({ quiet: true });
      connectRealtime();
    }
  });

  fetchChats({reset: true});
  fetchAccountHealth();
  setInterval(() => {
    if (!document.hidden) fetchAccountHealth({ quiet: true });
  }, 30000);
  connectRealtime();
  startFallbackPolling();
});
