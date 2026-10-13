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
