from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from catalog.models import Package, Prediction, RecentWin, Testimonial


class Command(BaseCommand):
    help = "Create deterministic local demo data for BK Football Predictions."

    def handle(self, *args, **options):
        owner, created = User.objects.get_or_create(
            phone="0700000000",
            defaults={"first_name": "BK", "last_name": "Owner", "is_staff": True, "is_superuser": True},
        )
        if created or not owner.check_password("BKowner2026!"):
            owner.set_password("BKowner2026!")
            owner.is_staff = True
            owner.is_superuser = True
            owner.save()

        packages = [
            ("Daily Edge", "daily-edge", "Accumulator", 10000, 78, 4, "BK-DAY-78", True),
            ("Weekend Board", "weekend-board", "Weekend combo", 35000, 82, 20, "BK-WKD-82", False),
            ("Season Desk", "season-desk", "VIP accumulator", 90000, 86, 28, "BK-VIP-86", False),
        ]
        package_objects = []
        now = timezone.now()
        for order, (name, slug, package_type, price, probability, closes_in, code, featured) in enumerate(packages):
            package, _ = Package.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "package_type": package_type,
                    "price": price,
                    "win_probability": probability,
                    "closes_at": now + timedelta(hours=closes_in),
                    "betslip_link": "https://example.com/betslip/" + slug,
                    "code": code,
                    "is_featured": featured,
                    "display_order": order,
                },
            )
            package_objects.append(package)

        predictions = [
            ("Arsenal", "Newcastle", "Premier League", 4, "free", None, "Total goals", "Over 1.5", "1.42", 76, "Both teams have produced high-volume final-third entries across their recent fixtures."),
            ("Inter Milan", "Atalanta", "Serie A", 7, "premium", package_objects[0], "Match result", "Inter Milan to win", "1.84", 81, "Inter's rest advantage and central progression profile create the stronger match-up."),
            ("Real Sociedad", "Villarreal", "La Liga", 27, "premium", package_objects[1], "Asian handicap", "Real Sociedad +0.0", "1.72", 74, "The home side's defensive floor makes the draw-no-bet position the disciplined angle."),
        ]
        for home, away, comp, hours, access, package, market, selection, odds, confidence, analysis in predictions:
            Prediction.objects.update_or_create(
                home_team=home,
                away_team=away,
                kickoff_at=now + timedelta(hours=hours),
                defaults={
                    "competition": comp,
                    "access_level": access,
                    "package": package,
                    "market": market,
                    "selection": selection,
                    "odds": odds,
                    "confidence": confidence,
                    "analysis": analysis,
                    "is_published": True,
                    "created_by": owner,
                },
            )

        RecentWin.objects.get_or_create(
            title="Three-match weekend sequence",
            defaults={"caption": "Three-match weekend package won at combined odds of 4.63.", "summary": "A measured weekend board settled with all three selections landing.", "odds": "4.63", "settled_at": now - timedelta(days=2), "is_published": True},
        )
        Testimonial.objects.get_or_create(
            member_name="Daniel K.",
            defaults={"quote": "The value is in the structure. I can see the market, timing and reasoning without noise.", "member_since": "Member since 2026", "is_published": True},
        )
        self.stdout.write(self.style.SUCCESS("Demo data ready. Owner login: 0700000000 / BKowner2026!"))
