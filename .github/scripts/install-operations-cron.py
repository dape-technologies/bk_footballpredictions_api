"""Install only BK's named operational jobs, preserving other crontab entries."""
import subprocess

result = subprocess.run(["crontab", "-l"], text=True, capture_output=True)
if result.returncode and "no crontab" not in result.stderr.lower():
    raise SystemExit("Unable to read existing crontab; no changes made.")
lines = [line for line in result.stdout.splitlines() if "# bk-operations:" not in line]
base = "cd /home/farmsnva/bk_app && DJANGO_ENV_FILE=/home/farmsnva/bk_app/.env /home/farmsnva/virtualenv/bk_app/3.12/bin/python manage.py "
installed = 0
for schedule, command in [("*/5 * * * *", "deliver_security_notices"),
                          ("*/5 * * * *", "reconcile_payments"),
                          ("17 3 * * *", "cleanup_operational_data")]:
    if command == "reconcile_payments" and any(
        not line.lstrip().startswith("#") and "manage.py reconcile_payments" in line for line in lines
    ):
        continue
    lines.append(f"{schedule} {base}{command} >> /home/farmsnva/bk_app/{command}.log 2>&1 # bk-operations:{command}")
    installed += 1
subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
print(f"Installed {installed} BK operational schedules; other jobs preserved.")
