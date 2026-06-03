import os
import random
import google.generativeai as genai
from django.db.models import Q
from django.core.mail import send_mail
from django.http import JsonResponse
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth import get_user_model
import requests
from rest_framework.exceptions import ValidationError
from rest_framework import viewsets, status, generics
from rest_framework.response import Response
from rest_framework.decorators import action, api_view, parser_classes, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.authtoken.models import Token
from rest_framework.parsers import MultiPartParser, FormParser, settings
from django.contrib.auth import authenticate
from django.db import transaction
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from .models import Category, Chat, Message, OTPToken, Product, Order, OrderItem, ProductVariant, Review, ReviewImage, Store, CartItem
from .serializers import CategorySerializer, ChatSerializer, MessageSerializer, ProductSerializer, OrderSerializer, ReviewSerializer, StoreSerializer, UserSerializer, CartItemSerializer
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
        
        if not user_message:
            return Response({"error": "Vui lòng nhập câu hỏi!"}, status=status.HTTP_400_BAD_REQUEST)

        # 1. TÌM KIẾM DỮ LIỆU TỪ DATABASE
        db_context = ""
        try:
            # A. Bóc tách câu hỏi thành các từ đơn (Ví dụ: "tôi muốn mua MacBook" -> ["tôi", "muốn", "mua", "MacBook"])
            words = user_message.split()
            
            # B. Khai báo danh sách các "Từ cấm" (Stop-words) thường xuất hiện nhưng vô nghĩa trong tìm kiếm
            stop_words = ['tôi', 'muốn', 'mua', 'tìm', 'có', 'bán', 'không', 'cho', 'hỏi', 'về', 'nào', 'ạ', 'nhé', 'cái', 'những']
            
            # C. Tạo bộ lọc linh hoạt
            query = Q()
            for word in words:
                # Chỉ lấy những từ dài hơn 2 ký tự và không nằm trong danh sách từ cấm
                if len(word) > 2 and word.lower() not in stop_words:
                    query |= Q(name__icontains=word) | Q(description__icontains=word)
            
            # D. Phương án dự phòng: Nếu câu hỏi không trích xuất được từ nào (ví dụ khách gõ toàn từ cấm), thì bê nguyên câu đi tìm
            if not query:
                query = Q(name__icontains=user_message) | Q(description__icontains=user_message)

            # E. Truy vấn Database với từ khóa đã lọc (dùng distinct để tránh trùng lặp)
            products = Product.objects.filter(query).distinct()[:5]
            
            if products.exists():
                db_context = "THÔNG TIN SẢN PHẨM TỪ HỆ THỐNG ĐANG BÁN:\n"
                for p in products:
                    db_context += f"- Sản phẩm: {p.name} | Giá: {p.price} VNĐ\n"
            else:
                db_context = "Hệ thống không tìm thấy sản phẩm nào khớp với từ khóa này."

            print(f"✅ [DEBUG DB SUCCESS]: {db_context}")

        except Exception as e:
            db_context = "Dữ liệu sản phẩm tạm thời không truy xuất được."

            print(f"❌ [DEBUG DB ERROR]: Lỗi truy vấn Database - {str(e)}")

        # 2. NHỒI NGỮ CẢNH VÀO CHO AI (PROMPT)
        system_instruction = (
            "Bạn là 'E-Com Assistant', trợ lý ảo thông minh của sàn thương mại điện tử C2C. "
            "Nhiệm vụ của bạn là tư vấn cho khách hàng một cách lịch sự, thân thiện, ngắn gọn và hữu ích. "
            f"\n\n{db_context}\n\n"
            "QUY TẮC: Nếu khách hỏi về sản phẩm, hãy dựa CHÍNH XÁC vào dữ liệu hệ thống cung cấp ở trên để trả lời. "
            "Tuyệt đối không tự bịa ra sản phẩm hoặc giá tiền không có trong hệ thống."
        )

        print(f"🤖 [DEBUG AI PROMPT]: \n{system_instruction}")

        # 3. KẾT NỐI GEMINI VÀ LẤY CÂU TRẢ LỜI
        try:
            gemini_api_key = os.environ.get('GEMINI_API_KEY')
            if not gemini_api_key:
                return Response({"error": "Thiếu API Key trên Server!"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            genai.configure(api_key=gemini_api_key)
            model = genai.GenerativeModel('gemini-2.5-flash')
            
            # Ghép lệnh hệ thống và câu hỏi của khách
            full_prompt = f"{system_instruction}\n\nKhách hàng hỏi: {user_message}"
            response = model.generate_content(full_prompt)
            
            return Response({
                "reply": response.text,
                "bot_name": "E-Com Assistant"
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({"error": f"Lỗi gọi Gemini AI: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
# 1. Quản lý Sản phẩm (Xem, Thêm, Sửa, Xóa)

class ProductPagination(PageNumberPagination):
    page_size = 10

class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    pagination_class = None

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all().order_by('-created_at')
    serializer_class = ProductSerializer
    pagination_class = ProductPagination

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        queryset = super().get_queryset().select_related(
            'store',
        ).prefetch_related(
            'images', 
        )

        
        if self.request.query_params.get('my_store') == 'true' and self.request.user.is_authenticated:
            queryset = queryset.filter(store__owner=self.request.user)
            
        category_id = self.request.query_params.get('category')

        if category_id:
            queryset = queryset.filter(category_id=category_id)

        search_query = self.request.query_params.get('search')

        if search_query:
            queryset = queryset.filter(name__icontains=search_query)
            
        sort_by = self.request.query_params.get('sort')
        if sort_by == 'sales':
            queryset = queryset.order_by('-sold_count') # Bán chạy nhất (giảm dần)
        elif sort_by == 'price_asc':
            queryset = queryset.order_by('price')       # Giá từ thấp đến cao
        elif sort_by == 'price_desc':
            queryset = queryset.order_by('-price')      # Giá từ cao đến thấp

        # 👉 4. THÊM MỚI: Khoảng giá (Min / Max)
        min_price = self.request.query_params.get('min_price')
        max_price = self.request.query_params.get('max_price')
        
        if min_price:
            queryset = queryset.filter(price__gte=min_price) # Lớn hơn hoặc bằng min
        if max_price:
            queryset = queryset.filter(price__lte=max_price) # Nhỏ hơn hoặc bằng max
            
        category_id = self.request.query_params.get('category')
        if category_id:
            queryset = queryset.filter(category_id=category_id)
            
        return queryset

    # Khi tạo sản phẩm, tự động gắn với Store của người dùng đó (nếu là Seller)
    def perform_create(self, serializer):
        # Lấy cửa hàng đầu tiên thuộc về User này (thêm .first())
        store_instance = self.request.user.store.first() 
        
        if store_instance:
            # Truyền đúng "một đối tượng cửa hàng" vào
            serializer.save(store=store_instance)
        else:
            
            raise ValidationError({"error": "Bạn chưa có cửa hàng để thêm sản phẩm."})

class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        as_seller = self.request.query_params.get('as_seller') == 'true'
        if as_seller:
            # Kiểm tra xem User này có Store nào không
            # Nếu dùng quan hệ ForeignKey hoặc OneToOne, hãy đảm bảo tên field là chính xác
            try:
                # Lọc những đơn hàng mà trong đó có sản phẩm thuộc Store của User này
                return Order.objects.filter(items__product__store__owner=user).distinct().order_by('-created_at')
            except Exception as e:
                print(f"Lỗi truy vấn Seller Order: {e}")
                return Order.objects.none()
        return Order.objects.filter(user=user).order_by('-created_at')
    
    def create(self, request, *args, **kwargs):
        data = request.data
        items_data = data.get('items', [])
        
        try:
            shipping_fee = Decimal(str(data.get('shipping_fee', 0)))
        except Exception:
            shipping_fee = Decimal('0')

        if not items_data:
            return Response({'error': 'Giỏ hàng trống'}, status=400)

        try:
            # BẮT ĐẦU VÒNG BẢO VỆ GIAO DỊCH
            with transaction.atomic(): 
                total_amount = shipping_fee
                order_items_to_create = []
                
                for item in items_data:
                    try:
                        qty = int(item.get('quantity', 0))
                        if qty <= 0:
                            raise Exception('Số lượng sản phẩm không hợp lệ!')
                    except ValueError:
                        raise Exception('Số lượng phải là số nguyên!')
                    
                    variant_str = item.get('variant', '')
                    product_id = item['product_id']

                    # 1. KHÓA DÒNG SẢN PHẨM GỐC 
                    # Phải khóa sản phẩm gốc trước để các user xếp hàng đợi nhau
                    product = Product.objects.select_for_update().get(id=product_id)
                    actual_price = product.price

                    # ==========================================
                    # 2. XỬ LÝ NẾU CÓ BIẾN THỂ (MÀU/SIZE)
                    # ==========================================
                    if variant_str and variant_str != 'Mặc định':
                        parts = variant_str.split(',')
                        selected_attrs = {p.split(':')[0].strip(): p.split(':')[1].strip() for p in parts if ':' in p}
                        
                        variant_found = False
                        
                        # Khóa tất cả biến thể của sản phẩm này
                        for v in product.variants.select_for_update().all():
                            v_attrs = {av.attribute.name.strip(): av.value.strip() for av in v.attribute_values.all()}
                            
                            if v_attrs == selected_attrs:
                                variant_found = True
                                
                                # 👉 KIỂM TRA KHO BIẾN THỂ VÀ NÉM LỖI THẲNG TAY
                                if v.stock < qty:
                                    raise Exception(f'Rất tiếc, phân loại "{variant_str}" đã hết hàng hoặc có người mua mất!')
                                
                                # Trừ kho biến thể
                                v.stock -= qty 
                                v.save()
                                actual_price = v.price 
                                break
                                
                        if not variant_found:
                            raise Exception(f'Không tìm thấy phân loại "{variant_str}"')
                            
                        # Vẫn phải trừ tồn kho tổng của sản phẩm gốc (Vì kho tổng = tổng các kho biến thể)
                        if product.stock < qty:
                            raise Exception(f'Sản phẩm "{product.name}" đã hết hàng!')
                        product.stock -= qty
                        product.sold_count += qty
                        product.save()

                    # ==========================================
                    # 3. XỬ LÝ SẢN PHẨM KHÔNG BIẾN THỂ
                    # ==========================================
                    else:
                        if product.stock < qty:
                            raise Exception(f'Sản phẩm "{product.name}" đã hết hàng hoặc có người mua mất!')
                        
                        # Trừ kho sản phẩm gốc
                        product.stock -= qty
                        product.sold_count += qty
                        product.save()

                    # Cộng tiền vào tổng bill
                    total_amount += actual_price * qty
                    
                    # Gom dữ liệu để chuẩn bị tạo OrderItem
                    order_items_to_create.append({
                        'product': product,
                        'quantity': qty,
                        'price': actual_price,
                        'variant': variant_str  
                    })

                # XONG BƯỚC TRỪ KHO AN TOÀN -> BẮT ĐẦU LƯU ĐƠN HÀNG
                order = Order.objects.create(
                    user=request.user,
                    total_amount=total_amount,
                    address=data.get('shipping_address', ''),
                    status='pending'
                )

                for item_data in order_items_to_create:
                    OrderItem.objects.create(
                        order=order,
                        product=item_data['product'],
                        quantity=item_data['quantity'],
                        price=item_data['price'],
                        variant=item_data['variant']
                    )

            # Lấy lại dữ liệu đơn hàng để format json trả về app
            order_refresh = Order.objects.get(id=order.id)
            serializer = self.get_serializer(order_refresh)
            
            return Response(serializer.data, status=201)

        except Product.DoesNotExist:
            return Response({'error': 'Một hoặc nhiều sản phẩm không tồn tại'}, status=404)
        except Exception as e:
            # 👉 Ném chính xác key 'error' để Flutter hiện Dialog
            return Response({'error': str(e)}, status=400)
        
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
        
        # 👉 KIỂM TRA BẢO MẬT: Khách đã mua sản phẩm này chưa?
        # Lục tìm trong OrderItem xem có đơn hàng nào của User này chứa Product này không
        has_bought = OrderItem.objects.filter(
            order__user=self.request.user, 
            product=product
        ).exists()
        
        if not has_bought:
            raise ValidationError({"detail": "Bạn phải mua sản phẩm này thì mới được viết đánh giá!"})
        
        # Lưu Review chính
        review = serializer.save(user=self.request.user, product=product)
        
        # Lưu các ảnh đính kèm (nếu có)
        images_data = self.request.FILES.getlist('images')
        for image_data in images_data:
            ReviewImage.objects.create(review=review, image=image_data)
    # Lọc đánh giá theo Sản phẩm, Số sao, hoặc Có hình ảnh
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # 1. Bắt buộc phải có product_id thì mới hiện đánh giá của đúng sản phẩm đó
        product_id = self.request.query_params.get('product_id')
        if product_id:
            queryset = queryset.filter(product_id=product_id)
            
        # 2. Lọc theo số sao (VD: khách bấm vào tab "5 Sao")
        rating = self.request.query_params.get('rating')
        if rating:
            queryset = queryset.filter(rating=rating)
            
        # 3. Lọc tab "Có hình ảnh"
        has_image = self.request.query_params.get('has_image')
        if has_image == 'true':
            queryset = queryset.exclude(images__isnull=True)

        if self.request.query_params.get('my_reviews') == 'true':
            queryset = queryset.filter(user=self.request.user)
            
        return queryset
        
    # Logic khi khách hàng Gửi đánh giá mới
    def perform_create(self, serializer):
        product_id = self.request.data.get('product_id')
        product = get_object_or_404(Product, id=product_id)
        
        # Lưu Review chính
        review = serializer.save(user=self.request.user, product=product)
        
        # Lưu các ảnh đính kèm (nếu có)
        images_data = self.request.FILES.getlist('images')
        for image_data in images_data:
            ReviewImage.objects.create(review=review, image=image_data)

    def perform_destroy(self, instance):
        # Kiểm tra xem người đang bấm xóa có phải là chủ nhân của bài đánh giá không
        if instance.user != self.request.user:
            raise PermissionDenied("Bạn không có quyền xóa đánh giá của người khác!")
        instance.delete()