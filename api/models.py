from datetime import timedelta
import secrets
import string
from django.utils import timezone

from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    first_name = None
    last_name = None

    email = models.EmailField('Email', unique=True)

    username = models.CharField('Họ và tên', max_length=150, unique=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    # 👉 THÊM TÊN TIẾNG VIỆT VÀO ĐẦU MỖI TRƯỜNG
    phone = models.CharField('Số điện thoại', max_length=15, blank=True, null=True)
    address = models.TextField('Địa chỉ liên hệ', blank=True, null=True)
    avatar = models.ImageField('Ảnh đại diện', upload_to='avatars/users/', blank=True, null=True)
    role = models.CharField('Vai trò', max_length=20, default='customer')

    def save(self, *args, **kwargs):
        if self.role == 'admin':
            self.is_staff = True
            self.is_superuser = True
        
        elif self.role in ['customer', 'seller']:
            self.is_staff = False
            self.is_superuser = False
            
        super().save(*args, **kwargs)

class Store(models.Model):
    # Dùng ForeignKey để liên kết với User (ownerId)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='store')
    store_name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    address = models.CharField(max_length=255, null=True, blank=True)
    avatar_url = models.ImageField(upload_to='avatars/stores/', max_length=255,null=True, blank=True)
    
    # Kinh doanh
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0.0)
    total_sales = models.PositiveIntegerField(default=0)
    
    # Tài chính
    tax_code = models.CharField(max_length=50, null=True, blank=True)
    tax_type = models.CharField(max_length=50, null=True, blank=True)
    bank_name = models.CharField(max_length=255, null=True, blank=True)
    bank_account = models.CharField(max_length=50, null=True, blank=True)
    
    # eKYC
    front_id_image = models.URLField(null=True, blank=True)
    back_id_image = models.URLField(null=True, blank=True)
    business_license = models.URLField(null=True, blank=True)
    
    # Trạng thái
    verification_status = models.CharField(max_length=20, default='pending')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.store_name
    
class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    icon = models.ImageField(upload_to='categories/icons/', null=True, blank=True)

    def __str__(self):
        return self.name

class Product(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=12, decimal_places=0)
    discount = models.PositiveIntegerField(default=0)
    
    rating = models.FloatField(default=0.0)
    review_count = models.IntegerField(default=0)
    sold_count = models.IntegerField(default=0)
    stock = models.IntegerField(default=100)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, related_name='products')
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
    
class ProductImage(models.Model):
    product = models.ForeignKey(Product, related_name='images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='products/', max_length=255)

    def __str__(self):
        return f"Image for {self.product.name}"
    
class Attribute(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name

class AttributeValue(models.Model):
    attribute = models.ForeignKey(Attribute, related_name='values', on_delete=models.CASCADE)
    value = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.attribute.name}: {self.value}"

class ProductVariant(models.Model):
    product = models.ForeignKey(Product, related_name='variants', on_delete=models.CASCADE)
    price = models.DecimalField(max_digits=12, decimal_places=2) # Hoặc IntegerField tùy bạn
    stock = models.IntegerField(default=0)
    image = models.ImageField(upload_to='variants/', max_length=500, null=True, blank=True)
    
    attribute_values = models.ManyToManyField(AttributeValue, blank=True)

    def __str__(self):
        return f"{self.product.name} - {self.price}VND"

class CartItem(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='cart_items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.IntegerField(default=1)
    variant = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

def generate_order_id():
    # Lấy chuỗi NămThángNgày (VD: 20260615)
    date_str = timezone.now().strftime('%Y%m%d')
    # Tạo ngẫu nhiên 6 kí tự gồm chữ in hoa (A-Z) và số (0-9)
    alphabet = string.ascii_uppercase + string.digits
    random_str = ''.join(secrets.choice(alphabet) for _ in range(6))
    # Ghép lại thành mã hoàn chỉnh (VD: 20260615X7K9V2)
    return f"{date_str}{random_str}"
    
class Order(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Chờ xác nhận'),
        ('processing', 'Chờ lấy hàng'),
        ('shipped', 'Đang giao'),
        ('delivered', 'Đã giao'),
        ('completed', 'Hoàn tất'),
        ('cancelled', 'Đã hủy'),
    )
    id = models.CharField(max_length=20, primary_key=True, default=generate_order_id, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')
    total_amount = models.DecimalField(max_digits=12, decimal_places=0) 
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    address = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    shipping_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.IntegerField(default=1)
    price = models.DecimalField(max_digits=12, decimal_places=0)
    variant = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"{self.quantity} x {self.product.name}"
    
class Review(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    rating = models.IntegerField(default=5) # Số sao từ 1 đến 5
    content = models.TextField(blank=True, null=True) # Nội dung khách khen/chê
    variant = models.CharField(max_length=255, blank=True, null=True) # Lưu lại khách đã mua phân loại nào (VD: Màu Bạc, 256GB)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at'] # Đánh giá mới nhất sẽ tự động lên đầu

class ReviewImage(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='reviews/images/')    

class Chat(models.Model):
    customer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='customer_chats')
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='store_chats')
    last_message = models.TextField(blank=True, null=True)
    last_message_time = models.DateTimeField(auto_now_add=True)
    
    # Biến đếm tin nhắn chưa đọc
    customer_unread_count = models.IntegerField(default=0)
    store_unread_count = models.IntegerField(default=0)
    
    # Trạng thái online (có thể dùng để check xem ai đang mở app)
    customer_is_online = models.BooleanField(default=False)
    store_is_online = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Chat: {self.customer.username} - {self.store.store_name}"

class Message(models.Model):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE) # Ai gửi (có thể là User hoặc Chủ Store)
    content = models.TextField()
    msg_type = models.CharField(max_length=20, default='text') # 'text', 'image', 'product'
    status = models.CharField(max_length=20, default='sent') # 'sent', 'read'
    
    # Các trường đính kèm (nếu là gửi ảnh hoặc sản phẩm)
    image_url = models.URLField(blank=True, null=True)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, blank=True, null=True)
    
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sender.username}: {self.content[:20]}"
    
class OTPToken(models.Model):
    email = models.EmailField(max_length=255)
    otp_code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        return timezone.now() < self.created_at + timedelta(minutes=5)

    def __str__(self):
        return f"OTP của {self.email} - {self.otp_code}"
    
class Voucher(models.Model):
    DISCOUNT_TYPES = (
        ('fixed', 'Giảm số tiền cố định'),
        ('percent', 'Giảm theo phần trăm'),
        ('shipping', 'Miễn phí vận chuyển'),
    )

    # Thông tin cơ bản
    code = models.CharField(max_length=50, unique=True, verbose_name="Mã giảm giá")
    name = models.CharField(max_length=255, verbose_name="Tên chương trình")
    
    # store = null có nghĩa là mã của Toàn sàn (ví dụ: Freeship). Có giá trị = mã của riêng Shop đó.
    store = models.ForeignKey('Store', on_delete=models.CASCADE, null=True, blank=True, related_name='vouchers')
    
    # Thiết lập giảm giá
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPES, default='fixed')
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Mức giảm (VNĐ hoặc %)")
    min_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Đơn tối thiểu")
    max_discount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Giảm tối đa (Dành cho mã %)")
    
    # Giới hạn sử dụng
    usage_limit = models.IntegerField(default=100, verbose_name="Tổng số lượt dùng")
    used_count = models.IntegerField(default=0, verbose_name="Đã dùng")
    
    # Thời gian
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} - {self.name}"

    # Hàm kiểm tra mã còn hiệu lực hay không
    def is_valid(self):
        now = timezone.now()
        return (
            self.is_active and 
            self.start_date <= now <= self.end_date and 
            self.used_count < self.usage_limit
        )