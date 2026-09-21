from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers

from .models import ActivityLog, User


class UserSerializer(serializers.ModelSerializer):
    surname = serializers.CharField(source="last_name", read_only=True)
    full_name = serializers.CharField(read_only=True)
    is_owner = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "phone", "first_name", "surname", "full_name", "date_of_birth",
            "roles", "is_owner", "is_blocked", "created_at",
        ]
        read_only_fields = fields

    def get_is_owner(self, obj):
        return obj.is_product_owner

    def get_roles(self, obj):
        roles = list(obj.groups.order_by("name").values_list("name", flat=True))
        if obj.is_superuser and "Product Owner" not in roles:
            roles.insert(0, "Product Owner")
        return roles or ["Customer"]


class RegisterSerializer(serializers.ModelSerializer):
    surname = serializers.CharField(source="last_name", max_length=150)
    password = serializers.CharField(write_only=True, min_length=4, trim_whitespace=False)
    password_confirm = serializers.CharField(write_only=True, min_length=4, trim_whitespace=False)

    class Meta:
        model = User
        fields = ["first_name", "surname", "date_of_birth", "phone", "password", "password_confirm"]

    def validate_first_name(self, value):
        value = " ".join(value.split())
        if len(value) < 2:
            raise serializers.ValidationError("Enter your first name.")
        return value

    def validate_surname(self, value):
        value = " ".join(value.split())
        if len(value) < 2:
            raise serializers.ValidationError("Enter your surname.")
        return value

    def validate_date_of_birth(self, value):
        today = timezone.localdate()
        try:
            eighteenth_birthday = value.replace(year=value.year + 18)
        except ValueError:
            eighteenth_birthday = value.replace(year=value.year + 18, day=28)
        if eighteenth_birthday > today:
            raise serializers.ValidationError("You must be at least 18 years old to register.")
        return value

    def validate_phone(self, value):
        if not str(value).strip() or any(character.isalpha() for character in str(value)):
            raise serializers.ValidationError("Enter a valid phone number.")
        phone = User.normalize_phone(value)
        if len(phone) < 10 or len(phone) > 15:
            raise serializers.ValidationError("Enter a valid phone number.")
        if User.objects.filter(phone=phone).exists():
            raise serializers.ValidationError("This phone number is already registered.")
        return phone

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "The passwords do not match."})
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        try:
            with transaction.atomic():
                return User.objects.create_user(**validated_data)
        except IntegrityError:
            raise serializers.ValidationError({"phone": "This phone number is already registered."})


class LoginSerializer(serializers.Serializer):
    phone = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        phone = User.normalize_phone(attrs["phone"])
        user = authenticate(phone=phone, password=attrs["password"])
        if not user:
            raise serializers.ValidationError("The phone number or password is incorrect.")
        if user.is_blocked:
            raise serializers.ValidationError("This account has been suspended.")
        attrs["user"] = user
        return attrs


class ActivityLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True, default="System")
    actor_phone = serializers.CharField(source="actor.phone", read_only=True, default="")

    class Meta:
        model = ActivityLog
        fields = [
            "id", "category", "action", "description", "actor_name", "actor_phone",
            "target_type", "target_id", "metadata", "ip_address", "created_at",
        ]
        read_only_fields = fields
