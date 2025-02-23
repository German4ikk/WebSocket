import os
import asyncio
import websockets
import json
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Определяем порт для сервера (по умолчанию 8080)
PORT = int(os.getenv("PORT", 8080))

# Словарь {user_id: websocket} — все активные клиенты
clients = {}

# Словарь {streamer_id: websocket} — активные эфиры
active_streams = {}

# Список user_id, ожидающих собеседника в рулетке
roulette_queue = []

# Словарь {streamer_id: [viewer_ids]} — зрители конкретного эфира
viewers = {}

async def handler(websocket, path):
    """
    Основной обработчик входящих сообщений.
    Каждое сообщение — это JSON, содержащий поле 'type', указывающее тип события.
    """
    user_id = None
    try:
        async for message in websocket:
            data = json.loads(message)
            msg_type = data.get('type')

            # --- РЕГИСТРАЦИЯ ---
            if msg_type == 'register':
                user_id = data['user_id']
                if user_id in clients:
                    logger.warning(f"Пользователь {user_id} уже зарегистрирован, перезаписываем соединение")
                    await clients[user_id].close()
                clients[user_id] = websocket
                mode = data.get('mode', 'viewer')
                logger.info(f"[register] user_id={user_id}, mode={mode}")
                await websocket.send(json.dumps({'type': 'connected', 'mode': mode}))

            # --- СТАРТ ЭФИРА ---
            elif msg_type == 'start_stream':
                user_id = data['user_id']
                if user_id in active_streams:
                    logger.warning(f"Пользователь {user_id} уже ведёт эфир, игнорируем повторный старт")
                    continue
                active_streams[user_id] = websocket
                stream_mode = data.get('mode', 'stream')
                logger.info(f"[start_stream] user_id={user_id}, mode={stream_mode}")
                await websocket.send(json.dumps({
                    'type': 'stream_started',
                    'user_id': user_id,
                    'mode': stream_mode
                }))
                # Уведомляем всех клиентов о новом стриме, кроме самого стримера
                for uid, client_ws in clients.items():
                    if uid != user_id:
                        await client_ws.send(json.dumps({
                            'type': 'stream_notification',
                            'user_id': user_id,
                            'mode': stream_mode
                        }))

            # --- ПОЛУЧИТЬ СПИСОК ЭФИРОВ (для зрителя) ---
            elif msg_type == 'get_streams':
                streams = list(active_streams.keys())
                logger.info(f"[get_streams] user_id={data['user_id']} -> {streams}")
                await websocket.send(json.dumps({
                    'type': 'stream_list',
                    'streams': streams
                }))

            # --- ПРИСОЕДИНИТЬСЯ К ЭФИРУ (зритель) ---
            elif msg_type == 'join_stream':
                user_id = data['user_id']
                streamer_id = data['streamer_id']
                logger.info(f"[join_stream] user_id={user_id} -> streamer_id={streamer_id}")
                if streamer_id not in active_streams:
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': 'Эфир не найден'
                    }))
                    continue
                if streamer_id not in viewers:
                    viewers[streamer_id] = []
                if user_id not in viewers[streamer_id]:
                    viewers[streamer_id].append(user_id)
                    # Отправляем зрителю предложение (offer) для WebRTC
                    await clients[user_id].send(json.dumps({
                        'type': 'offer',
                        'offer': {},  # Здесь нужно заменить на реальный offer из стримера
                        'from': streamer_id
                    }))
                    # Уведомляем стримера о новом зрителе
                    await active_streams[streamer_id].send(json.dumps({
                        'type': 'viewer_joined',
                        'viewer_id': user_id
                    }))
                else:
                    logger.warning(f"Пользователь {user_id} уже подключён к эфиру {streamer_id}")

            # --- РУЛЕТКА ---
            elif msg_type == 'join_roulette':
                user_id = data['user_id']
                logger.info(f"[join_roulette] user_id={user_id}")
                if user_id in roulette_queue:
                    logger.warning(f"Пользователь {user_id} уже в очереди рулетки")
                    continue
                if roulette_queue and roulette_queue[0] != user_id:
                    partner_id = roulette_queue.pop(0)
                    await clients[user_id].send(json.dumps({
                        'type': 'partner',
                        'partner_id': partner_id
                    }))
                    await clients[partner_id].send(json.dumps({
                        'type': 'partner',
                        'partner_id': user_id
                    }))
                    # Отправляем offer для WebRTC (нужно доработать)
                    await clients[user_id].send(json.dumps({
                        'type': 'offer',
                        'offer': {},
                        'from': partner_id
                    }))
                else:
                    roulette_queue.append(user_id)

            # --- ICE/SDP (WebRTC) ---
            elif msg_type in ['offer', 'answer', 'candidate']:
                to_id = data.get('to')
                if to_id in clients:
                    target_ws = clients[to_id]
                    logger.info(f"[{msg_type}] from={user_id} to={to_id}")
                    await target_ws.send(json.dumps(data))
                elif to_id in active_streams:
                    target_ws = active_streams[to_id]
                    logger.info(f"[{msg_type}] from={user_id} to_streamer={to_id}")
                    await target_ws.send(json.dumps(data))
                else:
                    logger.error(f"Получатель {to_id} не найден для {msg_type}")

            # --- ЧАТ (общий или к стримеру) ---
            elif msg_type == 'chat_message':
                from_id = data['user_id']
                message_text = data['message']
                to = data.get('to')
                logger.info(f"[chat_message] from={from_id}, to={to}, msg={message_text}")
                if to and to in active_streams:
                    await active_streams[to].send(json.dumps({
                        'type': 'chat_message',
                        'user_id': from_id,
                        'message': message_text
                    }))
                    if to in viewers:
                        for v_id in viewers[to]:
                            if v_id in clients and v_id != from_id:
                                await clients[v_id].send(json.dumps({
                                    'type': 'chat_message',
                                    'user_id': from_id,
                                    'message': message_text
                                }))
                else:
                    # Рассылка общего чата всем, кроме отправителя
                    for uid, client_ws in clients.items():
                        if uid != from_id:
                            await client_ws.send(json.dumps({
                                'type': 'chat_message',
                                'user_id': from_id,
                                'message': message_text
                            }))

            # --- ПОДАРОК ---
            elif msg_type == 'gift':
                from_id = data['user_id']
                to_id = data['to']
                amount = data['amount']
                logger.info(f"[gift] from={from_id} to={to_id} amount={amount}")
                if to_id in active_streams:
                    await active_streams[to_id].send(json.dumps({
                        'type': 'gift',
                        'user_id': from_id,
                        'amount': amount
                    }))
                else:
                    logger.error(f"Получатель подарка {to_id} не найден")

    except websockets.ConnectionClosed as e:
        logger.error(f"Соединение закрыто: {e}")
    except Exception as e:
        logger.error(f"Ошибка в обработчике: {e}")
    finally:
        if user_id:
            if user_id in clients:
                del clients[user_id]
            if user_id in active_streams:
                del active_streams[user_id]
            if user_id in roulette_queue:
                roulette_queue.remove(user_id)
            for sid, v_list in list(viewers.items()):
                if user_id in v_list:
                    v_list.remove(user_id)
                    if not v_list:
                        del viewers[sid]
            logger.info(f"Пользователь {user_id} отключён")

async def main():
    async with websockets.serve(handler, "0.0.0.0", PORT):
        logger.info(f"✅ WebSocket сервер запущен на порту {PORT}")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
