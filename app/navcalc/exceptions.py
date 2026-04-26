from __future__ import annotations


class NavCalcError(RuntimeError):
    pass


class NavConfigError(NavCalcError):
    pass


class BybitAuthError(NavCalcError):
    pass


class BybitNetworkError(NavCalcError):
    pass


class InvalidWalletResponseError(NavCalcError):
    pass


class NavSanityCheckError(NavCalcError):
    pass


class FundDisabledError(NavCalcError):
    pass