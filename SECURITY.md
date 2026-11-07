# Security

## Scope

The web UI has **no login by design**. It is meant for a trusted home network. "Anyone on the
network can load and unload models or read the API key in the UI" is therefore expected
behavior, not a vulnerability. Set `ALLOWED_HOSTS` (see the README) so other web pages cannot
reach the UI through DNS rebinding.

Things that *are* worth reporting, for example:

- reading, writing or deleting files outside `/config/models`, `/data/Strata-data/models` and
  `/models` (path traversal);
- running arbitrary commands or engine flags through model configs beyond the documented
  `--set` rows (command injection);
- anything that lets a web page on another site act on the UI despite `ALLOWED_HOSTS` and the
  `Origin` check (CSRF/XSS);
- the API key showing up in a log or page where the UI claims to hide it.

Problems in Strata itself or its base image belong to the
[Strata project](https://github.com/Niko1221/Strata).

## Reporting

Please report vulnerabilities **privately** via GitHub's
[private vulnerability reporting](https://github.com/sognix/strata-supervisor/security/advisories/new)
rather than a public issue. Include what you found, how to reproduce it and which image tag you
used.

This is a private hobby project, so there is no guaranteed response time. Fixed issues are
credited in the release notes unless you prefer otherwise.
