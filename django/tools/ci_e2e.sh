#!/usr/bin/env bash
# Disposable synthetic projects only; never reads the deployment .env.
set -euo pipefail
if [[ "${CI:-}" != "true" ]]; then
  echo 'Réservé à une validation CI isolée (CI=true).' >&2
  exit 2
fi
cd "$(dirname "$0")/.."
mkdir -p test-results
run_number="${GITHUB_RUN_ID:-local}${GITHUB_RUN_ATTEMPT:-1}"
if [[ ! "$run_number" =~ ^[a-z0-9]+$ ]]; then exit 2; fi
ci_project="zefacture-ci-$run_number"
ci_recovery="${ci_project}-recovery"
ci_env="$PWD/test-results/${ci_project}.env"
ci_port="${CI_HTTP_PORT:-8091}"
for candidate in "$ci_project" "$ci_recovery"; do
  if [[ -n "$(docker volume ls --filter "label=com.docker.compose.project=$candidate" --format '{{.Name}}')" ]]; then
    echo "Projet déjà existant : $candidate. Aucun volume ne sera modifié." >&2
    exit 2
  fi
done
python3 tools/configure.py --output "$ci_env"
python3 - "$ci_env" "$ci_port" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1]);port=int(sys.argv[2])
updates={'APP_ENV':'testing','APP_URL':f'http://127.0.0.1:{port}','HTTP_PORT':str(port),'ALLOWED_HOSTS':'127.0.0.1,localhost,testserver'}
lines=[line for line in p.read_text().splitlines() if line.split('=',1)[0] not in updates]
p.write_text('\n'.join(lines)+'\n'+'\n'.join(k+'='+v for k,v in updates.items())+'\n')
PY
export ZEFACTURE_ENV_FILE="$ci_env"
dc() { docker compose --project-name "$ci_project" --env-file "$ci_env" "$@"; }
cleanup() {
  dc logs --no-color > test-results/ci-containers.log 2>&1 || true
  # Both project names were checked empty and created by this script alone.
  dc down --volumes --remove-orphans >/dev/null 2>&1 || true
  recovery_env="$PWD/backups/recovery-$ci_recovery/.env"
  if [[ -f "$recovery_env" ]]; then
    ZEFACTURE_ENV_FILE="$recovery_env" docker compose --project-name "$ci_recovery" --env-file "$recovery_env" down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
dc up -d --build --wait --wait-timeout 180 web
# The worker deliberately has no HTTP healthcheck. Wait for the web/database,
# then start it without applying Compose's health requirement to that service.
dc up -d --no-deps worker
dc exec -T web python manage.py shell < tests/browser_fixture.py
export TEST_BASE_URL="http://127.0.0.1:$ci_port"
export NODE_PATH="$PWD/tests/browser/node_modules${NODE_PATH:+:$NODE_PATH}"
node tests/browser-smoke.cjs
node tests/browser-imports.cjs
node tests/browser-crm-integrations.cjs
dc stop worker
dc exec -T web python manage.py shell < tests/pdp_browser_fixture.py > test-results/superpdp-browser-fixture.json
node tests/browser-superpdp.cjs
node tests/browser-oauth-branding.cjs
dc stop web worker
dc run --rm --no-deps -T --entrypoint python web manage.py shell < tests/instance_fingerprint.py > test-results/ci-before.json
python3 tools/backup.py --project "$ci_project" --env-file "$ci_env" --output "test-results/$ci_project.tar.gz" --leave-stopped
recovery_port="$((ci_port+1))"
python3 tools/restore.py "test-results/$ci_project.tar.gz" --project "$ci_recovery" --port "$recovery_port"
recovery_env="$PWD/backups/recovery-$ci_recovery/.env"
ZEFACTURE_ENV_FILE="$recovery_env" docker compose --project-name "$ci_recovery" --env-file "$recovery_env" stop web worker
ZEFACTURE_ENV_FILE="$recovery_env" docker compose --project-name "$ci_recovery" --env-file "$recovery_env" run --rm --no-deps -T --entrypoint python web manage.py shell < tests/instance_fingerprint.py > test-results/ci-after.json
python3 - <<'PY'
import json
from pathlib import Path
def read(name):
    return json.loads(next(line for line in Path(name).read_text().splitlines() if line.startswith('{')))
before,after=read('test-results/ci-before.json'),read('test-results/ci-after.json')
assert before==after, 'La restauration doit conserver chaque table, chaque champ et chaque fichier.'
result={'ok':True,'tables':len(before['tables']),'rows':sum(t['rows'] for t in before['tables'].values()),'files':len(before['files']),'all_table_and_file_hashes_equal':True,'synthetic_data_only':True}
Path('test-results/ci-recovery.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
PY
