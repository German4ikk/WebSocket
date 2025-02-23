# Используем официальный образ Python 3.9 (можно изменить на 3.8, если нужно)
FROM python:3.9

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы проекта в контейнер
COPY requirements.txt .
COPY server.py .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Открываем порт 5000 для сервера
EXPOSE 5000

# Запускаем сервер при старте контейнера
CMD ["python", "server.py"]
