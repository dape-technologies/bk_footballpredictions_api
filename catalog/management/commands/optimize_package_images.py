from django.core.management.base import BaseCommand
from catalog.models import Package
from catalog.image_services import optimize_package_image


class Command(BaseCommand):
    help = "Create smaller WebP package images while preserving the uploaded originals."

    def handle(self, *args, **options):
        total = 0
        for package in Package.objects.exclude(image="").filter(image_optimized__isnull=True):
            if package.image:
                optimize_package_image(package)
                total += 1
        self.stdout.write(f"Optimized {total} package image(s); originals retained.")
