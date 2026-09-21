from .models import ActivityLog


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",", 1)[0].strip() or request.META.get("REMOTE_ADDR") or None


def record_activity(
    request,
    *,
    category,
    action,
    description,
    actor=None,
    target=None,
    metadata=None,
):
    resolved_actor = actor
    if resolved_actor is None and getattr(request, "user", None) and request.user.is_authenticated:
        resolved_actor = request.user
    ActivityLog.objects.create(
        actor=resolved_actor,
        category=category,
        action=action,
        description=description,
        target_type=target._meta.label if target is not None else "",
        target_id=str(target.pk) if target is not None and target.pk is not None else "",
        metadata=metadata or {},
        ip_address=client_ip(request),
    )
