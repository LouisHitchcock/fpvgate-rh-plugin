# Mocked tuning regressions

These tests make no requests to physical gates. They exercise clock resets,
confirmed-frequency handling, receiver-sample capture, write failures and the
fallback tuning routes' authentication and validation.

Set `RH_SOURCE` to a RotorHazard source checkout and use a Python environment
with that checkout's server requirements installed:

```powershell
$env:RH_SOURCE = 'C:\path\to\RotorHazard'
python -m unittest discover -s tests -p test_tuning.py
```

Receiver sampling is injected during the three-second capture wait, so these
checks are deterministic and do not measure radio detection accuracy.

The older-RH graph page also has an intercepted-HTTP browser regression. With
Node.js 18 or later, Playwright and Chromium available, run:

```text
node --test tests/test_tuning_page.cjs
```

`PLAYWRIGHT_MODULE` and `PLAYWRIGHT_CHROMIUM_EXECUTABLE` can select existing
development installations. Every browser request is intercepted; this check
does not connect to a physical gate or running RH installation.
