import unicodedata

from django.contrib.auth.hashers import make_password
from rest_framework import serializers


RECOVERY_PROMPTS = [
    {
        "id": "private_phrase",
        "question": "What private word or phrase will you always remember?",
        "help_text": "Choose something only you would recognize. Do not enter a PIN, password, financial detail, or deeply sensitive secret.",
    },
    {
        "id": "life_phrase",
        "question": "What nickname or special phrase reminds you of someone important in your life?",
        "help_text": "Enter words you will remember exactly. Do not include their full name, phone number, or sensitive personal information.",
    },
]


def normalize_recovery_phrase(value):
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.split()).casefold()


def validate_recovery_phrase(value, field_name):
    normalized = normalize_recovery_phrase(value)
    if len(normalized) < 8 or len(normalized) > 128:
        raise serializers.ValidationError({field_name: "Use between 8 and 128 characters."})
    if normalized.isdigit():
        raise serializers.ValidationError({field_name: "The recovery answer cannot contain only numbers."})
    return normalized


def validate_recovery_pair(private_phrase, life_phrase, *, user_values=()):
    private_normalized = validate_recovery_phrase(private_phrase, "recovery_private_phrase")
    life_normalized = validate_recovery_phrase(life_phrase, "recovery_life_phrase")
    if private_normalized == life_normalized:
        raise serializers.ValidationError({"recovery_life_phrase": "Use a different answer for each prompt."})

    forbidden = {normalize_recovery_phrase(value) for value in user_values if value}
    if private_normalized in forbidden:
        raise serializers.ValidationError({"recovery_private_phrase": "Choose an answer that is different from your account details and password."})
    if life_normalized in forbidden:
        raise serializers.ValidationError({"recovery_life_phrase": "Choose an answer that is different from your account details and password."})
    return private_normalized, life_normalized


def recovery_hashes(private_phrase, life_phrase):
    return make_password(private_phrase), make_password(life_phrase)
