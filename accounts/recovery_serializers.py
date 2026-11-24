from rest_framework import serializers

from .recovery import validate_recovery_pair


class RecoveryProfileSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    recovery_private_phrase = serializers.CharField(write_only=True, trim_whitespace=False)
    recovery_life_phrase = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("The current password is incorrect.")
        return value

    def validate(self, attrs):
        user = self.context["request"].user
        private_phrase, life_phrase = validate_recovery_pair(
            attrs["recovery_private_phrase"],
            attrs["recovery_life_phrase"],
            user_values=(
                user.first_name, user.last_name, user.date_of_birth, user.phone,
                attrs.get("current_password"),
            ),
        )
        attrs["recovery_private_phrase"] = private_phrase
        attrs["recovery_life_phrase"] = life_phrase
        return attrs


class RecoveryStartSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=40)


class RecoveryCompleteSerializer(serializers.Serializer):
    recovery_id = serializers.CharField(max_length=80)
    private_phrase = serializers.CharField(trim_whitespace=False, max_length=256)
    life_phrase = serializers.CharField(trim_whitespace=False, max_length=256)
    new_password = serializers.CharField(write_only=True, min_length=4, trim_whitespace=False)
    password_confirm = serializers.CharField(write_only=True, min_length=4, trim_whitespace=False)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "The passwords do not match."})
        return attrs
