-- Runs once, when the Postgres volume is first created.
-- A separate, disposable database for the automated test suite, so tests never
-- touch real data.
CREATE DATABASE certwatch_test;
