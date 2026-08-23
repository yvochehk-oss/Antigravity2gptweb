# Archive Directory

This directory contains deprecated or replaced files that were part of previous iterations of the codebase.

## Purpose
- Store outdated scripts, migrations, and configuration files that have been superseded
- Preserve historical context for audit and rollback purposes

## Files in this directory
No active files are stored here. This directory exists as a convention for future archival needs.

## Cleanup Policy
- Only files that have been explicitly replaced by new implementations should be moved here
- Historical backups (`.bak`, `.pre-canonical-*`, `backups/`) are NOT to be moved into this directory
- No files from `data/` or `.git/` should be archived here

## Retention
Archived files should be kept for at least 90 days before permanent deletion consideration.
