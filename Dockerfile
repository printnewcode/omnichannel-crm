# Dockerfile для Django приложения
FROM python:3.12-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Копирование requirements и установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование проекта
COPY . .

# Создание необходимых директорий
RUN mkdir -p staticfiles media/sessions logs

# Переменные окружения
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=CRM.settings

# Порты
EXPOSE 8000 8001

# Команда по умолчанию (может быть переопределена в docker-compose.yml)
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
