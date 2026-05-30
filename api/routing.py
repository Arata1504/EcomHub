from django.urls import path
from . import consumers

websocket_urlpatterns = [
    # Nhận link có dạng: ws://127.0.0.1:8000/ws/chat/<id>/
    path('ws/chat/<str:chat_id>/', consumers.ChatConsumer.as_asgi()),
]