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
  let response;
  try {
    response = await fetch(url, { ...options, headers, credentials: "same-origin" });
  } catch (networkError) {
    // Обработка сетевых ошибок (HTTP 0)
    throw new Error(`Network error: ${networkError.message}`);
  }

  if (!response.ok) {
    let detail = `Request failed: ${response.status}`;
    try {
      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('application/json')) {
        const data = await response.json();
        detail = data.error || data.detail || JSON.stringify(data);
      } else {
        const text = await response.text();
        detail = text || detail;
      }
    } catch (e) {
      // Если не удалось прочитать тело, оставляем исходную ошибку
      detail = `Request failed: ${response.status} ${response.statusText}`;
    }
    throw new Error(detail);
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

    // Показ медиа
    if (message.media_file_path) {
      const mediaElement = createMediaElement(message);
      bubble.appendChild(mediaElement);
    } else if (message.message_type && message.message_type !== 'text') {
      // Показываем плейсхолдер для нескачанного медиа
      const placeholder = createMediaPlaceholder(message);
      bubble.appendChild(placeholder);
    }

    // Показываем текст (для медиа с подписью или обычных сообщений)
    if (message.text || message.media_caption) {
      const text = document.createElement("div");
      text.textContent = message.text || message.media_caption || "";
      bubble.appendChild(text);
    }

    const time = document.createElement("span");
    time.className = "message__time";
    time.textContent = (message.telegram_date || "").slice(11, 16);
    bubble.appendChild(time);
    container.appendChild(bubble);
  });
};

// Функция для создания медиа элементов
const createMediaElement = (message) => {
  const mediaType = message.message_type;

  // Если файл уже скачан
  if (message.media_file_path) {
    const mediaPath = `/media/${message.media_file_path}`;
    return createMediaFromPath(mediaPath, mediaType);
  }

  // Если файл не скачан, но есть file_id - показать плейсхолдер
  if (message.telegram_file_id) {
    const placeholder = document.createElement('div');
    placeholder.className = 'media-placeholder';
    placeholder.innerHTML = `
      <div class="media-placeholder__content">
        📎 ${getMediaTypeText(mediaType)}
        <button class="media-placeholder__download" data-message-id="${message.id}">
          Загрузить
        </button>
      </div>
    `;
    return placeholder;
  }
};

const createMediaFromPath = (mediaPath, mediaType) => {
  switch (mediaType) {
    case 'photo':
      const img = document.createElement('img');
      img.src = mediaPath;
      img.className = 'message__media message__media--image';
      img.onclick = () => openMediaModal(mediaPath, 'image');
      return img;

    case 'video':
      const video = document.createElement('video');
      video.src = mediaPath;
      video.className = 'message__media message__media--video';
      video.controls = true;
      video.preload = 'metadata';
      video.onclick = () => openMediaModal(mediaPath, 'video');
      return video;

    case 'voice':
    case 'audio':
      const audio = document.createElement('audio');
      audio.src = mediaPath;
      audio.className = 'message__media message__media--audio';
      audio.controls = true;
      return audio;

    case 'document':
      const docLink = document.createElement('a');
      docLink.href = mediaPath;
      docLink.target = '_blank';
      docLink.className = 'message__media message__media--document';
      docLink.textContent = '📎 Документ';
      return docLink;

    default:
      const unknown = document.createElement('div');
      unknown.textContent = `Медиа: ${mediaType}`;
      return unknown;
  }
};

const getMediaTypeText = (mediaType) => {
  const types = {
    'photo': 'Фото',
    'video': 'Видео',
    'voice': 'Голосовое сообщение',
    'document': 'Документ'
  };
  return types[mediaType] || 'Медиа';
};

// Модальное окно для просмотра медиа
const openMediaModal = (mediaPath, mediaType) => {
  // Создаем модальное окно если его нет
  let modal = document.getElementById('media-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'media-modal';
    modal.className = 'media-modal';
    modal.innerHTML = `
      <div class="media-modal__overlay"></div>
      <div class="media-modal__content">
        <button class="media-modal__close" aria-label="Закрыть">&times;</button>
        <div class="media-modal__media-container"></div>
      </div>
    `;
    document.body.appendChild(modal);

    // Обработчики закрытия
    modal.querySelector('.media-modal__overlay').onclick = closeMediaModal;
    modal.querySelector('.media-modal__close').onclick = closeMediaModal;

    // Закрытие по ESC
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modal.style.display === 'flex') {
        closeMediaModal();
      }
    });
  }

  // Заполняем контент
  const container = modal.querySelector('.media-modal__media-container');
  container.innerHTML = '';

  if (mediaType === 'image') {
    const img = document.createElement('img');
    img.src = mediaPath;
    img.className = 'media-modal__image';
    container.appendChild(img);
  } else if (mediaType === 'video') {
    const video = document.createElement('video');
    video.src = mediaPath;
    video.className = 'media-modal__video';
    video.controls = true;
    video.autoplay = true;
    container.appendChild(video);
  }

  // Показываем модальное окно
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden'; // Запрещаем прокрутку фона
};

const closeMediaModal = () => {
  const modal = document.getElementById('media-modal');
  if (modal) {
    modal.style.display = 'none';
    document.body.style.overflow = ''; // Восстанавливаем прокрутку

    // Останавливаем видео если оно играет
    const video = modal.querySelector('.media-modal__video');
    if (video) {
      video.pause();
    }
  }
};

const createMediaPlaceholder = (message) => {
  const placeholder = document.createElement('div');
  placeholder.className = 'media-placeholder';
  placeholder.innerHTML = `
    <div class="media-placeholder__content">
      📎 ${getMediaTypeText(message.message_type)}
      <button class="media-placeholder__download" data-message-id="${message.id}">
        Загрузить
      </button>
    </div>
  `;
  return placeholder;
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

  const fetchMessages = async (retryCount = 0) => {
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

      // Повторная попытка при сетевых ошибках (HTTP 0)
      if ((message.includes('HTTP 0') || message.includes('Failed to fetch')) && retryCount < 2) {
        setStatus(`Повторная попытка... (${retryCount + 1}/2)`);
        setTimeout(() => fetchMessages(retryCount + 1), 1000 * (retryCount + 1));
        return;
      }

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

  // Обработчик клика по кнопке загрузки медиа
  document.addEventListener('click', async (e) => {
    if (e.target.classList.contains('media-placeholder__download')) {
      const messageId = e.target.dataset.messageId;
      const button = e.target;

      // Отключаем кнопку на время загрузки
      button.disabled = true;
      button.textContent = "Загрузка...";

      try {
        setStatus("Загрузка медиа...");
        setError("");

        // Проверяем токен
        const token = getToken();
        if (!token) {
          throw new Error("Не авторизован - обновите страницу");
        }

        // Специальный запрос для скачивания медиа (не парсим как JSON)
        const headers = {
          'Accept': 'application/json',
          'Authorization': `Token ${token}`
        };

        try {
          // Простой GET запрос - браузер автоматически обработает редирект
          const response = await fetch(`${apiBase}/messages/${messageId}/download_media/`, {
            method: 'GET',
            headers,
            credentials: "same-origin"
          });

          if (response.ok) {
            // Если получили успешный ответ, значит файл скачан
            setStatus("Медиа загружено");
            setTimeout(() => fetchMessages(), 800);
            return;
          } else {
            // Ошибка
            let errorText = `HTTP ${response.status}`;
            if (response.status === 0) {
              errorText = "Сетевая ошибка - проверьте подключение";
            } else if (response.status === 401) {
              errorText = "Не авторизован - обновите страницу";
            } else if (response.status === 403) {
              errorText = "Доступ запрещен";
            }
            throw new Error(errorText);
          }
        } catch (networkError) {
          if (networkError.name === 'TypeError' && networkError.message.includes('fetch')) {
            throw new Error("Сетевая ошибка - проверьте подключение к интернету");
          }
          throw networkError;
        }
      } catch (error) {
        const message = localizeError(error.message);
        setError(message);
        setStatus(`Ошибка загрузки медиа: ${message}`);
      } finally {
        // Восстанавливаем кнопку
        button.disabled = false;
        button.textContent = "Загрузить";
      }
    }
  });

  fetchChats();
  setStatus("Готово");
});
