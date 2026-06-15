

from urllib import request

from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Attribute, CartItem, Category, Chat, Message, Product, Order, ProductImage, Review, ReviewImage, Store, OrderItem, ProductVariant, AttributeValue, Voucher
import json

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True) # Chỉ cho phép ghi password, không trả về khi xem

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'password', 'role', 'phone', 'address', 'avatar')

    def create(self, validated_data):
        # Hàm này chạy khi đăng ký user mới (để mã hóa mật khẩu)
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            role=validated_data.get('role', 'customer')
        )
        if 'role' in validated_data:
            user.role = validated_data['role']
            user.save()
        return user

class StoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = '__all__'
        read_only_fields = ['owner']

    def get_avatarUrl(self, obj):
        return self.get_avatar_url(obj)

    def get_avatar_url(self, obj):
        request = self.context.get('request')
        
        # 👉 BẮT ĐÚNG TÊN CỘT "avatar_url" TỪ DATABASE CỦA BẠN
        if hasattr(obj, 'avatar_url') and obj.avatar_url:
            if hasattr(obj.avatar_url, 'url'): 
                # Trường hợp khai báo là ImageField
                return request.build_absolute_uri(obj.avatar_url.url) if request else obj.avatar_url.url
            else: 
                # Trường hợp khai báo là CharField (Dò thấy thiếu /media/ sẽ tự động bù vào)
                url_str = str(obj.avatar_url)
                if not url_str.startswith('/media/'): 
                    url_str = f'/media/{url_str}'
                return request.build_absolute_uri(url_str) if request else url_str
        return ""

class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ['id', 'image']

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'icon']

class ProductSerializer(serializers.ModelSerializer):
    store_id = serializers.IntegerField(source='store.id', read_only=True)
    store_name = serializers.CharField(source='store.store_name', read_only=True)
    store_address = serializers.CharField(source='store.address', read_only=True)   
    store_avatar = serializers.SerializerMethodField()

    image = serializers.SerializerMethodField()
    attributes = serializers.SerializerMethodField()
    variants_detail = serializers.SerializerMethodField()
    category_name = serializers.CharField(source='category.name', read_only=True)

    rating_breakdown = serializers.SerializerMethodField()
    rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()

    def get_price_after_discount(self, obj):
        return obj.price - (obj.price * obj.discount / 100)

    class Meta:
        model = Product
        fields = [
            'id', 'store_id', 'store_name', 'store_address', 'store_avatar',
            'name', 'description', 'price', 'category_name', 'image', 'discount',
            'stock', 'sold_count', 'rating', 'review_count', 
            'created_at', 'attributes', 'variants_detail', 'rating_breakdown'   
        ]

    def get_store_avatar(self, obj):
        request = self.context.get('request')
        
        # 👉 BẮT ĐÚNG TÊN CỘT "avatar_url" CỦA BẢNG STORE
        if hasattr(obj, 'store') and obj.store and hasattr(obj.store, 'avatar_url') and obj.store.avatar_url:
            avatar_field = obj.store.avatar_url
            
            if hasattr(avatar_field, 'url'):
                return request.build_absolute_uri(avatar_field.url) if request else avatar_field.url
            else:
                url_str = str(avatar_field)
                if not url_str.startswith('/media/'): 
                    url_str = f'/media/{url_str}'
                return request.build_absolute_uri(url_str) if request else url_str
        return ""

    def get_image(self, obj):
        images = ProductImage.objects.filter(product=obj)
        request = self.context.get('request')
        if request:
            return [request.build_absolute_uri(img.image.url) for img in images]
        return [img.image.url for img in images] if images else []
    
    def get_attributes(self, obj):
        variants = obj.variants.all()
        if not variants.exists():
            return None
        
        attr_dict = {}
        for variant in variants:
            for attr_val in variant.attribute_values.all():
                attr_name = attr_val.attribute.name # Ví dụ: "Mùi hương"
                val = attr_val.value # Ví dụ: "Hương Sen"
                
                if attr_name not in attr_dict:
                    attr_dict[attr_name] = set()
                attr_dict[attr_name].add(val)
        
        for key in attr_dict:
            attr_dict[key] = list(attr_dict[key])
            
        return attr_dict
    
    def create(self, validated_data):
        current_request = self.context.get('view').request
        images_data = current_request.FILES.getlist('image')
        
        # 1. Nhận chuỗi JSON biến thể từ Flutter gửi lên
        variants_json = current_request.data.get('variants', '[]')
        
        product = Product.objects.create(**validated_data)
        
        for image_data in images_data:
            ProductImage.objects.create(product=product, image=image_data)
            
        # 2. XỬ LÝ LƯU BIẾN THỂ VÀO DATABASE
        try:
            import json
            variants_data = json.loads(variants_json)
            
            for idx, v_data in enumerate(variants_data):
                
                # Tạo Variant
                variant = ProductVariant.objects.create(
                    product=product,
                    price=v_data.get('price', product.price),
                    stock=v_data.get('stock', 0)
                )

                variant_image_file = current_request.FILES.get(f'variant_image_{idx}')
                if variant_image_file:
                    variant.image = variant_image_file
                    variant.save()
                
                # Phân tích chuỗi (VD: "Màu: Đỏ, Size: L") để tạo Thuộc tính
                attr_string = v_data.get('attributes', '')
                if attr_string:
                    parts = attr_string.split(',')
                    for part in parts:
                        if ':' in part:
                            attr_name, attr_val = part.split(':', 1)
                            
                            # Lưu vào bảng Attribute và AttributeValue
                            attr_obj, _ = Attribute.objects.get_or_create(name=attr_name.strip())
                            val_obj, _ = AttributeValue.objects.get_or_create(
                                attribute=attr_obj, 
                                value=attr_val.strip()
                            )
                            # Gắn liên kết M2M
                            variant.attribute_values.add(val_obj)
                            
        except Exception as e:
            print(f"Lỗi khi phân tích và tạo biến thể: {e}")
            
        return product

    def update(self, instance, validated_data):
        current_request = self.context.get('view').request
        images_data = current_request.FILES.getlist('image')
        kept_images_json = current_request.data.get('kept_images', '[]')
        
        try:
            kept_images = json.loads(kept_images_json)
        except Exception:
            kept_images = []

        instance = super().update(instance, validated_data)
        
        old_images = instance.images.all()
        for img in old_images:
            is_kept = False
            
            # Duyệt qua từng URL mà Flutter gửi lên
            for kept_url in kept_images:
                # Nếu đường dẫn của ảnh trong DB (ví dụ: /media/abc.jpg) nằm trong URL Flutter gửi lên
                if img.image.url in kept_url or img.image.name in kept_url:
                    is_kept = True
                    break
            
            # Nếu không tìm thấy sự trùng khớp nào -> Xóa
            if not is_kept:
                img.delete()
                
        # 2. THÊM ẢNH MỚI TỪ ĐIỆN THOẠI
        if images_data:
            for image_data in images_data:
                ProductImage.objects.create(product=instance, image=image_data)
                
        # 3. CẬP NHẬT BIẾN THỂ (Giữ nguyên logic của bạn)
        variants_json = current_request.data.get('variants')
        if variants_json is not None:
            try:
                import json
                variants_data = json.loads(variants_json)
                
                incoming_variant_ids = [v.get('id') for v in variants_data if v.get('id')]
                
                instance.variants.exclude(id__in=incoming_variant_ids).delete()

                for idx, v_data in enumerate(variants_data):
                    variant_id = v_data.get('id')
                    price = v_data.get('price', instance.price)
                    stock = v_data.get('stock', 0)

                    if variant_id:
                        try:
                            variant = ProductVariant.objects.get(id=variant_id, product=instance)
                            variant.price = price
                            variant.stock = stock
                            variant.save()
                        except ProductVariant.DoesNotExist:
                            continue
                    else:
                        variant = ProductVariant.objects.create(
                            product=instance, price=price, stock=stock
                        )
                        attr_string = v_data.get('attributes', '')
                        if attr_string:
                            parts = attr_string.split(',')
                            for part in parts:
                                if ':' in part:
                                    attr_name, attr_val = part.split(':', 1)
                                    attr_obj, _ = Attribute.objects.get_or_create(name=attr_name.strip())
                                    val_obj, _ = AttributeValue.objects.get_or_create(attribute=attr_obj, value=attr_val.strip())
                                    variant.attribute_values.add(val_obj)

                    # 👉 FIX LỖI ẢNH BIẾN THỂ:
                    # Chú ý: Ở đây không xóa ảnh cũ của biến thể, chỉ ghi đè ảnh mới nếu có
                    variant_image_file = current_request.FILES.get(f'variant_image_{idx}')
                    if variant_image_file:
                        variant.image = variant_image_file
                        variant.save()
            except Exception as e:
                print(f"Lỗi khi cập nhật biến thể: {e}")
                
        return instance
    
    def get_variants_detail(self, obj):
        variants = obj.variants.all()
        request = self.context.get('request') # Lấy request để tạo link ảnh hoàn chỉnh
        result = []
        for v in variants:
            attrs = {av.attribute.name: av.value for av in v.attribute_values.all()}
            
            # 👉 LOGIC LẤY ẢNH BIẾN THỂ
            v_image_url = ""
            if hasattr(v, 'image') and v.image:
                v_image_url = request.build_absolute_uri(v.image.url) if request else v.image.url

            result.append({
                "id": v.id,
                "price": str(v.price),
                "stock": v.stock,
                "attributes": attrs,
                "image": v_image_url # 👉 Gửi kèm ảnh về cho Flutter
            })
        return result
    
    # 👉 3. Hàm đếm số lượng từng loại sao
    def get_rating_breakdown(self, obj):
        from django.db.models import Count
        reviews = obj.reviews.all()
        total = reviews.count()
        
        # Mặc định tất cả đều là 0
        breakdown = [
            {'stars': 5, 'count': 0, 'percentage': 0.0},
            {'stars': 4, 'count': 0, 'percentage': 0.0},
            {'stars': 3, 'count': 0, 'percentage': 0.0},
            {'stars': 2, 'count': 0, 'percentage': 0.0},
            {'stars': 1, 'count': 0, 'percentage': 0.0},
        ]
        
        if total > 0:
            # Nhóm và đếm trong Database
            counts = reviews.values('rating').annotate(c=Count('id'))
            for item in counts:
                star = item['rating']
                count = item['c']
                index = 5 - star # 5 sao thì nằm ở index 0
                
                if 0 <= index <= 4:
                    breakdown[index]['count'] = count
                    breakdown[index]['percentage'] = round(count / total, 2)
                    
        return breakdown
    
    def get_review_count(self, obj):
        return obj.reviews.count()

    def get_rating(self, obj):
        from django.db.models import Avg
        # Lấy trung bình cộng cột 'rating' của tất cả bài đánh giá
        avg_rating = obj.reviews.aggregate(Avg('rating'))['rating__avg']
        return round(avg_rating, 1) if avg_rating else 0.0
    
class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_image = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem # Bạn cần tạo model này trong models.py nếu chưa có
        fields = ('product', 'product_name', 'product_image', 'quantity', 'price', 'variant')

    def get_product_image(self, obj):
        request = self.context.get('request')
    
        first_image = obj.product.images.first()
        
        if first_image and request:
            return request.build_absolute_uri(first_image.image.url)
        return "" 

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    user_name = serializers.CharField(source='user.first_name', read_only=True, default="Khách hàng")

    class Meta:
        model = Order
        fields = '__all__'
        read_only_fields = ('user', 'total_amount', 'status') # Những trường này server tự tính, app không được sửa

class CartItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    price = serializers.SerializerMethodField()
    
    # Khai báo cả 2 kiểu tên để đảm bảo Flutter (dù dùng chuẩn nào) cũng đọc được
    image_url = serializers.SerializerMethodField()
    imageUrl = serializers.SerializerMethodField() 
    
    product_id = serializers.IntegerField(source='product.id', read_only=True)
    productId = serializers.IntegerField(source='product.id', read_only=True)
    store_id = serializers.IntegerField(source='product.store.id', read_only=True)
    store_name = serializers.CharField(source='product.store.store_name', read_only=True)
    store_address = serializers.CharField(source='product.store.address', read_only=True)
    stock = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        # 👉 Thêm 'imageUrl' vào danh sách
        fields = ['id', 'product_id', 'productId', 'product_name', 'price', 'quantity','store_id', 'store_name', 'store_address', 'image_url', 'imageUrl', 'variant', 'stock']

    # Hàm đồng bộ dữ liệu cho Flutter
    def get_imageUrl(self, obj):
        return self.get_image_url(obj)

    def get_price(self, obj):
        base_price = obj.product.price
        if not obj.variant or obj.variant == 'Mặc định':
            return base_price
        try:
            # Tách chuỗi cực kỳ an toàn (chống lỗi dư khoảng trắng)
            parts = obj.variant.split(',')
            selected_attrs = {p.split(':')[0].strip(): p.split(':')[1].strip() for p in parts if ':' in p}
                
            for v in obj.product.variants.all():
                v_attrs = {av.attribute.name.strip(): av.value.strip() for av in v.attribute_values.all()}
                if v_attrs == selected_attrs:
                    return v.price
        except:
            pass
        return base_price

    def get_image_url(self, obj):
        request = self.context.get('request')
        from .models import ProductImage # Import trực tiếp để tránh lỗi
        
        # 1. TÌM ẢNH BIẾN THỂ
        if obj.variant and obj.variant != 'Mặc định':
            try:
                parts = obj.variant.split(',')
                selected_attrs = {p.split(':')[0].strip(): p.split(':')[1].strip() for p in parts if ':' in p}
                
                for v in obj.product.variants.all():
                    v_attrs = {av.attribute.name.strip(): av.value.strip() for av in v.attribute_values.all()}
                    if v_attrs == selected_attrs:
                        if v.image and hasattr(v.image, 'url'):
                            return request.build_absolute_uri(v.image.url) if request else v.image.url
                        break
            except:
                pass
                
        # 2. TÌM ẢNH SẢN PHẨM CHA
        first_image = ProductImage.objects.filter(product=obj.product).first()
        if first_image and first_image.image and hasattr(first_image.image, 'url'):
            return request.build_absolute_uri(first_image.image.url) if request else first_image.image.url
            
        # 3. DỰ PHÒNG CHỐNG LỖI UI (Trả về ảnh mặc định thay vì bỏ trống)
        return "https://via.placeholder.com/150"
    
    def get_stock(self, obj):
        base_stock = obj.product.stock
        if not obj.variant or obj.variant == 'Mặc định':
            return base_stock
        try:
            parts = obj.variant.split(',')
            selected_attrs = {p.split(':')[0].strip(): p.split(':')[1].strip() for p in parts if ':' in p}
            for v in obj.product.variants.all():
                v_attrs = {av.attribute.name.strip(): av.value.strip() for av in v.attribute_values.all()}
                if v_attrs == selected_attrs:
                    return v.stock # Trả về kho của đúng cái màu/size đó
        except:
            pass
        return base_stock
    
class ReviewImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewImage
        fields = ['id', 'image']

class ReviewSerializer(serializers.ModelSerializer):
    # Lấy tên và avatar của User đã viết đánh giá
    user_name = serializers.CharField(source='user.username', read_only=True)
    user_avatar = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_image = serializers.SerializerMethodField()
    seller_reply = serializers.CharField(read_only=True)
    reply_created_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Review
        fields = ['id', 'user_name', 'user_avatar', 'rating', 'content', 'variant', 'created_at', 'images', 'product_name', 'product_image', 'seller_reply', 'reply_created_at']

    def get_user_avatar(self, obj):
        request = self.context.get('request')
        if hasattr(obj.user, 'avatar') and obj.user.avatar:
            if hasattr(obj.user.avatar, 'url'):
                return request.build_absolute_uri(obj.user.avatar.url) if request else obj.user.avatar.url
            else:
                url_str = str(obj.user.avatar)
                if not url_str.startswith('/media/'): 
                    url_str = f'/media/{url_str}'
                return request.build_absolute_uri(url_str) if request else url_str
                
        return f"https://ui-avatars.com/api/?name={obj.user.username}&background=random"
        
    def get_images(self, obj):
        request = self.context.get('request')
        images = obj.images.all()
        if request:
            return [request.build_absolute_uri(img.image.url) for img in images]
        return [img.image.url for img in images]
    
    def get_product_image(self, obj):
        request = self.context.get('request')
        try:
            if hasattr(obj.product, 'images') and obj.product.images.exists():
                first_img = obj.product.images.first()
                if first_img and first_img.image:
                    return request.build_absolute_uri(first_img.image.url) if request else first_img.image.url
        except Exception:
            pass
            
        return ""

class ChatSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.username', read_only=True)
    customer_image = serializers.SerializerMethodField()
    store_name = serializers.CharField(source='store.store_name', read_only=True)
    store_image = serializers.SerializerMethodField()
    store_owner_id = serializers.IntegerField(source='store.owner.id', read_only=True)

    class Meta:
        model = Chat
        fields = [
            'id', 'customer_id', 'customer_name', 'customer_image',
            'store_id', 'store_name', 'store_image', 'last_message',
            'last_message_time', 'customer_unread_count', 'store_unread_count',
            'customer_is_online', 'store_is_online', 'created_at',
            'store_owner_id'
        ]

    def get_customer_image(self, obj):
        request = self.context.get('request')
        if obj.customer.avatar:
            return request.build_absolute_uri(obj.customer.avatar.url) if request else obj.customer.avatar.url
        return None

    def get_store_image(self, obj):
        request = self.context.get('request')
        if obj.store.avatar_url:
            url = str(obj.store.avatar_url)
            # 1. Đảm bảo có /media/ ở đầu nếu chưa có
            if not url.startswith('/media/'):
                url = '/media/' + url.lstrip('/')
            
            # 2. Encode khoảng trắng (Art H Store -> Art%20H%20Store)
            encoded_url = url.replace(' ', '%20')
            
            return request.build_absolute_uri(encoded_url) if request else f"http://127.0.0.1:8000{encoded_url}"
        return None

class MessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source='sender.username', read_only=True)
    type = serializers.CharField(source='msg_type', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_price = serializers.DecimalField(source='product.price', max_digits=12, decimal_places=0, read_only=True)
    product_image = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'id', 'chat_id', 'sender_id', 'sender_name', 'content',
            'timestamp', 'type', 'status', 'image_url',
            'product_id', 'product_name', 'product_image', 'product_price'
        ]

    def get_product_image(self, obj):
        request = self.context.get('request')
        if obj.product:
            first_image = obj.product.images.first()
            if first_image and request:
                return request.build_absolute_uri(first_image.image.url)
        return None

class VoucherSerializer(serializers.ModelSerializer):
    class Meta:
        model = Voucher
        fields = ['id', 'code', 'name', 'discount_type', 'discount_value', 
                  'min_order_value', 'max_discount', 'usage_limit', 
                  'used_count', 'start_date', 'end_date', 'is_active', 'store_id']
        # Những trường này người bán không được tự ý sửa khi gửi request
        read_only_fields = ('id', 'store', 'used_count')