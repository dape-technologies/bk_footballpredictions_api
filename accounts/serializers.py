from django.contrib.auth import authenticate
from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    is_owner = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "phone", "display_name", "is_owner", "is_blocked", "created_at"]
        read_only_fields = fields

    def get_is_owner(self, obj):
        return bool(obj.is_staff)


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["display_name", "phone", "password"]

    def validate_phone(self, value):
        phone = User.normalize_phone(value)
        if len(phone) < 10 or len(phone) > 15:
            raise serializers.ValidationError("Enter a valid phone number.")
        if User.objects.filter(phone=phone).exists():
            raise serializers.ValidationError("This phone number is already registered.")
        return phone

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


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

