from django.core.management.base import BaseCommand

from catalog.models import Payment, WalletLedgerEntry
from catalog.wallet_services import accrue_payment


class Command(BaseCommand):
    help = "Idempotently repair missing owner and developer wallet credits for paid payments."

    def handle(self, *args, **options):
        scanned = 0
        repaired_payments = 0
        created_entries = 0

        for payment in Payment.objects.filter(status=Payment.Status.PAID).order_by("pk").iterator():
            scanned += 1
            before = WalletLedgerEntry.objects.filter(payment=payment).count()
            accrue_payment(payment)
            after = WalletLedgerEntry.objects.filter(payment=payment).count()
            if after > before:
                repaired_payments += 1
                created_entries += after - before

        self.stdout.write(self.style.SUCCESS(
            f"Wallet credit reconciliation scanned {scanned} paid payment(s), "
            f"repaired {repaired_payments}, and created {created_entries} ledger entry/entries."
        ))
