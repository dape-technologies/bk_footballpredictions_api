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
production_env="$app_root/.env"
production_patch="$stage/.production-env"
env_tmp=""

cleanup() {
  rm -f -- "$production_patch"
  if [[ -n "$env_tmp" ]]; then
    rm -f -- "$env_tmp"
  fi
}
trap cleanup EXIT

test -f "$stage/manage.py"
test -f "$stage/passenger_wsgi.py"
test -f "$activate"
test -f "$production_env"
test -f "$production_patch"

expected_pattern='^(RELWORX_API_KEY|RELWORX_ACCOUNT_NO|RELWORX_WEBHOOK_SIGNING_KEY|RELWORX_API_BASE_URL|RELWORX_WEBHOOK_URL)=.+$'
if [[ $(wc -l < "$production_patch") -ne 5 ]] || grep -Eqv "$expected_pattern" "$production_patch"; then
  echo "The staged Relworx configuration is incomplete or invalid." >&2
  exit 1
fi

for key in RELWORX_API_KEY RELWORX_ACCOUNT_NO RELWORX_WEBHOOK_SIGNING_KEY RELWORX_API_BASE_URL RELWORX_WEBHOOK_URL; do
  if [[ $(grep -c "^$key=" "$production_patch") -ne 1 ]]; then
    echo "The staged Relworx configuration has a missing or duplicate key." >&2
    exit 1
  fi
done

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
  --exclude='.production-env' \
  --exclude='frontend_dist/' \
  --exclude='media/' \
  --exclude='staticfiles/' \
  --exclude='tmp/' \
  --exclude='*.log' \
  "$stage/" "$app_root/"

env_tmp=$(mktemp "$app_root/.env.tmp.XXXXXX")
awk -F= '
  NR == FNR {
    replacement[$1] = $0
    order[++count] = $1
    next
  }
  {
    key = $1
    if (key in replacement) {
      if (!written[key]++) {
        print replacement[key]
      }
    } else {
      print
    }
  }
  END {
    for (i = 1; i <= count; i++) {
      key = order[i]
      if (!written[key]) {
        print replacement[key]
      }
    }
  }
' "$production_patch" "$production_env" > "$env_tmp"
chmod 600 "$env_tmp"
mv -- "$env_tmp" "$production_env"
env_tmp=""

cd "$app_root"
python manage.py collectstatic --noinput --clear
mkdir -p tmp
touch tmp/restart.txt

rm -rf -- "$stage"
