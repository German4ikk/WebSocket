# Используем официальный образ Python 3.10 (совпадает с fly.toml)
FROM python:3.10-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы проекта в контейнер
COPY requirements.txt .
COPY server.py .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем переменную окружения для порта (совпадает с fly.toml)
ENV PORT=5000

# Открываем порт 5000 для сервера
EXPOSE ${PORT}

# Запускаем сервер при старте контейнера
CMD ["python", "server.py"]
