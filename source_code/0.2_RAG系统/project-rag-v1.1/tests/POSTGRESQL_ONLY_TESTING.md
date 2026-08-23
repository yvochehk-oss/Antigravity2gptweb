# PostgreSQL-only testing
Database integration tests require an explicit disposable `TEST_DATABASE_URL`
whose database name contains `test`. The active suite never silently falls back
to SQLite. Pure logic tests may still run without a database service.
