import os
import random
import google.generativeai as genai
from datetime import timedelta
from django.db.models import Q, F
from django.core.mail import send_mail
from django.http import JsonResponse
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth import get_user_model
import requests
from rest_framework.exceptions import ValidationError
from rest_framework import viewsets, status, generics, permissions
from rest_framework.response import Response
from rest_framework.decorators import action, api_view, parser_classes, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.authtoken.models import Token
from rest_framework.parsers import MultiPartParser, FormParser, settings
from django.contrib.auth import authenticate
from django.db import transaction
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from .models import Category, Chat, Message, OTPToken, Product, Order, OrderItem, ProductVariant, ProductImage, Review, ReviewImage, Store, CartItem, Voucher
from .serializers import CategorySerializer, ChatSerializer, MessageSerializer, ProductSerializer, OrderSerializer, ReviewSerializer, StoreSerializer, UserSerializer, CartItemSerializer, VoucherSerializer
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from django.contrib.auth.hashers import make_password
from rest_framework.pagination import PageNumberPagination

User = get_user_model()
# --- PHẦN 1: AUTHENTICATION (Đăng nhập/Đăng ký) ---
GOOGLE_WEB_CLIENT_ID = "212535286652-0vniohj0lult5drgpuf5ig4snq69ks6e.apps.googleusercontent.com"

@api_view(['POST'])
def google_login(request):
    token_tu_flutter = request.data.get('idToken')
    
    if not token_tu_flutter:
        return Response({'detail': 'Không tìm thấy Token'}, status=400)

    try:
        # 1. Nhờ Google xác minh xem idToken này có thật không
        idinfo = id_token.verify_oauth2_token(
            token_tu_flutter, 
            google_requests.Request(), 
            GOOGLE_WEB_CLIENT_ID
        )

        # 2. Lấy thông tin từ Google trả về
        email = idinfo['email']
        name = idinfo.get('name', 'Khách hàng Google')
        avatar = idinfo.get('picture', '')

        # 3. Tìm hoặc Tạo tài khoản mới trong hệ thống của bạn
        try:
            # Nếu đã có tài khoản
            user = User.objects.get(email=email)
            user.last_login = timezone.now()
            user.save(update_fields=['last_login'])
        except User.DoesNotExist:
            # Nếu chưa có -> Tạo mới hoàn toàn
            user = User.objects.create(
                username=name,
                email=email,
                role='customer',
                password=make_password(None),
                last_login=timezone.now()
            )
            # Có thể thêm logic tải avatar từ Google về nếu cần

        # 4. Cấp Token thông hành của hệ thống bạn (Giống hệt hàm login_view cũ)
        token, created = Token.objects.get_or_create(user=user)
        return Response({
            'access': token.key, 
            'role': user.role,
            'user_id': user.id,
            'name': user.username,
            "phone": user.phone or "",   
            "address": user.address or "",
            "avatar": user.avatar.url if user.avatar else avatar, # Dùng tạm link avatar của GG
            'last_login': user.last_login.isoformat() if user.last_login else None,
        })

    except ValueError:
        # Token không hợp lệ hoặc hết hạn
        return Response({'detail': 'Xác thực Google thất bại'}, status=400)
    
@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    email = request.data.get('email')
    password = request.data.get('password')
    
    if not email or not password:
        return Response({'detail': 'Vui lòng nhập đầy đủ thông tin'}, status=400)

    # --- LOGIC MỚI: TÌM USER BẰNG EMAIL ---
    user = None
    try:
        user_obj = User.objects.get(email=email)
        user = authenticate(email=user_obj.email, password=password)
    except User.DoesNotExist:
        pass
    
    if user:
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])

        token, created = Token.objects.get_or_create(user=user)
        return Response({
            'access': token.key, # Trả về token để Flutter lưu
            'role': user.role,
            'user_id': user.id,
            'username': user.username,
            "phone": user.phone or '',   
            "address": user.address or '',
            "avatar": user.avatar.url if user.avatar else '',
            'last_login': user.last_login.isoformat() if user.last_login else None,
        })
    else:
        return Response({'detail': 'Email hoặc mật khẩu không đúng'}, status=400)

@api_view(['POST'])
@permission_classes([AllowAny])
def register_view(request):
    serializer = UserSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({'detail': 'Đăng ký thành công'}, status=201)
    return Response(serializer.errors, status=400)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def register_store(request):
    user = request.user
    # 1. Tạo Store
    store = Store.objects.create(owner=user, **request.data)
    # 2. Đổi quyền
    user.role = 'seller'
    user.save()
    return Response({"message": "Thành công"}, status=201)

@api_view(['POST']) # 
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser]) 
def update_avatar(request):
    user = request.user
    avatar_file = request.data.get('avatar')
    if avatar_file:
        user.avatar = avatar_file
        user.save()
        return Response({
            "message": "Cập nhật ảnh đại diện thành công",
            "avatar_url": user.avatar.url
        }, status=200)
    return Response({"error": "Không tìm thấy file ảnh"}, status=400)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def get_or_create_chat(request):
    store_id = request.data.get('store_id')
    try:
        store = Store.objects.get(id=store_id)
        # Lấy hoặc tạo phòng chat giữa user đang đăng nhập và store
        chat, created = Chat.objects.get_or_create(
            customer=request.user,
            store=store,
            defaults={'last_message': '', 'last_message_time': timezone.now()}
        )
        serializer = ChatSerializer(chat, context={'request': request})
        return Response(serializer.data, status=201 if created else 200)
    except Store.DoesNotExist:
        return Response({'detail': 'Không tìm thấy cửa hàng'}, status=404)

@api_view(['POST'])
def place_order(request):
    # 1. Lấy dữ liệu từ request
    product_id = request.data.get('product_id')
    user_id = request.user.id
    quantity_to_buy = int(request.data.get('quantity', 1)) 
    
    # 👉 LẤY THÊM TRƯỜNG BIẾN THỂ (Sẽ có giá trị "Chọn màu: Brown" hoặc None)
    variant_name = request.data.get('variant') 

    try:
        with transaction.atomic():
            
            # ==========================================
            # TRƯỜNG HỢP 1: MUA SẢN PHẨM CÓ BIẾN THỂ
            # ==========================================
            if variant_name:
                # 👉 Khóa đúng dòng của Biến thể đó lại
                # (Thay 'attribute_values' bằng đúng tên cột lưu chữ "Chọn màu: Brown" trong DB của bạn)
                variant = ProductVariant.objects.select_for_update().get(
                    product_id=product_id, 
                    attribute_values=variant_name 
                )

                if variant.stock >= quantity_to_buy:
                    # Trừ kho của biến thể
                    variant.stock -= quantity_to_buy
                    variant.save()

                    # Tạo đơn hàng
                    Order.objects.create(
                        user_id=user_id,
                        product_id=product_id, # Lưu ý: dùng product_id thay vì product nguyên cục
                        # variant=variant_name, # Mở comment dòng này nếu bảng Order của bạn có cột lưu tên biến thể
                        quantity=quantity_to_buy,
                        total_price=variant.product.price * quantity_to_buy # Lấy giá từ sản phẩm gốc
                    )
                    return JsonResponse({"message": "Đặt hàng thành công!"}, status=200)
                else:
                    return JsonResponse({"error": f"Rất tiếc, phân loại '{variant_name}' vừa bị người khác mua mất!"}, status=400)


            # ==========================================
            # TRƯỜNG HỢP 2: MUA SẢN PHẨM KHÔNG CÓ BIẾN THỂ
            # ==========================================
            else:
                # Khóa dòng sản phẩm gốc
                product = Product.objects.select_for_update().get(id=product_id)

                if product.stock >= quantity_to_buy:
                    product.stock -= quantity_to_buy
                    product.save()

                    Order.objects.create(
                        user_id=user_id,
                        product=product,
                        quantity=quantity_to_buy,
                        total_price=product.price * quantity_to_buy
                    )
                    return JsonResponse({"message": "Đặt hàng thành công!"}, status=200)
                else:
                    return JsonResponse({"error": "Rất tiếc, sản phẩm vừa bị người khác mua mất!"}, status=400)

    except Product.DoesNotExist:
        return JsonResponse({"error": "Sản phẩm không tồn tại"}, status=404)
    # 👉 Bắt thêm lỗi nếu tìm không ra phân loại
    except ProductVariant.DoesNotExist: 
        return JsonResponse({"error": "Phân loại sản phẩm không tồn tại"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_chats(request, user_id):
    # Lấy danh sách chat của user, sắp xếp tin nhắn mới nhất lên đầu
    chats = Chat.objects.filter(customer_id=user_id).order_by('-last_message_time')
    serializer = ChatSerializer(chats, many=True, context={'request': request})
    return Response(serializer.data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_store_chats(request):
    store = Store.objects.filter(owner=request.user).first()
    
    if store:
        chats = Chat.objects.filter(store=store).order_by('-last_message_time')
        serializer = ChatSerializer(chats, many=True, context={'request': request})
        return Response(serializer.data)
    
    return Response([])

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_chat_messages(request, chat_id):
    # Lấy toàn bộ tin nhắn của 1 đoạn chat, xếp theo thứ tự thời gian cũ -> mới
    messages = Message.objects.filter(chat_id=chat_id).order_by('timestamp')
    serializer = MessageSerializer(messages, many=True, context={'request': request})
    return Response(serializer.data)

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_online_status(request):
    user_id = request.user.id
    is_online = request.data.get('is_online', False)
    # Cập nhật trạng thái vào bảng Chat cho tất cả các cuộc hội thoại mà user này tham gia
    Chat.objects.filter(customer_id=user_id).update(customer_is_online=is_online)
    Chat.objects.filter(store__owner=request.user).update(store_is_online=is_online)
    return Response({'status': 'success'})

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def mark_chat_read(request, chat_id):
    try:
        chat = Chat.objects.get(id=chat_id)
        is_store = request.data.get('is_store', False)
        
        # Nếu là Chủ shop đọc -> Reset số đếm của Cửa hàng
        if is_store:
            chat.store_unread_count = 0
        # Nếu là Khách hàng đọc -> Reset số đếm của Khách hàng
        else:
            chat.customer_unread_count = 0
            
        chat.save()
        return Response({'status': 'success'})
    except Chat.DoesNotExist:
        return Response({'error': 'Không tìm thấy đoạn chat'}, status=404)

@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_address(request):
    user = request.user
    new_address = request.data.get('address')
    new_phone = request.data.get('phone')
    
    fields_to_update = []
    
    if new_address:
        user.address = new_address
        fields_to_update.append('address')
        
    if new_phone:
        user.phone = new_phone
        fields_to_update.append('phone')
        
    if fields_to_update:
        user.save(update_fields=fields_to_update) # Lưu thẳng vào bảng User
        return Response({
            "message": "Cập nhật thông tin thành công", 
            "address": user.address,
            "phone": user.phone
        }, status=200)
        
    return Response({"error": "Vui lòng cung cấp địa chỉ hợp lệ"}, status=400)

# --- PHẦN 2: VIEWSETS (CRUD Tự động) ---
class SendOTPView(APIView):
    """API gửi mã OTP qua HTTP API của Brevo (Vượt tường lửa Render)"""
    def post(self, request):
        email = request.data.get('email', '').strip()
        if not email:
            return Response({"error": "Vui lòng cung cấp địa chỉ Email!"}, status=status.HTTP_400_BAD_REQUEST)
            
        # Kiểm tra xem email đã tồn tại trong hệ thống chưa
        if User.objects.filter(email=email).exists():
            return Response({"error": "Email này đã được đăng ký trên hệ thống rồi!"}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Sinh mã OTP và lưu vào Database
        otp = f"{random.randint(100000, 999999)}"
        OTPToken.objects.filter(email=email).delete()
        OTPToken.objects.create(email=email, otp_code=otp)

        # 2. Chuẩn bị nội dung mail
        subject = "Mã Xác Thực Tạo Tài Khoản - E-Commerce Hub"
        message = (
            f"Chào bạn,\n\n"
            f"Bạn đang thực hiện đăng ký tài khoản mới trên hệ thống E-Commerce Hub.\n"
            f"Mã OTP xác thực Gmail của bạn là: {otp}\n\n"
            f"Mã này có hiệu lực trong 5 phút. Vui lòng không chia sẻ mã này cho bất kỳ ai."
        )
        
        # 3. 👉 BẮN REQUEST QUA CỔNG HTTPS CỦA BREVO (Bỏ qua cấu hình SMTP cũ)
        api_key = os.environ.get('BREVO_API_KEY') # Lấy khóa API từ Render
        url = "https://api.brevo.com/v3/smtp/email"
        
        headers = {
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json"
        }
        
        payload = {
            # THAY EMAIL DƯỚI ĐÂY bằng chính email bạn vừa dùng để đăng ký tài khoản Brevo
            "sender": {"name": "E-Commerce Hub", "email": "tranminhtan2003@gmail.com"}, 
            "to": [{"email": email}],
            "subject": subject,
            "textContent": message
        }
        
        try:
            # Gửi tín hiệu HTTP POST đi
            response = requests.post(url, json=payload, headers=headers)
            
            # Nếu Brevo báo thành công (Mã 200, 201 hoặc 202)
            if response.status_code in [200, 201, 202]:
                return Response({"message": "Mã OTP đã được gửi thành công!"}, status=status.HTTP_200_OK)
            else:
                return Response({"error": f"Máy chủ mail từ chối: {response.text}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
        except Exception as e:
            return Response({"error": f"Lỗi gọi API ngoại: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class VerifyOTPView(APIView):
    """API đối chiếu mã OTP từ App Flutter gửi lên"""
    def post(self, request):
        email = request.data.get('email', '').strip()
        otp_code = request.data.get('otp_code', '').strip()

        try:
            otp_token = OTPToken.objects.filter(email=email, otp_code=otp_code).latest('created_at')
        except OTPToken.DoesNotExist:
            return Response({"error": "Mã xác thực OTP không chính xác!"}, status=status.HTTP_400_BAD_REQUEST)

        if not otp_token.is_valid():
            return Response({"error": "Mã OTP của bạn đã hết hạn!"}, status=status.HTTP_400_BAD_REQUEST)

        otp_token.delete() # Xác thực xong thì xóa mã tránh dùng lại
        return Response({"message": "Xác thực thành công!"}, status=status.HTTP_200_OK)

class SystemChatBotView(APIView):
    """API Trợ lý ảo AI - Đọc Database và trả lời khách hàng"""
    def post(self, request):
        user_message = request.data.get('message', '').strip()
        # Hứng "trí nhớ" từ Flutter gửi lên
        previous_message = request.data.get('previous_message', '').strip()

        full_history = request.data.get('full_history', [])
        
        if not user_message:
            return Response({"error": "Vui lòng nhập câu hỏi!"}, status=status.HTTP_400_BAD_REQUEST)
        
        user = request.user

        db_context = ""
        personal_context = ""
        try:
            # === PHẦN 1: TÌM KIẾM SẢN PHẨM  ===
            stop_words = ['tôi', 'muốn', 'mua', 'tìm', 'có', 'bán', 'không', 'cho', 'hỏi', 'về', 'nào', 'ạ', 'nhé', 'cái', 'những', 'một', 'chiếc', 'loại', 'này', 'đó', 'bao', 'nhiêu', 'rồi', 'đã', 'sản', 'phẩm', 'được', 'thì', 'là', 'nữa', 'đi', 'kèm', 'với', 'các', 'xin', 'chào', 'hi', 'hello', 'ơi']
            
            # Viết 1 hàm nhỏ ẩn bên trong để tái sử dụng logic tìm kiếm
            def do_search(text):
                for char in [',', '.', '?', '!', ';', ':']:
                    text = text.replace(char, ' ')

                words = text.split()
                core_words = [w for w in words if len(w) > 2 and w.lower() not in stop_words]
                phrase = " ".join(core_words)
                
                res = Product.objects.none()
                if phrase:
                    res = Product.objects.filter(Q(name__icontains=phrase) | Q(description__icontains=phrase)).distinct()[:20]
                
                if not res.exists() and core_words:
                    and_query = Q()
                    for word in core_words:
                        q = Q(name__icontains=word) | Q(description__icontains=word)
                        if not and_query:
                            and_query = q
                        else:
                            and_query &= q
                    res = Product.objects.filter(and_query).distinct()[:20]
                return res

            # LƯỚI LỌC 1: Thử tìm kết hợp cả câu cũ và câu mới
            search_text = f"{previous_message} {user_message}"
            products = do_search(search_text)
            
            # LƯỚI LỌC 2 (CỨU CÁNH): Nếu câu mới làm hỏng kết quả, bỏ câu mới, chỉ tìm bằng câu cũ!
            if not products.exists() and user_message:
                products = do_search(user_message)

            if not products.exists() and previous_message:
                products = do_search(previous_message)

            # Ráp dữ liệu gửi cho AI (Code cũ của bạn)
            if products.exists():
                db_context = "THÔNG TIN CHI TIẾT CÁC SẢN PHẨM TRONG HỆ THỐNG:\n\n"
                for p in products:
                    sold_qty = getattr(p, 'sold', getattr(p, 'sold_count', getattr(p, 'sold_quantity', 0)))
                    stock_qty = getattr(p, 'stock', getattr(p, 'quantity', 'Không xác định'))
                    desc = getattr(p, 'description', getattr(p, 'detail', 'Không có mô tả'))
                    category = p.category.name if hasattr(p, 'category') and p.category else 'Chưa phân loại'

                    db_context += (
                        f"Tên sản phẩm: {p.name}\n"
                        f"- Giá bán: {p.price} VNĐ\n"
                        f"- Phân loại/Danh mục: {category}\n"
                        f"- Số lượng tồn kho: {stock_qty} cái | Đã bán được: {sold_qty} cái\n"
                        f"- Mô tả chi tiết & Thông số kỹ thuật: {desc}\n"
                        f"--------------------------------------------------\n"
                    )
            else:
                db_context = (
                    "GHI CHÚ HỆ THỐNG: Hiện tại không có sản phẩm nào khớp với từ khóa.\n"
                    "LƯU Ý ĐẶC BIỆT DÀNH CHO AI: Nếu khách hàng chỉ đang chào hỏi (Xin chào, Hi, Hello...) "
                    "thì bạn hãy chào lại thật thân thiện và tuyệt đối KHÔNG ĐƯỢC nhắc đến việc 'không tìm thấy sản phẩm'."
                )

            # === PHẦN 2: LẤY LỊCH SỬ CÁ NHÂN (NẾU USER ĐÃ ĐĂNG NHẬP) ===
            if user.is_authenticated:
                # Phân tích xem khách có đang hỏi về thông tin cá nhân không
                personal_keywords = ['tôi', 'của tôi', 'đơn hàng', 'đã mua', 'nhắn tin', 'lịch sử', 'cửa hàng này']
                is_asking_personal = any(kw in user_message.lower() or kw in previous_message.lower() for kw in personal_keywords)

                if is_asking_personal:
                    personal_context = "\n--- THÔNG TIN CÁ NHÂN CỦA KHÁCH HÀNG (CHỈ DÙNG KHI ĐƯỢC HỎI) ---\n"
                    
                    # A. Lấy lịch sử mua hàng (Ví dụ: 20 đơn hàng gần nhất)
                    # Lưu ý: Đổi tên Model Order và các trường cho khớp với Database của bạn
                    recent_orders = Order.objects.filter(user=user).order_by('-created_at')[:20]
                    if recent_orders.exists():
                        personal_context += "Lịch sử mua hàng:\n"
                        for order in recent_orders:
                            personal_context += f"- Đã mua '{order.product.name}' từ cửa hàng '{order.seller.shop_name}' vào ngày {order.created_at.strftime('%d/%m/%Y')}. Trạng thái: {order.status}\n"
                    else:
                        personal_context += "Khách hàng chưa từng mua sản phẩm nào trên hệ thống.\n"

                    # B. Lấy lịch sử nhắn tin với các cửa hàng
                    # Lưu ý: Đổi tên Model Message cho khớp
                    recent_chats = Chat.objects.filter(customer=user).values_list('store__name', flat=True).distinct()
                    if recent_chats:
                        shops = ", ".join(recent_chats)
                        personal_context += f"Lịch sử nhắn tin: Khách hàng đã từng nhắn tin với các cửa hàng: {shops}.\n"
                    else:
                        personal_context += "Khách hàng chưa từng nhắn tin với cửa hàng nào.\n"
                
        except Exception as e:
            db_context = "Dữ liệu sản phẩm tạm thời không truy xuất được."
        
        product_list_data = []
        if 'products' in locals() and products.exists():
            # Sử dụng ProductSerializer có sẵn của bạn để biến DB thành JSON
            product_list_data = ProductSerializer(products[:5], many=True, context={'request': request}).data

        # 2. PROMPT AI ĐÃ CÓ TRÍ NHỚ
        history_text = ""
        for msg in full_history:
            role = "Khách hàng" if msg.get('role') == 'user' else "Trợ lý AI (Bạn)"
            history_text += f"{role}: {msg.get('content')}\n"

        system_instruction = (
            "Bạn là 'E-Com Assistant', chuyên gia tư vấn bán hàng cấp cao của sàn thương mại điện tử C2C.\n\n"
            f"{db_context}\n\n"
            "--- LỊCH SỬ TRÒ CHUYỆN TỪ ĐẦU ĐẾN NAY ---\n"
            f"{history_text}\n"
            "-----------------------------------------\n"
            "NHIỆM VỤ CỦA BẠN:\n"
            "1. Đọc toàn bộ 'LỊCH SỬ TRÒ CHUYỆN' ở trên để hiểu ngữ cảnh khách hàng đang muốn gì, sản phẩm nào.\n"
            "2. Dựa vào Thông tin hệ thống cung cấp để trả lời câu hỏi mới nhất một cách lịch sự, thân thiện.\n"
            "3. TUYỆT ĐỐI KHÔNG tự bịa ra thông số hay giá tiền không có trong hệ thống.\n"
        )

        try:
            gemini_api_key = os.environ.get('GEMINI_API_KEY')
            genai.configure(api_key=gemini_api_key)
            model = genai.GenerativeModel('gemini-2.5-flash')
            
            full_prompt = f"{system_instruction}\nCâu hỏi mới nhất của Khách hàng: {user_message}"
            response = model.generate_content(full_prompt)
            
            return Response({
                "reply": response.text,
                "bot_name": "E-Com Assistant"
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            error_msg = str(e)
            # 👉 NẾU LÀ LỖI 429 (QUÁ TẢI)
            if "429" in error_msg or "exceeded" in error_msg.lower():
                import re
                # Dùng Regex để tìm con số giây bị khóa trong chuỗi lỗi của Google
                match = re.search(r'retry in ([\d\.]+)s', error_msg)
                if match:
                    # Lấy số giây và làm tròn lên
                    wait_time = int(float(match.group(1))) + 1 
                    friendly_error = f"Dạ, trợ lý AI đang quá tải lượt hỏi do có nhiều người truy cập. Vui lòng chờ {wait_time} giây nữa rồi nhắn lại cho mình nhé!"
                else:
                    # Phương án dự phòng nếu Google đổi mẫu câu báo lỗi
                    friendly_error = "Dạ, trợ lý AI đang bị kẹt mạng một chút. Vui lòng chờ khoảng 30 giây nữa rồi nhắn lại cho mình nhé!"
                
                return Response({"error": friendly_error}, status=status.HTTP_429_TOO_MANY_REQUESTS)
            
            # Nếu là các lỗi sập server khác
            return Response({"error": "Dạ, hệ thống đang bảo trì, bạn vui lòng thử lại sau nhé!"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# 1. Quản lý Sản phẩm (Xem, Thêm, Sửa, Xóa)

class ProductPagination(PageNumberPagination):
    page_size = 10

class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    pagination_class = None

    def get_queryset(self):
        queryset = Category.objects.all()
        store_id = self.request.query_params.get('store_id')

        if store_id:
            queryset = queryset.filter(products__store_id=store_id).distinct()

        return queryset

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all().order_by('-created_at')
    serializer_class = ProductSerializer
    pagination_class = None
    filterset_fields = ['store_id', 'category_id']

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        queryset = Product.objects.select_related('store', 'category').prefetch_related('images', 'variants')
        
        # Lấy các tham số từ URL
        store_id = self.request.query_params.get('store_id')
        category_param = self.request.query_params.get('category')
        
        search_query = self.request.query_params.get('search') or self.request.query_params.get('name') or self.request.query_params.get('q')

        if search_query:
            queryset = queryset.filter(name__icontains=search_query)

        if store_id:
            queryset = queryset.filter(store_id=store_id)

        if category_param:
            if category_param.isdigit():
                queryset = queryset.filter(category_id=category_param)
            else:
                queryset = queryset.filter(category__name=category_param)

        return queryset.order_by('-created_at')

    def perform_create(self, serializer):
        store_instance = self.request.user.store.first() 
        
        if store_instance:
            product = serializer.save(store=store_instance)
            
            images_data = self.request.FILES.getlist('images') 
            for image_data in images_data:
                ProductImage.objects.create(product=product, image=image_data)
        else:
            raise ValidationError({"error": "Bạn chưa có cửa hàng để thêm sản phẩm."})

    def perform_update(self, serializer):
        product = serializer.save()
        
        images_data = self.request.FILES.getlist('images')
        
        if images_data:
            for image_data in images_data:
                ProductImage.objects.create(product=product, image=image_data)

class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all().order_by('-created_at')
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        
        three_days_ago = timezone.now() - timedelta(days=3)
        auto_completed = Order.objects.filter(
            status='delivered', 
            delivered_at__lte=three_days_ago
        )
        if auto_completed.exists():
            auto_completed.update(status='completed')

        if not self.request.user.is_authenticated:
            return queryset.none()

        store_id = self.request.query_params.get('store_id')
        as_seller = self.request.query_params.get('as_seller')
        
        if store_id:
            return queryset.filter(items__product__store_id=store_id).distinct()
            
        elif as_seller == 'true':
            return queryset.filter(items__product__store__owner=self.request.user).distinct()

        # 👉 NẾU LÀ NGƯỜI MUA BÌNH THƯỜNG: Chỉ trả về đơn họ đã đặt
        return queryset.filter(user=self.request.user).distinct()

    @action(detail=True, methods=['PATCH'])
    def confirm_received(self, request, pk=None):
        order = self.get_object()
        
        if order.user != request.user:
            return Response({"error": "Bạn không có quyền thao tác đơn hàng này!"}, status=403)
            
        if order.status != 'delivered':
            return Response({"error": "Đơn hàng chưa ở trạng thái Đã giao!"}, status=400)
            
        order.status = 'completed'
        order.save()
        
        return Response({"message": "Cảm ơn bạn đã xác nhận nhận hàng!"}, status=200)
    
    @transaction.atomic 
    def create(self, request, *args, **kwargs):
        user = request.user
        data = request.data
        
        cart_items = data.get('items', [])
        shipping_address = data.get('shipping_address', '')
        shipping_fee = float(data.get('shipping_fee', 0))
        voucher_ids = data.get('voucher_ids', []) 
        
        if not cart_items:
            return Response({"error": "Giỏ hàng trống!"}, status=status.HTTP_400_BAD_REQUEST)

        # 1. PHÂN NHÓM SẢN PHẨM THEO CỬA HÀNG
        store_groups = {}
        total_cart_value = 0
        
        for item in cart_items:
            product = get_object_or_404(Product, id=item['product_id'])
            store_id = product.store_id
            
            if store_id not in store_groups:
                store_groups[store_id] = {'items': [], 'subtotal': 0}
                
            quantity = item.get('quantity', 1)
            price = float(product.price)
            
            store_groups[store_id]['items'].append({
                'product': product,
                'quantity': quantity,
                'price': price,
                'variant': item.get('variant', '')
            })
            store_groups[store_id]['subtotal'] += price * quantity
            total_cart_value += price * quantity

        # 2. XỬ LÝ VOUCHER & TÍNH TỔNG GIẢM GIÁ
        total_discount = 0
        applied_vouchers = []
        
        if voucher_ids:
            now = timezone.now()
            valid_vouchers = Voucher.objects.select_for_update().filter(
                id__in=voucher_ids, is_active=True, start_date__lte=now, end_date__gte=now, used_count__lt=F('usage_limit')
            )
            
            for voucher in valid_vouchers:
                discount = 0
                if voucher.discount_type == 'percent':
                    discount = total_cart_value * (float(voucher.discount_value) / 100)
                    if voucher.max_discount and discount > float(voucher.max_discount):
                        discount = float(voucher.max_discount)
                elif voucher.discount_type == 'shipping':
                    discount = shipping_fee
                else:
                    discount = min(float(voucher.discount_value), total_cart_value)
                    
                total_discount += discount
                applied_vouchers.append(voucher)

        # 3. CHIA ĐỀU CHI PHÍ CHO CÁC ĐƠN HÀNG
        num_stores = len(store_groups)
        split_shipping = shipping_fee / num_stores if num_stores > 0 else 0
        split_discount = total_discount / num_stores if num_stores > 0 else 0

        created_order_ids = []

        # 4. TẠO NHIỀU ĐƠN HÀNG RIÊNG BIỆT (Mỗi Cửa hàng 1 mã đơn)
        for store_id, group in store_groups.items():
            final_total = (group['subtotal'] + split_shipping) - split_discount
            if final_total < 0:
                final_total = 0 

            # Tạo Order cho Cửa hàng này
            order = Order.objects.create(
                user=user,
                total_amount=final_total,
                address=shipping_address,
                status='pending',
                shipping_fee=split_shipping,
            )
            created_order_ids.append(order.id)
            
            # Đẩy sản phẩm vào Order
            for item_data in group['items']:
                OrderItem.objects.create(
                    order=order,
                    product=item_data['product'],
                    quantity=item_data['quantity'],
                    price=item_data['price'],
                    variant=item_data['variant']
                )

        # 5. CẬP NHẬT LƯỢT DÙNG VOUCHER
        for voucher in applied_vouchers:
            voucher.used_count += 1
            voucher.save()
            
        return Response({
            "message": "Đặt hàng thành công!", 
            "order_ids": created_order_ids
        }, status=status.HTTP_201_CREATED)
        
    def partial_update(self, request, *args, **kwargs):
        # Lấy đơn hàng hiện tại trong DB
        order = self.get_object()
        
        # Kiểm tra xem có phải là Seller đang thao tác không
        is_seller = request.query_params.get('as_seller') == 'true'
        new_status = request.data.get('status')
        
        # Nếu là Seller và có gửi trạng thái mới -> Cho phép lưu thẳng vào DB
        if is_seller and new_status:
            order.status = new_status
            order.save() # Lưu thay đổi vào Database
            
            # Trả về dữ liệu mới cho Flutter
            serializer = self.get_serializer(order)
            return Response(serializer.data)
            
        # Nếu là khách hàng bình thường, chạy logic mặc định
        return super().partial_update(request, *args, **kwargs)
    
class CartAPIView(APIView):
    # Bắt buộc phải có Token (đăng nhập) mới được gọi API này
    permission_classes = [IsAuthenticated]

    # HÀM GET: Gửi danh sách giỏ hàng về cho Flutter
    def get(self, request):
        items = CartItem.objects.filter(user=request.user).order_by('-created_at')
        serializer = CartItemSerializer(items, many=True, context={'request': request})
        return Response(serializer.data)

    # HÀM POST: Thêm sản phẩm vào giỏ hoặc tăng số lượng
    def post(self, request):
        product_id = request.data.get('product_id')
        quantity = int(request.data.get('quantity', 1))
        variant = request.data.get('variant', '')

        product = get_object_or_404(Product, id=product_id)

        # Kiểm tra xem món hàng này (cùng màu/size) đã có trong giỏ chưa
        cart_item, created = CartItem.objects.get_or_create(
            user=request.user,
            product=product,
            variant=variant,
            defaults={'quantity': quantity} # Nếu chưa có thì tạo mới với số lượng này
        )

        # Nếu đã có sẵn trong giỏ, chỉ cần cộng dồn số lượng
        if not created:
            cart_item.quantity += quantity
            cart_item.save()

        return Response({"message": "Đã cập nhật giỏ hàng thành công"}, status=200)
    
class CartItemDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    # HÀM DELETE: Xóa 1 sản phẩm khỏi giỏ
    def delete(self, request, pk):
        # Đảm bảo user chỉ xóa được đồ trong giỏ của chính họ
        item = get_object_or_404(CartItem, id=pk, user=request.user)
        item.delete()
        return Response({"message": "Đã xóa sản phẩm"}, status=204)
    
    def patch(self, request, pk):
        try:
            # Tìm món hàng bằng pk
            cart_item = CartItem.objects.get(id=pk, user=request.user)
            
            # Lấy dữ liệu Flutter gửi lên
            new_variant = request.data.get('variant')
            new_quantity = request.data.get('quantity')

            # Cập nhật
            if new_variant is not None:
                cart_item.variant = new_variant
            
            if new_quantity is not None:
                cart_item.quantity = int(new_quantity) # Ép kiểu về số nguyên cho an toàn
                
            cart_item.save()
            return Response({"message": "Cập nhật thành công!"}, status=status.HTTP_200_OK)
            
        except CartItem.DoesNotExist:
            return Response({"error": "Không tìm thấy món hàng này trong giỏ"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    
class StoreViewSet(viewsets.ModelViewSet):
    queryset = Store.objects.all()
    serializer_class = StoreSerializer
    permission_classes = [IsAuthenticated] # Bắt buộc phải có Token mới được gọi

    # 👉 ĐÂY LÀ API /my_store/ MÀ FLUTTER ĐANG TÌM KIẾM
    @action(detail=False, methods=['get'])
    def my_store(self, request):
        try:
            # Tìm cửa hàng do user đang gửi request làm chủ
            store = Store.objects.get(owner_id=request.user.id)
            serializer = self.get_serializer(store)
            return Response(serializer.data)
        except Store.DoesNotExist:
            return Response(
                {"error": "Bạn chưa có cửa hàng nào"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)
        user = self.request.user
        user.role = 'seller'
        user.save()

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [AllowAny()] 
        return [IsAuthenticated()]

class ReviewViewSet(viewsets.ModelViewSet):
    queryset = Review.objects.all().order_by('-created_at')
    serializer_class = ReviewSerializer
    
    # Đọc thì ai cũng xem được, nhưng POST/Sửa/Xóa thì phải đăng nhập
    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        product_id = self.request.data.get('product_id')
        product = get_object_or_404(Product, id=product_id)
        
        has_bought = OrderItem.objects.filter(
            order__user=self.request.user, 
            product=product
        ).exists()
        
        if not has_bought:
            raise ValidationError({"detail": "Bạn phải mua sản phẩm này thì mới được viết đánh giá!"})
            
        has_reviewed = Review.objects.filter(user=self.request.user, product=product).exists()
        if has_reviewed:
            raise ValidationError({"detail": "Bạn đã đánh giá sản phẩm này rồi. Vui lòng cập nhật lại đánh giá cũ!"})
        
        # Lưu Review chính
        review = serializer.save(user=self.request.user, product=product)
        
        # Lưu các ảnh đính kèm (nếu có)
        images_data = self.request.FILES.getlist('images')
        for image_data in images_data:
            ReviewImage.objects.create(review=review, image=image_data)

    def perform_update(self, serializer):
        # Kiểm tra bảo mật: Chỉ chủ nhân mới được sửa bài của mình
        if serializer.instance.user != self.request.user:
            raise ValidationError({"detail": "Bạn không có quyền sửa đánh giá này!"})

        review = serializer.save()
        images_data = self.request.FILES.getlist('images')

        # Nếu khách hàng có chọn tải lên ảnh MỚI -> Xóa hết ảnh cũ và thay bằng ảnh mới
        # Nếu khách không tải ảnh mới -> Giữ nguyên ảnh cũ
        if images_data:
            ReviewImage.objects.filter(review=review).delete()
            for image_data in images_data:
                ReviewImage.objects.create(review=review, image=image_data)

    def get_queryset(self):
        queryset = super().get_queryset()
        
        product_id = self.request.query_params.get('product_id')
        if product_id:
            queryset = queryset.filter(product_id=product_id)
            
        rating = self.request.query_params.get('rating')
        if rating:
            queryset = queryset.filter(rating=rating)
            
        has_image = self.request.query_params.get('has_image')
        if has_image == 'true':
            queryset = queryset.exclude(images__isnull=True)

        if self.request.query_params.get('my_reviews') == 'true':
            if self.request.user.is_authenticated:
                queryset = queryset.filter(user=self.request.user)
            else:
                queryset = queryset.none()

        if self.request.query_params.get('for_my_store') == 'true':
            if self.request.user.is_authenticated:
                queryset = queryset.filter(product__store__owner=self.request.user).distinct()
            else:
                queryset = queryset.none()
            
        return queryset

    def perform_destroy(self, instance):
        # Kiểm tra xem người đang bấm xóa có phải là chủ nhân của bài đánh giá không
        if instance.user != self.request.user:
            # Đổi PermissionDenied thành ValidationError để tránh lỗi chưa import thư viện
            raise ValidationError({"detail": "Bạn không có quyền xóa đánh giá của người khác!"})
        instance.delete()

    @action(detail=True, methods=['PATCH'])
    def reply(self, request, pk=None):
        review = self.get_object()
        seller_reply = request.data.get('seller_reply')

        # Kiểm tra bảo mật: Người đang gọi API có phải là Chủ của cửa hàng bán món đồ này không?
        if review.product.store.owner != request.user:
            return Response(
                {"error": "Bạn không có quyền phản hồi đánh giá của cửa hàng khác!"}, 
                status=status.HTTP_403_FORBIDDEN
            )

        if not seller_reply:
            return Response(
                {"error": "Vui lòng nhập nội dung phản hồi."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Lưu câu trả lời
        review.seller_reply = seller_reply
        review.reply_created_at = timezone.now()
        review.save()

        # Trả về dữ liệu đánh giá mới đã có phản hồi
        serializer = self.get_serializer(review)
        return Response({
            "message": "Đã gửi phản hồi thành công!",
            "review": serializer.data
        }, status=status.HTTP_200_OK)

class VoucherViewSet(viewsets.ModelViewSet):
    serializer_class = VoucherSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        
        if hasattr(user, 'store') and user.store.exists():
            my_store = user.store.first()
            return Voucher.objects.filter(store=my_store).order_by('-start_date')
        return Voucher.objects.none()

    def perform_create(self, serializer):
        user = self.request.user
        
        if not hasattr(user, 'store') or not user.store.exists():
            raise ValidationError({"error": "Bạn phải tạo cửa hàng trước khi tạo mã giảm giá."})
        
        my_store = user.store.first()
        code = serializer.validated_data.get('code')
        
        if Voucher.objects.filter(store=my_store, code=code).exists():
            raise ValidationError({"error": "Mã giảm giá này đã tồn tại trong cửa hàng của bạn."})

        serializer.save(store=my_store)

    @action(detail=False, methods=['get'])
    def available(self, request):
        now = timezone.now()
        product_ids = request.query_params.get('product_ids', '')
        
        queryset = Voucher.objects.filter(
            is_active=True,
            start_date__lte=now,
            end_date__gte=now,
            used_count__lt=F('usage_limit')
        )
        
        if product_ids:
            p_ids = [int(pid) for pid in product_ids.split(',') if pid.isdigit()]
            # Lấy danh sách store_id của các sản phẩm này
            store_ids = Product.objects.filter(id__in=p_ids).values_list('store_id', flat=True).distinct()
            
            # Lấy mã của store đó HOẶC mã toàn sàn (store__isnull=True)
            queryset = queryset.filter(Q(store_id__in=store_ids) | Q(store__isnull=True))
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)