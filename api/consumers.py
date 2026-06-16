import json
from urllib.parse import parse_qs
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Chat, Message, User
from rest_framework.authtoken.models import Token

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.chat_id = self.scope['url_route']['kwargs']['chat_id']
        self.room_group_name = f'chat_{self.chat_id}'
        
        # 1. Lấy token từ URL query string
        query_string = self.scope['query_string'].decode()
        query_params = parse_qs(query_string)
        token_key = query_params.get('token', [None])[0]

        # 2. Truy vấn user_id từ Token
        self.connected_user_id = None
        if token_key:
            user_id = await self.get_user_id_from_token(token_key)
            if user_id:
                self.connected_user_id = user_id

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()
        if self.connected_user_id:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'presence_update',
                    'is_online': True,
                    'user_id': self.connected_user_id
                }
            )

    @database_sync_to_async
    def get_user_id_from_token(self, token_key):
        try:
            token = Token.objects.get(key=token_key)
            return token.user_id
        except Token.DoesNotExist:
            return None

    async def disconnect(self, close_code):
        if hasattr(self, 'connected_user_id') and self.connected_user_id:
            await self.set_offline_status(self.connected_user_id)
            
            # Đảm bảo lệnh này được gọi ĐÚNG VÀ CÓ DỮ LIỆU
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'presence_update',
                    'is_online': False,
                    'user_id': self.connected_user_id
                }
            )
        
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def presence_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'presence_update',
            'is_online': event['is_online'],
            'user_id': event['user_id']
        }))

    @database_sync_to_async
    def set_offline_status(self, user_id):
        # Lấy user từ self.scope["user"]
        try:
             user = User.objects.get(id=user_id)
             Chat.objects.filter(customer=user).update(customer_is_online=False)
             Chat.objects.filter(store__owner=user).update(store_is_online=False)
        except User.DoesNotExist:
             print(f"Không tìm thấy user với id {user_id} để set offline.")

    # Nhận dữ liệu từ Flutter gửi lên
    async def receive(self, text_data):
        text_data_json = json.loads(text_data)
        action = text_data_json.get('action')
        data = text_data_json.get('data')

        if action == 'send_message':
            msg = await self.save_message(data['chat_id'], data['sender_id'], data['content'])
            
            # Phát tin nhắn (Broadcast) về lại cho Flutter
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'chat_message',
                    'message': {
                        'id': msg.id,
                        'chat_id': msg.chat_id,
                        'sender_id': msg.sender_id,
                        'content': msg.content,
                        'timestamp': msg.timestamp.isoformat(),
                        'type': msg.msg_type,
                        'status': msg.status
                    }
                }
            )
        elif action == 'mark_read':
            await self.mark_messages_as_read(data['chat_id'], data['user_id'])
            # Phát loa thông báo cho người kia biết là mình đã xem
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'messages_read',
                    'reader_id': data['user_id']
                }
            )
            
    async def messages_read(self, event):
        await self.send(text_data=json.dumps({
            'type': 'messages_read',
            'reader_id': event['reader_id']
        }))

    @database_sync_to_async
    def mark_messages_as_read(self, chat_id, reader_id):
        # Cập nhật DB: Tất cả tin nhắn do người kia gửi sẽ chuyển thành 'read'
        Message.objects.filter(chat_id=chat_id).exclude(sender_id=reader_id).update(status='read')

    # Gửi JSON về lại cho điện thoại
    async def chat_message(self, event):
        message = event['message']
        await self.send(text_data=json.dumps({
            'type': 'new_message',
            'message': message
        }))

    @database_sync_to_async
    def save_message(self, chat_id, sender_id, content):
        try:
            chat = Chat.objects.get(id=chat_id)
            sender = User.objects.get(id=sender_id)
            
            # Lưu tin nhắn mới vào PostgreSQL
            msg = Message.objects.create(chat=chat, sender=sender, content=content)
            
            # Cập nhật thông tin đoạn chat cha
            chat.last_message = content
            chat.last_message_time = msg.timestamp
            if sender.id == chat.customer.id:
                chat.store_unread_count += 1  # Khách gửi -> Chủ shop chưa đọc tăng lên
            else:
                chat.customer_unread_count += 1 # Chủ shop gửi -> Khách chưa đọc tăng lên
            chat.save()
            return msg
        except Chat.DoesNotExist:
            print(f"❌ LỖI CHAT CONSUMER: Không tìm thấy cuộc hội thoại ID={chat_id}")
            raise
        except User.DoesNotExist:
            print(f"❌ LỖI CHAT CONSUMER: Không tìm thấy User ID={sender_id}")
            raise
        except Exception as e:
            print(f"❌ LỖI HỆ THỐNG KHI LƯU DB: {str(e)}")
            raise

    async def send_status_update(self, is_online):
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'status_update',
                'is_online': is_online
            }
        )

    # Hàm này để xử lý sự kiện khi nhận từ group_send
    async def status_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'status_update',
            'is_online': event['is_online']
        }))