#!/bin/sh
set -eu
umask 077
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    python manage.py migrate --noinput
fi
python manage.py prepare_storage
exec "$@"
