from traceback import format_tb

from django import forms
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.contrib import admin
from django.contrib.auth.forms import UserChangeForm
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

# Import đầy đủ các Model (Đã loại bỏ CartItem theo yêu cầu)
from .models import (
    Review, ReviewImage, User, Store, Product, Order, OrderItem,
    ProductImage, ProductVariant, Attribute, AttributeValue, Voucher
)

# ==========================================
# PHẦN 1: CUSTOM USER ADMIN (Giữ nguyên của bạn)
# ==========================================
class MaskedPasswordWidget(forms.Widget):
    def render(self, name, value, attrs=None, renderer=None):
        input_box = '<input type="password" class="vTextField" value="********" disabled>'
        reset_link = '''
        <div style="margin-top: 10px;">
            <strong><a href="../password/">Đặt lại mật khẩu</a></strong>
        </div>
        '''
        return mark_safe(input_box + reset_link)

class CustomUserChangeForm(UserChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'password' in self.fields:
            self.fields['password'].widget = MaskedPasswordWidget()
    
@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = CustomUserChangeForm
    list_display = ('username', 'email', 'role', 'is_active')
    readonly_fields = ('email',) 
    fieldsets = (
        ('Thông tin chung', {
            'fields': ('email', 'password'),
            'description': 'Đây là các thông tin đăng nhập bắt buộc của tài khoản.'
        }),
        ('Thông tin cá nhân', {
            'fields': ('username', 'phone', 'address', 'avatar')
        }),
        ('Phân quyền & Vai trò', {
            'fields': ('role', 'is_active', 'is_staff', 'is_superuser')
        }),
        ('Important dates', {
            'fields': ('last_login', 'date_joined')
        }),
    )

# ==========================================
# PHẦN 2: CÁC LỚP INLINE (Lồng ghép dữ liệu con)
# ==========================================
class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ('image', 'image_preview')
    readonly_fields = ('image_preview',) 

    def image_preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="width: 60px; height: 60px; border-radius: 4px; object-fit: cover; border: 1px solid #ccc;" />', obj.image.url)
        return "Chưa có ảnh"
    
    def has_add_permission(self, request, obj=None):
        return False
    
    image_preview.short_description = "Ảnh xem trước"
    
class ProductVariantInline(admin.TabularInline): 
    model = ProductVariant
    extra = 1
    fields = ('attribute_values', 'price', 'stock', 'image', 'image_preview')
    readonly_fields = ('image_preview',) 
    
    def image_preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="width: 60px; height: 60px; border-radius: 4px; object-fit: cover; border: 1px solid #ccc;" />', obj.image.url)
        return "Chưa có ảnh"
    
    image_preview.short_description = "Ảnh xem trước"

class OrderItemInline(admin.StackedInline):
    model = OrderItem
    extra = 0  
    readonly_fields = ('product', 'quantity', 'price') 
    
    def has_add_permission(self, request, obj=None):
        return False

class ReviewImageInline(admin.StackedInline):
    model = ReviewImage
    extra = 0
    # 👉 Ép Django chỉ vẽ đúng cột image_preview, bỏ qua hoàn toàn việc tạo Form cho ảnh gốc
    fields = ('image_preview',)
    readonly_fields = ('image_preview', 'image') 
    
    def image_preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="width: 100px; height: 100px; border-radius: 4px; object-fit: cover; border: 1px solid #ccc;" />', obj.image.url)
        return "Không có ảnh"
    
    image_preview.short_description = "Ảnh khách chụp"

    def has_add_permission(self, request, obj=None):
        return False

# ==========================================
# PHẦN 3: CÁC LỚP QUẢN TRỊ CHÍNH
# ==========================================
@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ('store_name', 'owner', 'address', 'rating', 'total_sales', 'created_at')
    search_fields = ('store_name', 'owner__username')

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'store', 'price', 'stock', 'category', 'sold_count')
    list_filter = ('category', 'store')
    search_fields = ('name',)
    inlines = [ProductImageInline, ProductVariantInline]
    autocomplete_fields = ('store',)
    list_select_related = ('store',)

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'total_amount', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__username', 'address')
    inlines = [OrderItemInline]
    
    # 🔒 BẢO MẬT: Khóa các thông tin kế toán, Admin chỉ được quyền thay đổi "status" (Trạng thái đơn)
    readonly_fields = ('user', 'total_amount', 'created_at')

@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'user', 'rating', 'created_at')
    list_filter = ('rating', 'created_at')
    search_fields = ('content', 'user__username', 'product__name')
    inlines = [ReviewImageInline]

    # 🔒 BẢO MẬT: Đóng hoàn toàn quyền SỬA bài đánh giá, Admin chỉ có quyền Xem hoặc Xóa bài vi phạm
    def has_change_permission(self, request, obj=None):
        return False

@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'store', 'discount_type', 'discount_value', 'is_active')
    search_fields = ('code', 'name')
    list_filter = ('is_active', 'discount_type')
    
# Đăng ký 2 bảng quản lý thuộc tính động dùng chung cho hệ thống
admin.site.register(Attribute)
admin.site.register(AttributeValue)