const apiBase = "/api";

const getToken = () => "";

const setStatus = (message) => {
  const status = document.getElementById("status");
  if (status) {
    status.textContent = message;
  }
};

const setError = (message) => {
  const box = document.getElementById("error-box");
  if (!box) {
    return;
  }
  if (message) {
    box.textContent = message;
    box.style.display = "block";
  } else {
    box.textContent = "";
    box.style.display = "none";
  }
};

const localizeError = (message) => {
  if (message.includes("Chat is not assigned to this operator")) {
    return "Чат не назначен этому оператору";
  }
  return message;
};

const getCsrfToken = () => {
  // Try to get from hidden input first
  const csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
  if (csrfInput) {
    return csrfInput.value;
  }
  // Fallback to cookie
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? match[1] : "";
};

const request = async (url, options = {}) => {
  const token = getToken();
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (token) {
    headers.Authorization = `Token ${token}`;
  } else if (["POST", "PUT", "PATCH", "DELETE"].includes((options.method || "GET").toUpperCase())) {
    const csrfToken = getCsrfToken();
    if (csrfToken) {
      headers["X-CSRFToken"] = csrfToken;
    }
  }
  const response = await fetch(url, { ...options, headers, credentials: "same-origin" });
  if (!response.ok) {
    let detail = "";
    try {
      const data = await response.json();
      detail = data.error || data.detail || JSON.stringify(data);
    } catch {
      detail = await response.text();
    }
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json();
};

const normalizeList = (payload) => {
  if (Array.isArray(payload)) {
    return payload;
  }
  if (payload && Array.isArray(payload.results)) {
    return payload.results;
  }
  return [];
};

const renderChats = (chats) => {
  const list = document.getElementById("chat-list");
  if (!list) return;
  list.innerHTML = "";
  chats.forEach((chat) => {
    const item = document.createElement("li");
    item.textContent = chat.title || chat.username || `Chat ${chat.id}`;
    item.dataset.chatId = chat.id;
    list.appendChild(item);
  });
};

const renderMessages = (messages) => {
  const container = document.getElementById("message-list");
  if (!container) return;
  container.innerHTML = "";
  const sorted = [...messages].sort((a, b) => {
    const aTime = new Date(a.telegram_date || 0).getTime();
    const bTime = new Date(b.telegram_date || 0).getTime();
    return aTime - bTime;
  });
  sorted.forEach((message) => {
    const bubble = document.createElement("div");
    bubble.className = `message ${message.is_outgoing ? "message--outgoing" : ""}`;
    const text = document.createElement("div");
    text.textContent = message.text || "Media";
    const time = document.createElement("span");
    time.className = "message__time";
    time.textContent = (message.telegram_date || "").slice(11, 16);
    bubble.appendChild(text);
    bubble.appendChild(time);
    container.appendChild(bubble);
  });
};

const setActiveChat = (chatId, label) => {
  const active = document.getElementById("active-chat");
  if (active) {
    active.textContent = label || `Чат #${chatId}`;
  }
  const button = document.getElementById("load-messages");
  const send = document.getElementById("send-button");
  if (button) button.disabled = !chatId;
  if (send) send.disabled = !chatId;
};

document.addEventListener("DOMContentLoaded", () => {
  let currentChatId = null;

  const chatList = document.getElementById("chat-list");
  const loadChats = document.getElementById("load-chats");
  const loadMessages = document.getElementById("load-messages");
  const sendForm = document.getElementById("send-form");
  const messageInput = document.getElementById("message-input");

  const fetchChats = async () => {
    try {
      setStatus("Загрузка чатов...");
      setError("");
      const payload = await request(`${apiBase}/chats/?assigned_only=1`);
      const chats = normalizeList(payload);
      renderChats(chats);
      if (chats.length > 0 && !currentChatId) {
        const first = chats[0];
        currentChatId = Number(first.id);
        setActiveChat(currentChatId, first.title || first.username || `Чат ${first.id}`);
        fetchMessages();
      }
      setStatus("Чаты загружены");
    } catch (error) {
      const message = localizeError(error.message);
      setError(message);
      setStatus(`Ошибка чатов: ${message}`);
    }
  };

  const fetchMessages = async () => {
    if (!currentChatId) return;
    try {
      setStatus("Загрузка сообщений...");
      setError("");
      const payload = await request(`${apiBase}/messages/by_chat/?chat_id=${currentChatId}`);
      const messages = normalizeList(payload);
      renderMessages(messages);
      setStatus("Сообщения загружены");
    } catch (error) {
      const message = localizeError(error.message);
      setError(message);
      setStatus(`Ошибка сообщений: ${message}`);
    }
  };

  const sendMessage = async (text) => {
    if (!currentChatId) return;
    try {
      setStatus("Отправка...");
      setError("");
      await request(`${apiBase}/chats/${currentChatId}/send_message/`, {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      setStatus("Отправлено");
      await fetchMessages();
    } catch (error) {
      const message = localizeError(error.message);
      setError(message);
      setStatus(`Ошибка отправки: ${message}`);
    }
  };

  if (loadChats) {
    loadChats.addEventListener("click", fetchChats);
  }

  if (loadMessages) {
    loadMessages.addEventListener("click", fetchMessages);
  }

  if (chatList) {
    chatList.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement) || !target.dataset.chatId) {
        return;
      }
      const items = chatList.querySelectorAll("li");
      items.forEach((item) => item.classList.remove("active"));
      target.classList.add("active");
      currentChatId = Number(target.dataset.chatId);
      setActiveChat(currentChatId, target.textContent);
      fetchMessages();
    });
  }

  if (sendForm && messageInput) {
    sendForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = messageInput.value.trim();
      if (!text) return;
      sendMessage(text);
      messageInput.value = "";
    });
  }

  fetchChats();
  setStatus("Готово");
});
