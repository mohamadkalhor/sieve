"""The test suite, a package so the shared fixtures import one way.

`tests/test_api_acceptance.py` owns the `workspace` fixture that several
other modules reuse; without this file mypy sees those modules twice, once
as `test_api_acceptance` and once as `tests.test_api_acceptance`.
"""
