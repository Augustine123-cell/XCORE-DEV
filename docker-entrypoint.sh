#!/bin/sh
set -e
mkdir -p /app/sessions /app/data /app/logs
exec "$@"
