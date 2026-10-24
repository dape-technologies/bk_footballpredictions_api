from io import BytesIO
from uuid import uuid4

from django.core.files.base import ContentFile
from PIL import Image, ImageOps


def optimize_package_image(package):
    if not package.image:
        package.image_optimized = None
        package.save(update_fields=["image_optimized"])
        return
    with package.image.open("rb") as source:
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original)
            image.thumbnail((960, 960), Image.Resampling.LANCZOS)
            image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
            output = BytesIO()
            image.save(output, "WEBP", quality=84, method=6)
    if len(output.getvalue()) >= package.image.size:
        package.image_optimized = package.image.name
    else:
        package.image_optimized.save(f"{package.pk}-{uuid4().hex[:10]}.webp", ContentFile(output.getvalue()), save=False)
    package.save(update_fields=["image_optimized"])


def optimize_paid_package_image(package):
    if not package.paid_image:
        package.paid_image_optimized = None
        package.save(update_fields=["paid_image_optimized"])
        return
    with package.paid_image.open("rb") as source:
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original)
            image.thumbnail((1600, 2400), Image.Resampling.LANCZOS)
            image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
            output = BytesIO()
            image.save(output, "WEBP", quality=90, method=6)
    if len(output.getvalue()) >= package.paid_image.size:
        package.paid_image_optimized = package.paid_image.name
    else:
        package.paid_image_optimized.save(
            f"{package.pk}-{uuid4().hex[:10]}.webp", ContentFile(output.getvalue()), save=False
        )
    package.save(update_fields=["paid_image_optimized"])
