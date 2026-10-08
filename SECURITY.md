# Security policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting
(Security tab > "Report a vulnerability") instead of a public issue.
You will get an answer within 7 days.

## Scope and known limits

These servers run locally with the permissions of the user who starts them.
The guard rails are listed in the README ("Security model") and covered by
`tests/smoke_test.py`.

Known, accepted limits:

- **DNS rebinding (web-scraper):** the host is resolved and checked before the
  request; a hostile DNS server could answer with an internal address a moment
  later. Do not rely on the web-scraper as a network boundary.
- **file-manager can delete and overwrite** files inside `FILE_MANAGER_ROOT`
  (overwrite needs an explicit flag). Point the root at a folder you are happy
  for Claude to change, not at your whole home directory.
- **sqlite in write mode** executes any single statement Claude sends, after
  `confirm=true`. Keep `SQLITE_READ_ONLY=true` unless you need writes, and keep
  a backup of the database.
