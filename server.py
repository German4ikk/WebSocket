import os
import asyncio
import websockets
import json

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
                clients[user_id] = websocket
                mode = data.get('mode', 'viewer')
                print(f"[register] user_id={user_id}, mode={mode}")
                # Отправим обратно подтверждение
                await websocket.send(json.dumps({'type': 'connected', 'mode': mode}))

            # --- СТАРТ ЭФИРА ---
            elif msg_type == 'start_stream':
                user_id = data['user_id']
                active_streams[user_id] = websocket
                stream_mode = data.get('mode', 'stream')
                print(f"[start_stream] user_id={user_id}, mode={stream_mode}")
                # Подтверждаем стримеру
                await websocket.send(json.dumps({
                    'type': 'stream_started',
                    'user_id': user_id,
                    'mode': stream_mode
                }))
                # Уведомляем всех остальных о новом эфире
                for uid, client_ws in clients.items():
                    if uid != user_id:
                        await client_ws.send(json.dumps({
                            'type': 'stream_notification',
                            'user_id': user_id,
                            'mode': stream_mode
                        }))

            # --- ПОЛУЧИТЬ СПИСОК ЭФИРОВ (для зрителя) ---
            elif msg_type == 'get_streams':
                # Возвращаем список ключей из active_streams
                streams = list(active_streams.keys())
                print(f"[get_streams] user_id={data['user_id']} -> {streams}")
                await websocket.send(json.dumps({
                    'type': 'stream_list',
                    'streams': streams
                }))

            # --- ПРИСОЕДИНИТЬСЯ К ЭФИРУ (зритель) ---
            elif msg_type == 'join_stream':
                user_id = data['user_id']
                streamer_id = data['streamer_id']
                print(f"[join_stream] user_id={user_id} -> streamer_id={streamer_id}")
                if streamer_id in active_streams:
                    # Добавляем зрителя
                    if streamer_id not in viewers:
                        viewers[streamer_id] = []
                    viewers[streamer_id].append(user_id)
                    # Посылаем зрителю "offer" (заглушка) и уведомляем стримера
                    await clients[user_id].send(json.dumps({
                        'type': 'offer',
                        'offer': {},
                        'from': streamer_id
                    }))
                    await active_streams[streamer_id].send(json.dumps({
                        'type': 'viewer_joined',
                        'viewer_id': user_id
                    }))
                else:
                    # Нет такого эфира
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': 'Эфир не найден'
                    }))

            # --- РУЛЕТКА ---
            elif msg_type == 'join_roulette':
                user_id = data['user_id']
                print(f"[join_roulette] user_id={user_id}")
                if roulette_queue and roulette_queue[0] != user_id:
                    partner_id = roulette_queue.pop(0)
                    # Отправляем обоим сообщение "partner"
                    await clients[user_id].send(json.dumps({
                        'type': 'partner',
                        'partner_id': partner_id
                    }))
                    await clients[partner_id].send(json.dumps({
                        'type': 'partner',
                        'partner_id': user_id
                    }))
                else:
                    # Добавляем в очередь, если ещё нет
                    if user_id not in roulette_queue:
                        roulette_queue.append(user_id)

            # --- ICE/SDP (если используете WebRTC) ---
            elif msg_type in ['offer', 'answer', 'candidate']:
                to_id = data.get('to')
                if to_id in clients:
                    target_ws = clients[to_id]
                    print(f"[{msg_type}] from={user_id} to={to_id}")
                    await target_ws.send(json.dumps(data))
                elif to_id in active_streams:
                    target_ws = active_streams[to_id]
                    print(f"[{msg_type}] from={user_id} to_streamer={to_id}")
                    await target_ws.send(json.dumps(data))
                else:
                    print(f"[{msg_type}] to={to_id} not found")

            # --- ЧАТ (общий или к стримеру) ---
            elif msg_type == 'chat_message':
                from_id = data['user_id']
                message_text = data['message']
                to = data.get('to')  # если указано
                print(f"[chat_message] from={from_id}, to={to}, msg={message_text}")

                if to and to in active_streams:
                    # Отправляем сообщение стримеру
                    await active_streams[to].send(json.dumps({
                        'type': 'chat_message',
                        'user_id': from_id,
                        'message': message_text
                    }))
                    # И всем зрителям
                    if to in viewers:
                        for v_id in viewers[to]:
                            if v_id in clients and v_id != from_id:
                                await clients[v_id].send(json.dumps({
                                    'type': 'chat_message',
                                    'user_id': from_id,
                                    'message': message_text
                                }))
                else:
                    # Широковещательное сообщение (всем, кроме отправителя)
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
                print(f"[gift] from={from_id} to={to_id} amount={amount}")
                if to_id in active_streams:
                    await active_streams[to_id].send(json.dumps({
                        'type': 'gift',
                        'user_id': from_id,
                        'amount': amount
                    }))
                else:
                    # если хотим отсылать всем?
                    pass

    except Exception as e:
        print(f"Error in handler: {e}")
    finally:
        # Отключение пользователя
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

# Считываем порт из переменной окружения PORT (Railway, Render и т.д.)
import os
port = int(os.environ.get("PORT", 8080))  # 8080 — дефолт

import websockets

async def main():
    async with websockets.serve(handler, "0.0.0.0", port):
        print(f"Server started on port {port}")
        await asyncio.Future()  # бесконечно ждём

if __name__ == "__main__":
    asyncio.run(main())
