from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CartAPIView, CartItemDeleteAPIView, CategoryViewSet, SendOTPView, StoreViewSet, SystemChatBotView, VerifyOTPView, VoucherViewSet, get_chat_messages, get_or_create_chat, get_store_chats, get_user_chats, login_view, register_view, ProductViewSet, OrderViewSet, google_login, update_avatar, ReviewViewSet
from api import views

# Tạo router tự động cho Product và Order
router = DefaultRouter()
router.register(r'products', ProductViewSet, basename='product')
router.register(r'orders', OrderViewSet, basename='order')
router.register(r'stores', StoreViewSet, basename='store') 
router.register(r'reviews', ReviewViewSet, basename='review')
router.register(r'categories', CategoryViewSet)
router.register(r'vouchers', VoucherViewSet, basename='voucher')

urlpatterns = [
    # API Auth (Login/Register thủ công)
    path('auth/login/', login_view, name='login'),
    path('auth/register/', register_view, name='register'),
    path('auth/google/', google_login, name='google_login'),
    path('auth/send-otp/', SendOTPView.as_view(), name='send_otp'),
    path('auth/verify-otp/', VerifyOTPView.as_view(), name='verify_otp'),

    # API Products & Orders (Tự động)
    path('', include(router.urls)),
    path('cart/', CartAPIView.as_view(), name='cart-api'),
    path('cart/<int:pk>/', CartItemDeleteAPIView.as_view(), name='cart-delete-api'),
    path('update-address/', views.update_address, name='update_address'),
    path('stores/', include(router.urls)), 
    path('update-avatar/', update_avatar),
    path('change_password/', views.change_password, name='change_password'),
    path('chats/user/<int:user_id>/', get_user_chats, name='get_user_chats'),
    path('chats/store/', views.get_store_chats, name='get_store_chats'),
    path('chats/<int:chat_id>/messages/', get_chat_messages, name='get_chat_messages'),
    path('chats/get-or-create/', get_or_create_chat, name='get_or_create_chat'),
    path('chats/update-status/', views.update_online_status, name='update_online_status'),
    path('chats/<int:chat_id>/read/', views.mark_chat_read, name='mark_chat_read'),
    path('chat/system-bot/', SystemChatBotView.as_view(), name='system_chatbot'),
] + router.urls