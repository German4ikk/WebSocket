import os
import asyncio
import websockets
import json
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Определяем порт для сервера (по умолчанию 8080 для Fly.io)
PORT = int(os.getenv("PORT", 8080))

# Словарь {user_id: websocket} — все активные клиенты
clients = {}

# Словарь {streamer_id: websocket} — активные эфиры
active_streams = {}

# Очередь пользователей, ожидающих подключения в рулетке
roulette_queue = []

# Словарь {streamer_id: [viewer_ids]} — зрители эфира
viewers = {}

async def handler(websocket, path):
    """
    Основной обработчик входящих сообщений WebSocket.
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
                    logger.warning(f"Пользователь {user_id} уже подключен, обновляем соединение")
                    await clients[user_id].close()
                clients[user_id] = websocket
                mode = data.get('mode', 'viewer')
                logger.info(f"[REGISTER] user_id={user_id}, mode={mode}")
                await websocket.send(json.dumps({'type': 'connected', 'mode': mode}))

            # --- СТАРТ ЭФИРА ---
            elif msg_type == 'start_stream':
                user_id = data['user_id']
                if user_id in active_streams:
                    logger.warning(f"Пользователь {user_id} уже ведет эфир")
                    continue
                active_streams[user_id] = websocket
                stream_mode = data.get('mode', 'stream')
                logger.info(f"[START_STREAM] user_id={user_id}, mode={stream_mode}")
                await websocket.send(json.dumps({
                    'type': 'stream_started',
                    'user_id': user_id,
                    'mode': stream_mode
                }))
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
                logger.info(f"[GET_STREAMS] user_id={data['user_id']} -> {streams}")
                await websocket.send(json.dumps({'type': 'stream_list', 'streams': streams}))

            # --- ПРИСОЕДИНИТЬСЯ К ЭФИРУ (зритель) ---
            elif msg_type == 'join_stream':
                user_id = data['user_id']
                streamer_id = data['streamer_id']
                logger.info(f"[JOIN_STREAM] user_id={user_id} -> streamer_id={streamer_id}")
                if streamer_id not in active_streams:
                    await websocket.send(json.dumps({'type': 'error', 'message': 'Эфир не найден'}))
                    continue
                if streamer_id not in viewers:
                    viewers[streamer_id] = []
                if user_id not in viewers[streamer_id]:
                    viewers[streamer_id].append(user_id)
                    await clients[user_id].send(json.dumps({'type': 'offer', 'offer': {}, 'from': streamer_id}))
                    await active_streams[streamer_id].send(json.dumps({'type': 'viewer_joined', 'viewer_id': user_id}))

            # --- ВИДЕО-РУЛЕТКА ---
            elif msg_type == 'join_roulette':
                user_id = data['user_id']
                logger.info(f"[JOIN_ROULETTE] user_id={user_id}")
                if user_id in roulette_queue:
                    continue
                if roulette_queue:
                    partner_id = roulette_queue.pop(0)
                    await clients[user_id].send(json.dumps({'type': 'partner', 'partner_id': partner_id}))
                    await clients[partner_id].send(json.dumps({'type': 'partner', 'partner_id': user_id}))
                else:
                    roulette_queue.append(user_id)

            # --- ICE/SDP (WebRTC) ---
            elif msg_type in ['offer', 'answer', 'candidate']:
                to_id = data.get('to')
                if to_id in clients:
                    target_ws = clients[to_id]
                    logger.info(f"[{msg_type.upper()}] from={user_id} to={to_id}")
                    await target_ws.send(json.dumps(data))
                elif to_id in active_streams:
                    target_ws = active_streams[to_id]
                    logger.info(f"[{msg_type.upper()}] from={user_id} to_streamer={to_id}")
                    await target_ws.send(json.dumps(data))
                else:
                    logger.error(f"Получатель {to_id} не найден")

            # --- ЧАТ ---
            elif msg_type == 'chat_message':
                from_id = data['user_id']
                message_text = data['message']
                to = data.get('to')
                logger.info(f"[CHAT] from={from_id}, to={to}, msg={message_text}")
                if to and to in active_streams:
                    await active_streams[to].send(json.dumps({'type': 'chat_message', 'user_id': from_id, 'message': message_text}))
                    if to in viewers:
                        for v_id in viewers[to]:
                            if v_id in clients and v_id != from_id:
                                await clients[v_id].send(json.dumps({'type': 'chat_message', 'user_id': from_id, 'message': message_text}))
                else:
                    for uid, client_ws in clients.items():
                        if uid != from_id:
                            await client_ws.send(json.dumps({'type': 'chat_message', 'user_id': from_id, 'message': message_text}))

            # --- ПОДАРКИ ---
            elif msg_type == 'gift':
                from_id = data['user_id']
                to_id = data['to']
                amount = data['amount']
                logger.info(f"[GIFT] from={from_id} to={to_id} amount={amount}")
                if to_id in active_streams:
                    await active_streams[to_id].send(json.dumps({'type': 'gift', 'user_id': from_id, 'amount': amount}))

    except websockets.ConnectionClosed as e:
        logger.warning(f"Соединение закрыто: {e}")
    except Exception as e:
        logger.error(f"Ошибка в обработчике: {e}", exc_info=True)
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
