# Используем официальный образ Python
FROM python:3.9

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы проекта в контейнер
COPY requirements.txt .
COPY server.py .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Запускаем сервер при старте контейнера
CMD ["python", "server.py"]
