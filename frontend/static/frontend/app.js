const apiBase = "/api";

const getToken = () => "";

const setStatus = (message) => {
  const status = document.getElementById("status");
  if (status) status.textContent = message;
};

const setError = (message) => {
  const errorBox = document.getElementById("error-box");
  if (errorBox) {
    if (message) {
      errorBox.textContent = message;
      errorBox.style.display = "block";
    } else {
      errorBox.style.display = "none";
    }
  }
};

const request = async (url, options = {}) => {
  const defaults = {
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken(),
    },
  };
  const response = await fetch(url, { ...defaults, ...options });
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.error || errorData.detail || `HTTP ${response.status}`);
  }
  return response.json();
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
  if (err.includes('Chat is not assigned')) return "Чат не назначен оператору.";
  return err;
};

let currentChatId = null;
let lastRenderedMessageId = null;

const selectChat = (id, label) => {
  currentChatId = id;
  lastRenderedMessageId = null; // Сброс для принудительного скролла
  setActiveChat(id, label);

  document.querySelectorAll('#chat-list li').forEach(li => {
    if (Number(li.dataset.chatId) === id) li.classList.add('active');
    else li.classList.remove('active');
  });

  if (window.fetchMessagesGlobal) window.fetchMessagesGlobal(true);
};

const renderChats = (chats) => {
  const chatList = document.getElementById("chat-list");
  if (!chatList) return;
  chatList.innerHTML = "";

  if (chats.length === 0) {
    chatList.innerHTML = '<li class="list__item muted">Нет активных чатов.</li>';
    return;
  }

  chats.forEach((chat) => {
    const li = document.createElement("li");
    li.className = `list__item ${currentChatId === chat.id ? "active" : ""}`;
    li.dataset.chatId = chat.id;
    // Убрали имя аккаунта (small) и бейдж счетчика (badge)
    li.innerHTML = `
      <div class="chat-info">
        <strong>${chat.title || chat.username || 'Без названия'}</strong>
      </div>
    `;
    li.onclick = () => selectChat(chat.id, chat.title || chat.username);
    chatList.appendChild(li);
  });
};

let lastContentSnapshot = "";

const renderMessages = (messages, forceScroll = false) => {
  const messageList = document.getElementById("message-list");
  if (!messageList) return;

  // Создаем "отпечаток" контента, чтобы заметить изменения (например, появление media_file_path)
  const currentSnapshot = messages.map(m => `${m.id}-${!!m.media_file_path}`).join('|');
  const isMessageCountChanged = messages.length > 0 && (messages[messages.length - 1].id !== lastRenderedMessageId);

  // Если ничего не изменилось и это не принудительный скролл - выходим
  if (currentSnapshot === lastContentSnapshot && !forceScroll) return;

  const isAtBottom = messageList.scrollHeight - messageList.scrollTop <= messageList.clientHeight + 100;

  messageList.innerHTML = "";
  if (messages.length === 0) {
    messageList.innerHTML = '<div class="notice">Нет сообщений</div>';
    lastRenderedMessageId = null;
    lastContentSnapshot = "";
    return;
  }

  const sorted = [...messages].sort((a, b) => new Date(a.telegram_date) - new Date(b.telegram_date));

  sorted.forEach((msg) => {
    const div = document.createElement("div");
    div.className = `message ${msg.is_outgoing ? "message--outgoing" : "message--incoming"}`;

    let content = `<div class="message__text">${msg.text || ""}</div>`;

    if (msg.media_file_path) {
      if (msg.message_type === 'photo') {
        content = `<div class="message__media"><img src="/media/${msg.media_file_path}" class="message__media--image" onclick="window.open('/media/${msg.media_file_path}', '_blank')"></div>` + content;
      } else {
        content = `<div class="message__media"><a href="/api/messages/${msg.id}/download_media/" target="_blank" class="message__media--document">Файл</a></div>` + content;
      }
    } else if (msg.message_type && msg.message_type !== 'text') {
      content = `<div class="media-placeholder"><div class="media-placeholder__content"><button class="media-placeholder__download" onclick="downloadViaApi(${msg.id})">Загрузить ${msg.message_type_display || 'медиа'}</button></div></div>` + content;
    }

    div.innerHTML = `
      ${content}
      <span class="message__time">${new Date(msg.telegram_date).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
    `;
    messageList.appendChild(div);
  });

  lastRenderedMessageId = messages[messages.length - 1].id;
  lastContentSnapshot = currentSnapshot;

  if (forceScroll || (isMessageCountChanged && isAtBottom)) {
    messageList.scrollTop = messageList.scrollHeight;
  }
};

window.downloadViaApi = async (msgId) => {
  try {
    setStatus("Загрузка...");
    const resp = await fetch(`${apiBase}/messages/${msgId}/download_media/`);
    if (resp.ok) {
      // Принудительно очищаем снимок, чтобы fetchMessages точно перерисовал интерфейс
      lastContentSnapshot = "";
      if (window.fetchMessagesGlobal) await window.fetchMessagesGlobal(false);
      setStatus("Готово");
    }
  } catch (e) {
    setError("Ошибка загрузки");
  }
};

const setActiveChat = (chatId, label) => {
  const active = document.getElementById("active-chat");
  if (active) active.textContent = label || `Чат #${chatId}`;
  const btnM = document.getElementById("load-messages");
  const btnS = document.getElementById("send-button");
  if (btnM) btnM.disabled = !chatId;
  if (btnS) btnS.disabled = !chatId;
};

document.addEventListener("DOMContentLoaded", () => {
  const fetchChats = async () => {
    try {
      const response = await fetch(`${apiBase}/chats/?assigned_only=1`);
      const data = await response.json();
      const chats = normalizeList(data);
      renderChats(chats);

      const inactive = [];
      chats.forEach(c => {
        if (c.telegram_account && c.telegram_account.status !== 'active') inactive.push(c.telegram_account.name);
      });
      if (inactive.length > 0) setError(`Внимание: Аккаунты (${inactive.join(', ')}) неактивны.`);
      else setError("");

      if (chats.length > 0 && !currentChatId) {
        selectChat(chats[0].id, chats[0].title || chats[0].username);
      }
    } catch (e) { }
  };

  const fetchMessages = async (forceScroll = false) => {
    if (!currentChatId) return;
    try {
      const payload = await request(`${apiBase}/messages/by_chat/?chat_id=${currentChatId}`);
      renderMessages(normalizeList(payload), forceScroll);
    } catch (e) { }
  };

  window.fetchMessagesGlobal = fetchMessages;

  document.getElementById("send-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("message-input");
    const text = input.value.trim();
    if (!currentChatId || !text) return;

    try {
      await request(`${apiBase}/chats/${currentChatId}/send_message/`, {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      input.value = "";
      fetchMessages(true); // Скроллим после отправки
    } catch (e) {
      setError(localizeError(e.message));
    }
  });

  document.getElementById("load-chats")?.addEventListener("click", fetchChats);
  document.getElementById("load-messages")?.addEventListener("click", () => fetchMessages(true));

  // WebSocket / Polling
  const startPolling = () => {
    setInterval(fetchChats, 2000);
    setInterval(() => fetchMessages(false), 1000);
  };

  fetchChats();
  startPolling();
});
