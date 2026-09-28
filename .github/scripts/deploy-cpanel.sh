#!/usr/bin/env bash
set -Eeuo pipefail

app_root=${1:?Application root is required}
venv_root=${2:?Virtualenv root is required}
stage_name=${3:?Stage name is required}

if [[ "$app_root" != /home/*/* || "$venv_root" != /home/*/* || "$stage_name" != .deploy-api-* ]]; then
  echo "Refusing unsafe deployment paths." >&2
  exit 1
fi

stage="$app_root/$stage_name"
activate="$venv_root/bin/activate"

test -f "$stage/manage.py"
test -f "$stage/passenger_wsgi.py"
test -f "$activate"

source "$activate"
export DJANGO_ENV_FILE="$app_root/.env"
cd "$stage"

python -m pip install -r requirements.txt
python manage.py check --deploy
python manage.py migrate --noinput

rsync -a --delete \
  --exclude='.env' \
  --exclude='.htaccess' \
  --exclude='.deploy-api-*/' \
  --exclude='.deploy-frontend-*/' \
  --exclude='.frontend-previous/' \
  --exclude='frontend_dist/' \
  --exclude='media/' \
  --exclude='staticfiles/' \
  --exclude='tmp/' \
  --exclude='*.log' \
  "$stage/" "$app_root/"

cd "$app_root"
python manage.py collectstatic --noinput --clear
mkdir -p tmp
touch tmp/restart.txt

rm -rf -- "$stage"
