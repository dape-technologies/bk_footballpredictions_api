from django.conf import settings
from django.http import FileResponse, Http404


def spa_index(request):
    index_file = settings.FRONTEND_DIST_DIR / "index.html"
    if not index_file.is_file():
        raise Http404("The frontend build has not been deployed.")
    return FileResponse(index_file.open("rb"), content_type="text/html")
