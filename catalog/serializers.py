import re

from rest_framework import serializers

from accounts.serializers import UserSerializer
from .models import Package, Payment, Prediction, Purchase, RecentWin, Testimonial


def media_url(request, field):
    if not field:
        return ""
    url = field.url
    return request.build_absolute_uri(url) if request else url


def normalize_ugandan_msisdn(value):
    raw = re.sub(r"\D", "", str(value or ""))
    if raw.startswith("0") and len(raw) == 10:
        raw = f"256{raw[1:]}"
    if not raw.startswith("256") or len(raw) != 12:
        raise serializers.ValidationError("Enter a valid Ugandan mobile-money number.")
    return f"+{raw}"


class PublicPackageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    is_open = serializers.ReadOnlyField()

    class Meta:
        model = Package
        fields = [
            "id", "name", "slug", "package_type", "price", "currency",
            "win_probability", "closes_at", "image_url", "is_open",
        ]

    def get_image_url(self, obj):
        return media_url(self.context.get("request"), obj.image)


class PackageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    is_open = serializers.ReadOnlyField()

    class Meta:
        model = Package
        fields = [
            "id", "name", "slug", "package_type", "description", "price", "currency",
            "win_probability", "closes_at", "betslip_link", "code", "access_label",
            "benefits", "image", "image_url", "is_featured", "display_order",
            "is_open", "created_at", "updated_at",
        ]
        extra_kwargs = {
            "image": {"write_only": True, "required": False},
            "package_type": {"required": True},
            "betslip_link": {"required": True, "allow_blank": False},
            "code": {"required": True, "allow_blank": False},
            "closes_at": {"required": True, "allow_null": False},
        }

    def get_image_url(self, obj):
        return media_url(self.context.get("request"), obj.image)


class PaymentSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    package = PublicPackageSerializer(read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id", "reference", "internal_reference", "user", "package", "amount",
            "currency", "status", "provider", "payer_msisdn", "provider_message",
            "paid_at", "created_at", "updated_at",
        ]
        read_only_fields = fields


class PurchaseSerializer(serializers.ModelSerializer):
    package = PublicPackageSerializer(read_only=True)
    user = UserSerializer(read_only=True)
    is_paid = serializers.ReadOnlyField()
    status = serializers.ReadOnlyField()
    code = serializers.SerializerMethodField()
    betslip_link = serializers.SerializerMethodField()
    latest_payment = serializers.SerializerMethodField()

    class Meta:
        model = Purchase
        fields = [
            "id", "user", "package", "price_snapshot", "currency_snapshot",
            "initiated_at", "paid_at", "updated_at", "is_paid", "status", "code",
            "betslip_link", "latest_payment",
        ]
        read_only_fields = fields

    def _can_view_secret(self, obj):
        request = self.context.get("request")
        return obj.is_paid or bool(
            request and request.user.is_authenticated and request.user.is_product_owner
        )

    def get_code(self, obj):
        return obj.code_snapshot if self._can_view_secret(obj) else ""

    def get_betslip_link(self, obj):
        return obj.link_snapshot if self._can_view_secret(obj) else ""

    def get_latest_payment(self, obj):
        payments = list(obj.payments.all())
        payment = max(payments, key=lambda item: (item.created_at, item.id)) if payments else None
        return PaymentSerializer(payment, context=self.context).data if payment else None


class PurchaseCreateSerializer(serializers.Serializer):
    package_id = serializers.PrimaryKeyRelatedField(
        source="package",
        queryset=Package.objects.filter(deleted_at__isnull=True),
    )

    def validate_package_id(self, package):
        if not package.is_open:
            raise serializers.ValidationError("This package is closed and cannot be purchased.")
        return package

    def create(self, validated_data):
        package = validated_data["package"]
        purchase, created = Purchase.objects.get_or_create(
            user=self.context["request"].user,
            package=package,
            defaults={"price_snapshot": package.price, "currency_snapshot": package.currency},
        )
        self.was_created = created
        return purchase


class PaymentAttemptSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)

    def validate_phone(self, value):
        return normalize_ugandan_msisdn(value)


class PredictionSerializer(serializers.ModelSerializer):
    package_name = serializers.CharField(source="package.name", read_only=True)
    package_slug = serializers.CharField(source="package.slug", read_only=True)
    locked = serializers.SerializerMethodField()

    class Meta:
        model = Prediction
        fields = [
            "id", "home_team", "away_team", "competition", "kickoff_at",
            "access_level", "package", "package_name", "package_slug", "market",
            "selection", "odds", "confidence", "analysis", "betslip_reference",
            "result", "score", "is_published", "published_at", "locked",
            "created_at", "updated_at",
        ]
        read_only_fields = ["created_by", "published_at", "created_at", "updated_at"]

    def _can_view(self, obj):
        request = self.context.get("request")
        if obj.access_level == Prediction.Access.FREE:
            return True
        if not request or not request.user.is_authenticated or request.user.is_blocked:
            return False
        if request.user.is_staff:
            return True
        return Purchase.objects.filter(user=request.user, package=obj.package, paid_at__isnull=False).exists()

    def get_locked(self, obj):
        return not self._can_view(obj)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self._can_view(instance):
            for field in ["market", "selection", "odds", "confidence", "analysis", "betslip_reference"]:
                data.pop(field, None)
        return data

    def validate(self, attrs):
        access = attrs.get("access_level", getattr(self.instance, "access_level", Prediction.Access.FREE))
        package = attrs.get("package", getattr(self.instance, "package", None))
        if access == Prediction.Access.PREMIUM and not package:
            raise serializers.ValidationError({"package": "Premium predictions require a package."})
        return attrs


class RecentWinSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = RecentWin
        fields = ["id", "caption", "settled_at", "image", "image_url", "is_published", "created_at"]
        extra_kwargs = {
            "caption": {"required": True, "allow_blank": False},
            "image": {"write_only": True, "required": True},
        }

    def get_image_url(self, obj):
        return media_url(self.context.get("request"), obj.image)


class TestimonialSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = Testimonial
        fields = ["id", "member_name", "quote", "member_since", "avatar", "avatar_url", "is_published", "display_order", "created_at"]
        extra_kwargs = {"avatar": {"write_only": True, "required": False}}

    def get_avatar_url(self, obj):
        return media_url(self.context.get("request"), obj.avatar)
