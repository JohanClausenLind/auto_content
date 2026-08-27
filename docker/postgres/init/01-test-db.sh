#!/bin/sh
# Create a separate database for integration tests so they never touch development data.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE DATABASE content_factory_test;
EOSQL
