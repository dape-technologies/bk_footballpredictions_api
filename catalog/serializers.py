import re

from rest_framework import serializers

from accounts.serializers import UserSerializer
from .models import Package, Payment, PaymentAlert, PaymentEvent, Prediction, Purchase, RecentWin, Testimonial
from .payment_pricing import payment_quote


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
    payment_total = serializers.SerializerMethodField()
    payment_fee = serializers.SerializerMethodField()

    class Meta:
        model = Package
        fields = [
            "id", "name", "slug", "package_type", "price", "currency",
            "win_probability", "closes_at", "image_url", "is_active", "is_open",
            "payment_total", "payment_fee",
        ]

    def get_image_url(self, obj):
        return media_url(self.context.get("request"), obj.image_optimized or obj.image)

    def get_payment_total(self, obj):
        return payment_quote(obj.price)["total_charge"]

    def get_payment_fee(self, obj):
        return payment_quote(obj.price)["provider_fee_estimate"]


class PackageSerializer(serializers.ModelSerializer):
    def create(self, validated_data):
        instance = super().create(validated_data)
        if instance.image:
            from .image_services import optimize_package_image
            optimize_package_image(instance)
        if instance.paid_image:
            from .image_services import optimize_paid_package_image
            optimize_paid_package_image(instance)
        return instance

    def update(self, instance, validated_data):
        image_changed = "image" in validated_data
        paid_image_changed = "paid_image" in validated_data
        instance = super().update(instance, validated_data)
        if image_changed:
            from .image_services import optimize_package_image
            optimize_package_image(instance)
        if paid_image_changed:
            from .image_services import optimize_paid_package_image
            optimize_paid_package_image(instance)
            paid_image = instance.paid_image_optimized or instance.paid_image
            if paid_image:
                Purchase.objects.filter(
                    package=instance, paid_at__isnull=False, paid_image_snapshot=""
                ).update(paid_image_snapshot=paid_image.name)
        return instance
    image_url = serializers.SerializerMethodField()
    paid_image_url = serializers.SerializerMethodField()
    is_open = serializers.ReadOnlyField()

    class Meta:
        model = Package
        fields = [
            "id", "name", "slug", "package_type", "description", "price", "currency",
            "win_probability", "closes_at", "betslip_link", "code", "access_label",
            "benefits", "image", "image_url", "paid_image", "paid_image_url",
            "is_featured", "display_order",
            "is_active", "is_open", "created_at", "updated_at",
        ]
        extra_kwargs = {
            "image": {"write_only": True, "required": False},
            "paid_image": {"write_only": True, "required": False},
            "package_type": {"required": True},
            "betslip_link": {"required": True, "allow_blank": False},
            "code": {"required": True, "allow_blank": False},
            "closes_at": {"required": True, "allow_null": False},
        }

    def get_image_url(self, obj):
        return media_url(self.context.get("request"), obj.image)

    def get_paid_image_url(self, obj):
        if not (obj.paid_image_optimized or obj.paid_image):
            return ""
        request = self.context.get("request")
        path = f"/api/v1/owner/packages/{obj.pk}/paid-image/"
        return request.build_absolute_uri(path) if request else path

    def validate(self, attrs):
        currency = str(attrs.get("currency", getattr(self.instance, "currency", "UGX"))).upper()
        price = attrs.get("price", getattr(self.instance, "price", None))
        errors = {}
        if currency != "UGX":
            errors["currency"] = "Mobile-money checkout currently supports UGX packages only."
        if price is not None and not 500 <= price <= 5000000:
            errors["price"] = "Relworx accepts UGX payments from 500 to 5,000,000."
        if errors:
            raise serializers.ValidationError(errors)
        attrs["currency"] = currency
        return attrs


class PaymentSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    package = PublicPackageSerializer(read_only=True)
    receipt_url = serializers.SerializerMethodField()
    customer_message = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "id", "reference", "internal_reference", "user", "package", "amount",
            "base_amount", "provider_fee_estimate", "provider_net_amount", "developer_commission",
            "currency", "status", "provider", "payer_msisdn", "provider_message",
            "provider_transaction_id", "provider_charge", "failure_category",
            "failure_code", "failure_reason", "submitted_at", "confirmed_at",
            "failed_at", "last_checked_at", "check_attempts", "receipt_number",
            "receipt_issued_at", "receipt_url", "customer_message", "paid_at", "created_at", "updated_at",
        ]
        read_only_fields = fields

    def get_receipt_url(self, obj):
        if obj.status != Payment.Status.PAID:
            return ""
        request = self.context.get("request")
        path = f"/api/v1/me/payments/{obj.pk}/receipt/"
        return request.build_absolute_uri(path) if request else path

    def get_customer_message(self, obj):
        if obj.status == Payment.Status.PAID:
            return "Payment confirmed. Your package is ready."
        if obj.status in (Payment.Status.CREATED, Payment.Status.SUBMITTED, Payment.Status.PENDING):
            return "Approve the mobile-money prompt on your phone. We will confirm it automatically."
        if obj.status == Payment.Status.EXPIRED or obj.failure_code == "approval_window_expired":
            return "The payment prompt expired before confirmation. Try again to receive a new prompt."

        detail = f"{obj.failure_code} {obj.failure_reason} {obj.provider_message}".lower()
        if "wrong pin" in detail or "incorrect pin" in detail:
            return "The mobile-money PIN entered was incorrect. Check your PIN and try again."
        if "insufficient" in detail:
            return "Your mobile-money account does not have enough available balance for this payment."
        if "low_balance_or_payee_limit_reached_or_not_allowed" in detail:
            return "The mobile-money provider declined this request because of insufficient balance, an account limit, or a transaction restriction."
        if "cancel" in detail or obj.status == Payment.Status.CANCELLED:
            return "The payment was cancelled. Try again when you are ready."
        if obj.status == Payment.Status.NEEDS_REVIEW:
            return "We could not confirm this payment automatically. Please contact BK support before trying again."
        if obj.failure_category in (Payment.FailureCategory.PROVIDER, Payment.FailureCategory.SYSTEM):
            return "The mobile-money provider could not complete this request. Please try again."
        if obj.status == Payment.Status.FAILED:
            return "The payment was not completed. Check your mobile-money account and try again."
        return "Payment status is being updated."


class PaymentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentEvent
        fields = ["id", "event_type", "source", "status", "message", "payload", "created_at"]
        read_only_fields = fields


class PaymentAlertSerializer(serializers.ModelSerializer):
    payment_reference = serializers.CharField(source="payment.reference", read_only=True, default="")
    is_resolved = serializers.ReadOnlyField()

    class Meta:
        model = PaymentAlert
        fields = [
            "id", "payment", "payment_reference", "alert_key", "severity", "title",
            "message", "is_resolved", "resolved_at", "created_at",
        ]
        read_only_fields = fields


class PaymentDetailSerializer(PaymentSerializer):
    events = PaymentEventSerializer(many=True, read_only=True)
    alerts = PaymentAlertSerializer(many=True, read_only=True)

    class Meta(PaymentSerializer.Meta):
        fields = PaymentSerializer.Meta.fields + ["events", "alerts", "provider_payload"]


class PurchaseSerializer(serializers.ModelSerializer):
    access_history = serializers.SerializerMethodField()
    payment_timeline = serializers.SerializerMethodField()
    package = PublicPackageSerializer(read_only=True)
    user = UserSerializer(read_only=True)
    is_paid = serializers.ReadOnlyField()
    status = serializers.ReadOnlyField()
    code = serializers.SerializerMethodField()
    betslip_link = serializers.SerializerMethodField()
    paid_image_url = serializers.SerializerMethodField()
    latest_payment = serializers.SerializerMethodField()

    class Meta:
        model = Purchase
        fields = [
            "id", "user", "package", "price_snapshot", "currency_snapshot",
            "initiated_at", "paid_at", "updated_at", "is_paid", "status", "code",
            "betslip_link", "paid_image_url", "latest_payment", "payment_timeline", "access_history",
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

    def get_paid_image_url(self, obj):
        if not self._can_view_secret(obj) or not obj.paid_image_snapshot:
            return ""
        request = self.context.get("request")
        path = f"/api/v1/me/purchases/{obj.pk}/paid-image/"
        return request.build_absolute_uri(path) if request else path

    def get_latest_payment(self, obj):
        payments = list(obj.payments.all())
        payment = max(payments, key=lambda item: (item.created_at, item.id)) if payments else None
        return PaymentSerializer(payment, context=self.context).data if payment else None

    def get_payment_timeline(self, obj):
        payment_serializer = PaymentSerializer(context=self.context)
        return [{"reference": payment.reference, "status": payment.status,
                 "created_at": payment.created_at, "submitted_at": payment.submitted_at,
                 "confirmed_at": payment.confirmed_at, "failed_at": payment.failed_at,
                 "failure_reason": payment.failure_reason,
                 "customer_message": payment_serializer.get_customer_message(payment)}
                for payment in obj.payments.all()]

    def get_access_history(self, obj):
        from .models import JourneyEvent
        return list(JourneyEvent.objects.filter(purchase=obj, user=obj.user, kind="betslip_access").order_by("-created_at").values_list("created_at", flat=True)[:20])


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
