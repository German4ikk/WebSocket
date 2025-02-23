import asyncio
import websockets
import json

clients = {}  # Хранит подключённых пользователей
active_streams = {}  # Активные эфиры {streamer_id: websocket}
roulette_queue = []  # Очередь для рулетки
viewers = {}  # Зрители {streamer_id: [viewer_ids]}

async def handler(websocket, path):
    user_id = None
    try:
        async for message in websocket:
            data = json.loads(message)
            if data['type'] == 'register':
                user_id = data['user_id']
                clients[user_id] = websocket
                mode = data.get('mode', 'viewer')
                await websocket.send(json.dumps({'type': 'connected', 'mode': mode}))
            elif data['type'] == 'start_stream':
                user_id = data['user_id']
                active_streams[user_id] = websocket
                await websocket.send(json.dumps({'type': 'stream_started', 'user_id': user_id, 'mode': 'stream'}))
                # Уведомляем всех зрителей о новом эфире
                for uid, client in clients.items():
                    if uid != user_id:
                        await client.send(json.dumps({'type': 'stream_notification', 'user_id': user_id, 'mode': 'stream'}))
            elif data['type'] == 'join_roulette':
                user_id = data['user_id']
                if roulette_queue and roulette_queue[0] != user_id:
                    partner_id = roulette_queue.pop(0)
                    await clients[user_id].send(json.dumps({'type': 'partner', 'partner_id': partner_id}))
                    await clients[partner_id].send(json.dumps({'type': 'partner', 'partner_id': user_id}))
                else:
                    if user_id not in roulette_queue:
                        roulette_queue.append(user_id)
            elif data['type'] == 'join_stream':
                user_id = data['user_id']
                streamer_id = data['streamer_id']
                if streamer_id in active_streams:
                    if streamer_id not in viewers:
                        viewers[streamer_id] = []
                    viewers[streamer_id].append(user_id)
                    await clients[user_id].send(json.dumps({'type': 'offer', 'offer': {}, 'from': streamer_id}))  # Ведущий отправляет поток
                    await active_streams[streamer_id].send(json.dumps({'type': 'viewer_joined', 'viewer_id': user_id}))
            elif data['type'] in ['offer', 'answer', 'candidate']:
                to_id = data['to']
                if to_id in clients or to_id in active_streams:
                    target = clients.get(to_id, active_streams.get(to_id))
                    await target.send(json.dumps(data))
            elif data['type'] == 'chat_message':
                user_id = data['user_id']
                message = data['message']
                to = data.get('to')
                if to and to in active_streams:
                    await active_streams[to].send(json.dumps({'type': 'chat_message', 'user_id': user_id, 'message': message}))
                    # Отправляем сообщение всем зрителям этого стрима
                    if to in viewers:
                        for viewer_id in viewers[to]:
                            if viewer_id in clients and viewer_id != user_id:
                                await clients[viewer_id].send(json.dumps({'type': 'chat_message', 'user_id': user_id, 'message': message}))
                else:
                    for uid, client in clients.items():
                        if uid != user_id:
                            await client.send(json.dumps({'type': 'chat_message', 'user_id': user_id, 'message': message}))
            elif data['type'] == 'gift':
                user_id = data['user_id']
                to = data['to']
                amount = data['amount']
                if to in active_streams:
                    await active_streams[to].send(json.dumps({'type': 'gift', 'user_id': user_id, 'amount': amount}))
                    # Логика записи подарков (можно добавить в базу данных)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if user_id in clients:
            del clients[user_id]
        if user_id in active_streams:
            del active_streams[user_id]
        if user_id in roulette_queue:
            roulette_queue.remove(user_id)
        # Удаляем зрителя из списка
        for stream_id, viewer_list in list(viewers.items()):
            if user_id in viewer_list:
                viewer_list.remove(user_id)
                if not viewer_list:
                    del viewers[stream_id]

start_server = websockets.serve(handler, "0.0.0.0", 5000)
asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()
