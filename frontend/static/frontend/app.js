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
  if (msg.includes('HTTP 404')) return 'Ресурс не найден (404).';
  if (msg.includes('HTTP 500')) return 'Ошибка сервера (500).';
  if (msg.includes('HTTP 403')) return 'Доступ запрещен (403).';
  if (msg.includes('Chat is not assigned')) return 'Чат не назначен оператору.';
  if (msg.includes('Account is disconnected')) {
    const parts = msg.split('. ');
    return parts.length > 1 ? parts[1] : 'Аккаунт отключен. Пожалуйста, активируйте его в боковой панели.';
  }
  if (msg.includes('FloodWait')) return 'Слишком много запросов (FloodWait). Пожалуйста, подождите.';
  if (msg.includes('UserDeactivatedError')) return 'Этот аккаунт Telegram был удален или деактивирован.';
  if (msg.includes('SessionPasswordNeededError')) return 'Требуется пароль двухэтапной аутентификации.';
  if (msg.includes('is activating')) return 'Аккаунт в процессе активации. Пожалуйста, подождите.';

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
            <div class="notification-title">${title}</div>
            <div class="notification-close" onclick="this.parentElement.parentElement.remove()">×</div>
        </div>
        <div class="notification-body">${message}</div>
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

const normalizeList = (payload) => {
  if (Array.isArray(payload)) return payload;
  if (payload && Array.isArray(payload.results)) return payload.results;
  return [];
};

const localizeError = (err) => {
  return getRussianError(err);
};

let currentChatId = null;
let lastRenderedMessageId = null;
let currentMedia = null; // Store current uploaded media info
let messageSearchQuery = ""; // Current search query for messages
const notifiedAccounts = new Set(); // Track notified account errors
let lastAccountsSnapshot = ""; // Prevent flickering in sidebar

const selectChat = (id, label) => {
  currentChatId = id;
  lastRenderedMessageId = null; // Reset for scroll
  messageSearchQuery = ""; // Reset search on chat change
  const searchInput = document.getElementById("message-search-input");
  if (searchInput) searchInput.value = "";
  const searchContainer = document.getElementById("message-search-container");
  if (searchContainer) searchContainer.style.display = "none";

  setActiveChat(id, label);

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

const renderChats = (chats) => {
  const chatList = document.getElementById("chat-list");
  if (!chatList) return;
  chatList.innerHTML = "";

  if (chats.length === 0) {
    chatList.innerHTML = '<li class="chat-item" style="cursor:default; color: #707579; justify-content:center;">No active chats</li>';
    return;
  }

  chats.forEach((chat) => {
    const li = document.createElement("li");
    li.className = `chat-item ${currentChatId === chat.id ? "active" : ""}`;
    li.dataset.chatId = chat.id;

    const name = chat.title || chat.username || 'No Name';
    const initials = getInitials(name);

    // Use preview from serializer, fallback to last_message field, fallback to "No messages"
    let lastMsg = chat.last_message_preview || chat.last_message || "No messages";

    // Use telegram_date if available, otherwise fallback to updated_at
    let timeStr = "";
    if (chat.last_message_data && chat.last_message_data.telegram_date) {
      timeStr = new Date(chat.last_message_data.telegram_date).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } else if (chat.updated_at) {
      timeStr = new Date(chat.updated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    li.innerHTML = `
      <div class="chat-avatar">${initials}</div>
      <div class="chat-content-text">
        <div class="chat-top">
            <span class="chat-title">${name}</span>
            <span class="chat-time">${timeStr}</span>
        </div>
        <div class="chat-bottom">
            <span class="chat-last-message">${lastMsg}</span>
            ${(chat.unread_count && chat.unread_count > 0) ? '<div class="unread-indicator"></div>' : ''}
        </div>
      </div>
    `;
    li.onclick = () => selectChat(chat.id, name);
    chatList.appendChild(li);
  });
};

// Helper to get status icon
const getStatusIcon = (status) => {
  switch (status) {
    case 'pending': return '🕒';
    case 'sent': return '✓';
    case 'received': return '✓✓';
    case 'failed': return '❌';
    default: return '';
  }
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

  if (date.toDateString() === today.toDateString()) return 'Today';
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
  if (date.getFullYear() !== today.getFullYear()) options.year = 'numeric';

  return date.toLocaleDateString(undefined, options);
};

// Sticky date logic
const updateStickyDate = () => {
  const messageList = document.getElementById("message-list");
  const stickyDateHeader = document.getElementById("sticky-date-header");
  if (!messageList || !stickyDateHeader) return;

  const dividers = Array.from(messageList.querySelectorAll('.date-divider'));
  let currentDivider = null;

  for (const divider of dividers) {
    if (divider.offsetTop <= messageList.scrollTop + 80) {
      currentDivider = divider;
    } else {
      break;
    }
  }

  if (currentDivider) {
    stickyDateHeader.textContent = currentDivider.textContent;
    stickyDateHeader.style.display = 'block';
  } else {
    stickyDateHeader.style.display = 'none';
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

  if (forceScroll) {
    messageList.innerHTML = "";
    renderedMessageIds.clear();
    lastDateString = null;
  }

  if (messages.length === 0) {
    if (messageList.innerHTML === "") {
      messageList.innerHTML = '<div class="empty-state">No messages</div>';
    }
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

  if (filtered.length === 0 && messageSearchQuery) {
    messageList.innerHTML = '<div class="empty-state">No messages found for "' + messageSearchQuery + '"</div>';
    return;
  }

  const sorted = filtered.sort((a, b) => new Date(a.telegram_date) - new Date(b.telegram_date));

  sorted.forEach((msg) => {
    // Check for date change
    const msgDate = new Date(msg.telegram_date);
    const dateStr = msgDate.toDateString();

    if (dateStr !== lastDateString) {
      const divider = document.createElement("div");
      divider.className = "date-divider";
      divider.textContent = formatDateHeader(msgDate);
      messageList.appendChild(divider);
      lastDateString = dateStr;
    }

    // If message already rendered, just update status if it changed
    if (renderedMessageIds.has(msg.id)) {
      const existing = document.querySelector(`.message[data-msg-id="${msg.id}"]`);
      if (existing) {
        const icon = existing.querySelector('.status-icon');
        if (icon) icon.textContent = getStatusIcon(msg.status);
      }
      return;
    }

    const div = document.createElement("div");
    // Only animate if it's a new message during polling (not first load of chat)
    const animateClass = (!forceScroll && renderedMessageIds.size > 0) ? "animate-in" : "";
    div.className = `message ${msg.is_outgoing ? "message--outgoing" : "message--incoming"} ${animateClass}`;
    div.dataset.msgId = msg.id;

    let content = `<div class="message__text">${msg.text || ""}</div>`;

    let timeStr = "";
    try {
      if (msg.telegram_date) {
        timeStr = new Date(msg.telegram_date).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
    } catch (e) { console.error("Invalid date", msg.telegram_date); }

    if (msg.media_file_path) {
      const mediaUrl = `/media/${msg.media_file_path}`;
      const safeUrl = mediaUrl.replace(/'/g, "\\'");

      if (msg.message_type === 'photo') {
        content = `<div class="message__media"><img src="${mediaUrl}" class="message__media--image" onclick="openMediaViewer('${safeUrl}', 'image')" style="cursor: pointer; max-width: 100%;"></div>` + content;
      } else if (msg.message_type === 'video') {
        content = `<div class="message__media" onclick="openMediaViewer('${safeUrl}', 'video')" style="cursor: pointer; position: relative;">
                             <video src="${mediaUrl}" style="max-width: 100%;"></video>
                             <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 30px;">▶️</div>
                           </div>` + content;
      } else if (msg.message_type === 'voice' || msg.message_type === 'audio') {
        content = `<div class="message__media"><audio controls src="${mediaUrl}" style="width: 100%;"></audio></div>` + content;
      } else {
        content = `<div class="message__media"><a href="${mediaUrl}" target="_blank" class="message__media--document"><i class="material-icons" style="font-size:16px;">description</i> File</a></div>` + content;
      }
    } else if (msg.message_type && msg.message_type !== 'text') {
      content = `<div class="message__media"><button onclick="downloadViaApi(${msg.id})" style="background:#2563eb; color:white; border:none; padding:6px 12px; border-radius:6px; cursor:pointer;">Download ${msg.message_type_display || 'Media'}</button></div>` + content;
    }

    div.innerHTML = `
        ${content}
        <span class="message__time">${timeStr}
           ${msg.is_outgoing ? `<span class="status-icon">${getStatusIcon(msg.status)}</span>` : ''}
        </span>
      `;
    messageList.appendChild(div);
    renderedMessageIds.add(msg.id);
  });

  if (forceScroll || isAtBottom) {
    messageList.scrollTop = messageList.scrollHeight;
  }
};


window.downloadViaApi = async (msgId) => {
  try {
    setStatus("Downloading...");
    // Force download logic
    window.open(`${apiBase}/messages/${msgId}/download_media/`, '_blank');
    setStatus("Online");
  } catch (e) {
    setError("Download failed");
  }
};
const setActiveChat = (chatId, label) => {
  const active = document.getElementById("active-chat");
  if (active) active.textContent = label || `Chat #${chatId}`;

  const btnS = document.getElementById("send-button");
  if (btnS) btnS.disabled = !chatId;
};

// --- File Upload Logic ---
const handleFileUpload = async (file) => {
  if (!file) return;

  setStatus("Uploading...");

  const formData = new FormData();
  formData.append('file', file);

  try {
    const response = await fetch(`${apiBase}/upload/`, {
      method: 'POST',
      body: formData,
      headers: {
        'X-CSRFToken': getCsrfToken()
      }
    });

    if (!response.ok) throw new Error("Upload failed");

    const data = await response.json();
    currentMedia = {
      path: data.file_path,
      name: data.file_name
    };

    // Show preview
    const preview = document.getElementById("upload-preview");
    const filename = document.getElementById("upload-filename");
    const previewIcon = preview?.querySelector('.material-icons:first-child');

    if (preview && filename) {
      filename.textContent = data.file_name;
      preview.style.display = "flex";

      if (previewIcon) {
        const ext = data.file_name.split('.').pop().toLowerCase();
        const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp'];
        previewIcon.textContent = imageExts.includes(ext) ? 'image' : 'description';
      }
    }
    setStatus("");
  } catch (e) {
    setError("Upload failed: " + e.message);
    setStatus("");
  }
};

const clearUpload = () => {
  currentMedia = null;
  const preview = document.getElementById("upload-preview");
  const input = document.getElementById("media-upload");
  if (preview) preview.style.display = "none";
  if (input) input.value = "";
};
// -------------------------

document.addEventListener("DOMContentLoaded", () => {
  const fetchChats = async () => {
    try {
      // Use allSettled to prevent one failed fetch from blocking the whole UI
      const results = await Promise.allSettled([
        request(`${apiBase}/chats/?assigned_only=1`),
        request(`${apiBase}/accounts/`)
      ]);

      let chats = [];
      let accounts = [];

      // Process chats result
      if (results[0].status === 'fulfilled') {
        chats = normalizeList(results[0].value);
        renderChats(chats);
      } else {
        console.error("Chats fetch failed:", results[0].reason);
      }

      // Process accounts result
      const notificationArea = document.getElementById("sidebar-account-notifications");
      if (notificationArea) {
        if (results[1].status === 'fulfilled') {
          accounts = normalizeList(results[1].value);
          // Show accounts that are not active (including authenticating)
          const showAccounts = accounts.filter(acc => acc.status !== 'active');

          const snapshot = showAccounts
            .map(acc => `${acc.id}-${acc.status}-${acc.last_error || ''}`)
            .sort()
            .join('|');

          if (snapshot !== lastAccountsSnapshot) {
            notificationArea.innerHTML = "";
            showAccounts.forEach(acc => {
              const card = document.createElement("div");
              card.className = "account-notification-card";
              if (acc.status === 'authenticating') card.classList.add('authenticating');

              let statusText = acc.status === 'error' ? 'Ошибка аккаунта' :
                acc.status === 'authenticating' ? 'Активация...' : 'Аккаунт неактивен';
              let icon = acc.status === 'error' ? 'error_outline' :
                acc.status === 'authenticating' ? 'sync' : 'warning_amber';

              card.innerHTML = `
                <div class="account-notification-header">
                  <i class="material-icons ${acc.status === 'authenticating' ? 'spin' : ''}">${icon}</i>
                  <span>${acc.name}: ${statusText}</span>
                </div>
                ${acc.status === 'authenticating' ?
                  '<div class="activation-progress">Пожалуйста, подождите...</div>' :
                  `<button class="account-notification-btn" onclick="activateAccount(${acc.id}, '${acc.name}')">Активировать</button>`
                }
              `;
              notificationArea.appendChild(card);

              if (acc.status === 'error' && acc.last_error) {
                setError(acc.last_error, acc.name);
              } else if (acc.status === 'active' || acc.status === 'authenticating') {
                // Clear error tracking if account becomes active or attempts authentication
                notifiedAccounts.delete(acc.name);
              }
            });
            lastAccountsSnapshot = snapshot;
          }
        } else {
          console.error("Accounts fetch failed:", results[1].reason);
          // If fetch fails, we reset snapshot to ensure retry on next poll if things recover
          lastAccountsSnapshot = "";
        }
      }

      // Auto-select first chat if needed
      if (chats.length > 0 && !currentChatId) {
        selectChat(chats[0].id, chats[0].title || chats[0].username);
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
    try {
      const resp = await request(`${apiBase}/messages/by_chat/?chat_id=${currentChatId}&limit=100`);
      const msgs = normalizeList(resp);

      // Better snapshot: id + status + updated_at + text length + media path
      const snapshot = msgs.map(m => `${m.id}-${m.status}-${m.updated_at}-${(m.text || '').length}-${m.media_file_path || ''}`).join('|') + `[search:${messageSearchQuery}]`;

      if (snapshot !== lastContentSnapshot || forceScroll) {
        renderMessages(msgs, forceScroll);
        lastContentSnapshot = snapshot;
      }
    } catch (e) {
      // Silent fail provided we don't spam notifications
    }
  };

  window.fetchMessagesGlobal = fetchMessages;

  // File Input Listeners
  const fileInput = document.getElementById("media-upload");
  const attachBtn = document.getElementById("attach-btn");
  const clearBtn = document.getElementById("clear-upload");

  if (attachBtn && fileInput) {
    attachBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", (e) => {
      if (e.target.files.length > 0) {
        handleFileUpload(e.target.files[0]);
      }
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", clearUpload);
  }

  document.getElementById("send-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("message-input");
    const text = input.value.trim();

    if (!currentChatId) return;
    if (!text && !currentMedia) return;

    const payload = { text };
    if (currentMedia) {
      payload.media_path = currentMedia.path;
    }

    try {
      const res = await request(`${apiBase}/chats/${currentChatId}/send_message/`, {
        method: "POST",
        body: JSON.stringify(payload),
      });

      // Handle 202 Accepted (Activation in progress)
      if (res && res.status === 'pending') {
        showNotification('Информация', `Сообщение будет отправлено после активации аккаунта. ${localizeError(res.message)}`);
      }

      input.value = "";
      clearUpload(); // Clear media after send
      fetchMessages(true);
    } catch (e) {
      setError(localizeError(e.message));
    }
  });

  // Search filter (client side simple)
  document.getElementById("chat-search")?.addEventListener("input", (e) => {
    const val = e.target.value.toLowerCase();
    document.querySelectorAll('#chat-list .chat-item').forEach(li => {
      const title = li.querySelector('.chat-title').textContent.toLowerCase();
      if (title.includes(val)) li.style.display = "flex";
      else li.style.display = "none";
    });
  });

  // Message search filter
  const searchBtn = document.getElementById("message-search-btn");
  const searchContainer = document.getElementById("message-search-container");
  const searchInput = document.getElementById("message-search-input");
  const searchClose = document.getElementById("message-search-close");

  if (searchBtn && searchContainer && searchInput) {
    searchBtn.addEventListener("click", () => {
      const isVisible = searchContainer.style.display === "flex";
      searchContainer.style.display = isVisible ? "none" : "flex";
      if (!isVisible) searchInput.focus();
      else {
        messageSearchQuery = "";
        searchInput.value = "";
        fetchMessages(true);
      }
    });

    searchInput.addEventListener("input", (e) => {
      messageSearchQuery = e.target.value.trim();
      fetchMessages(false); // Re-render with filter
    });

    searchClose.addEventListener("click", () => {
      searchContainer.style.display = "none";
      messageSearchQuery = "";
      searchInput.value = "";
      fetchMessages(true);
    });
  }

  // WebSocket / Polling
  const startPolling = () => {
    setInterval(fetchChats, 3000);
    setInterval(() => fetchMessages(false), 2000);

    // Trigger Telegram -> DB sync every 5 seconds
    setInterval(async () => {
      try {
        await fetch(`${apiBase}/sync/`, {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCsrfToken()
          }
        });
      } catch (e) {
        console.error("Sync error:", e);
      }
    }, 7000);
  };

  // Emoji Picker Logic
  const emojiBtn = document.getElementById("emoji-btn");
  const emojiPicker = document.getElementById("emoji-picker");
  const messageInput = document.getElementById("message-input");

  const commonEmojis = ['😀', '😃', '😄', '😁', '😅', '😂', '🤣', '😊', '😇', '🙂', '🙃', '😉', '😌', '😍', '🥰', '😘', '😗', '😙', '😚', '😋', '😛', '😝', '😜', '🤪', '🤨', '🧐', '🤓', '😎', '🤩', '🥳', '😏', '😒', '😞', '😔', '😟', '😕', '🙁', '☹️', '😣', '😖', '😫', '😩', '🥺', '😢', '😭', '😤', '😠', '😡', '🤬', '🤯', '😳', '🥵', '🥶', '😱', '😨', '😰', '😥', '😓', '🤗', '🤔', '🤭', '🤫', '🤥', '😶', '😐', '😑', '😬', '🙄', '😯', '😧', '😨', '😰', '🤤', '😪', '😵', '🤐', '🥴', '🤢', '🤮', '🤧', '😷', '🤒', '🤕', '🤑', '🤠', '😈', '👿', '👹', '👺', '🤡', '👻', '💀', '☠️', '👽', '👾', '🤖', '🎃', '😺', '😸', '😹', '😻', '😼', '😽', '🙀', '😿', '😾'];

  if (emojiBtn && emojiPicker && messageInput) {
    const grid = document.createElement("div");
    grid.className = "emoji-grid";
    commonEmojis.forEach(emoji => {
      const span = document.createElement("span");
      span.className = "emoji-item";
      span.textContent = emoji;
      span.onclick = () => {
        messageInput.value += emoji;
        messageInput.focus();
        emojiPicker.style.display = "none";
      };
      grid.appendChild(span);
    });
    emojiPicker.appendChild(grid);

    emojiBtn.onclick = (e) => {
      e.stopPropagation();
      const isVisible = emojiPicker.style.display === "flex";
      emojiPicker.style.display = isVisible ? "none" : "flex";
    };

    document.addEventListener("click", (e) => {
      if (!emojiPicker.contains(e.target) && e.target !== emojiBtn) {
        emojiPicker.style.display = "none";
      }
    });
  }

  fetchChats();
  startPolling();
});
