from django.utils import timezone
from rest_framework import serializers

from accounts.serializers import UserSerializer
from .models import Package, Prediction, RecentWin, Subscription, Testimonial


def media_url(request, field):
    if not field:
        return ""
    url = field.url
    return request.build_absolute_uri(url) if request else url


class PackageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    is_open = serializers.ReadOnlyField()

    class Meta:
        model = Package
        fields = [
            "id", "name", "slug", "description", "price", "currency",
            "duration_days", "access_label", "benefits", "image", "image_url",
            "is_active", "is_featured", "display_order", "request_deadline",
            "is_open", "created_at", "updated_at",
        ]
        extra_kwargs = {"image": {"write_only": True, "required": False}}

    def get_image_url(self, obj):
        return media_url(self.context.get("request"), obj.image)


class SubscriptionSerializer(serializers.ModelSerializer):
    package = PackageSerializer(read_only=True)
    user = UserSerializer(read_only=True)
    approved_by_name = serializers.CharField(source="approved_by.full_name", read_only=True)
    grants_access = serializers.ReadOnlyField()

    class Meta:
        model = Subscription
        fields = [
            "id", "user", "package", "status", "price_snapshot", "activation_source",
            "requested_at", "approved_at", "starts_at", "expires_at", "approved_by_name",
            "owner_note", "customer_message", "grants_access", "updated_at",
        ]
        read_only_fields = fields


class SubscriptionRequestSerializer(serializers.Serializer):
    package_id = serializers.PrimaryKeyRelatedField(
        source="package",
        queryset=Package.objects.filter(is_active=True),
    )

    def validate_package_id(self, package):
        if not package.is_open:
            raise serializers.ValidationError("This package is not accepting requests.")
        return package

    def create(self, validated_data):
        user = self.context["request"].user
        package = validated_data["package"]
        existing = Subscription.objects.filter(
            user=user,
            package=package,
            status__in=[Subscription.Status.PENDING, Subscription.Status.ACTIVE],
        ).first()
        if existing:
            raise serializers.ValidationError({"package_id": "You already have a pending or active request for this package."})
        return Subscription.objects.create(
            user=user,
            package=package,
            price_snapshot=package.price,
        )


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
        return Subscription.objects.filter(
            user=request.user,
            package=obj.package,
            status=Subscription.Status.ACTIVE,
            expires_at__gt=timezone.now(),
        ).exists()

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
        fields = ["id", "title", "summary", "odds", "settled_at", "image", "image_url", "is_published", "created_at"]
        extra_kwargs = {"image": {"write_only": True, "required": False}}

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
