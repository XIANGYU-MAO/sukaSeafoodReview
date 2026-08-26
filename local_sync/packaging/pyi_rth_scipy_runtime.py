"""Keep SciPy's production imports independent from its excluded test runner."""

from __future__ import annotations

import sys
from types import ModuleType


class PytestTester:
    """Stand-in for SciPy's package-level ``test`` convenience attribute."""

    def __init__(self, package_name: str) -> None:
        self.package_name = package_name

    def __call__(self, *args, **kwargs):
        raise RuntimeError("Package test support is not included in this application")


numpy_pytesttester = ModuleType("numpy._pytesttester")
numpy_pytesttester.PytestTester = PytestTester
sys.modules["numpy._pytesttester"] = numpy_pytesttester

pywt_pytesttester = ModuleType("pywt._pytesttester")
pywt_pytesttester.PytestTester = PytestTester
sys.modules["pywt._pytesttester"] = pywt_pytesttester

module = ModuleType("scipy._lib._testutils")
module.PytestTester = PytestTester
sys.modules["scipy._lib._testutils"] = module

# SciPy's vendored array-api compatibility layer clones NumPy with ``import
# *``. NumPy exposes ``testing`` in that public list, so the clone otherwise
# imports stdlib unittest even though no production calculation uses it.
import numpy


numpy_testing = ModuleType("numpy.testing")
sys.modules["numpy.testing"] = numpy_testing
numpy.testing = numpy_testing
