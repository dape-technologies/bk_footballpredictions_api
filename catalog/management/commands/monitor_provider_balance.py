from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from catalog.relworx import check_wallet_balance
from accounts.security_services import queue_notice
from django.utils import timezone


class Command(BaseCommand):
    help = "Check Relworx liquidity and email both security contacts when below the withdrawal fee allowance."

    def handle(self, *args, **options):
        balance = Decimal(str(check_wallet_balance()["balance"]))
        if balance < Decimal("1500"):
            queue_notice(f"low-provider-balance:{timezone.now().strftime('%Y%m%d%H')}",
                         [settings.OWNER_SECURITY_EMAIL, settings.DEVELOPER_SECURITY_EMAIL],
                         "BK Relworx low balance", "Relworx has less than UGX 1,500 available. Payouts require the requested amount plus the withdrawal fee. Top up or wait for settled funds.")
            self.stdout.write("Low provider balance alert sent.")
        else:
            self.stdout.write("Provider balance is above the UGX 1,500 operating allowance.")
