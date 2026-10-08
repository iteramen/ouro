# External references

Sibling-repo sources: `vendorlib/motor_ctl.c`, `vendorlib/printf_queue.h`.
Host path: `hostsite/mu-plugins/ci-downloads.php`.
Build output: `build-out/results/`, `dist/_bundle/`.

S9 extraction cases (never probed -- fixtures are out of the live sweep's scope):
a URL with trailing punctuation, see https://fixture-dead-host.fixture-repo.net/some/path,
an exempt placeholder http://example.com/skipme plus http://localhost:8080/dev,
and a reserved-TLD host https://docs.local/00 that must not be extracted either.

```text
https://fenced-host.fixture-repo.net/never-extracted
```
