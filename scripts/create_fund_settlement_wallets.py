from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Literal

from dotenv import load_dotenv
from eth_account import Account
from sqlalchemy.orm import Session
from web3 import Web3

from app.db import SessionLocal
from app.models import Fund, FundWallet
from app.wallets import encrypt_private_key


WalletSetState = Literal["empty", "complete", "partial"]


@dataclass(frozen=True)
class FundWalletSpec:
    fund_code: str
    derivation_index: int

    @property
    def derivation_path(self) -> str:
        return f"m/44'/60'/0'/0/{self.derivation_index}"


FUND_WALLET_SPECS = [
    FundWalletSpec("btc_fund", 0),
    FundWalletSpec("defi_sniper", 1),
    FundWalletSpec("wb10", 2),
    FundWalletSpec("wb_test", 3),
    FundWalletSpec("wb_defi", 4),
    FundWalletSpec("wb_web3", 5),
]

EXPECTED_FUND_CODES = [spec.fund_code for spec in FUND_WALLET_SPECS]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create BSC settlement wallets for Wild Boar funds."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required safety flag. Without it the script exits without changes.",
    )
    return parser.parse_args()


def _normalize_private_key(priv_hex: str) -> str:
    value = (priv_hex or "").strip()
    if not value.startswith("0x"):
        value = "0x" + value
    return value


def _get_fund_by_code(db: Session, fund_code: str) -> Fund:
    fund = db.query(Fund).filter(Fund.code == fund_code).first()
    if fund is None:
        raise RuntimeError(f"Fund not found in DB: {fund_code}")
    return fund


def _get_funds_by_code(db: Session) -> dict[str, Fund]:
    funds = (
        db.query(Fund)
        .filter(Fund.code.in_(EXPECTED_FUND_CODES))
        .all()
    )
    funds_by_code = {fund.code: fund for fund in funds}

    missing = [code for code in EXPECTED_FUND_CODES if code not in funds_by_code]
    if missing:
        raise RuntimeError("Funds not found in DB: " + ", ".join(missing))

    return funds_by_code


def get_existing_active_wallets_by_fund(db: Session) -> dict[str, FundWallet]:
    """
    Return active BSC settlement wallets for the 6 expected funds.

    Criteria:
    - fund_wallets.fund_id = funds.id
    - blockchain = 'BSC'
    - wallet_type = 'settlement'
    - is_active = true
    """
    rows = (
        db.query(Fund.code, FundWallet)
        .join(FundWallet, FundWallet.fund_id == Fund.id)
        .filter(
            Fund.code.in_(EXPECTED_FUND_CODES),
            FundWallet.blockchain == "BSC",
            FundWallet.wallet_type == "settlement",
            FundWallet.is_active == True,
        )
        .all()
    )

    return {fund_code: wallet for fund_code, wallet in rows}


def validate_wallet_set_state(existing_map: dict[str, FundWallet]) -> WalletSetState:
    existing_count = len(existing_map)

    if existing_count == 0:
        return "empty"

    if existing_count == len(EXPECTED_FUND_CODES):
        return "complete"

    return "partial"


def _print_existing_wallets(existing_map: dict[str, FundWallet]) -> None:
    print("Existing active settlement wallets:")
    for spec in FUND_WALLET_SPECS:
        wallet = existing_map.get(spec.fund_code)
        if wallet is None:
            continue

        derivation_path = wallet.derivation_path or spec.derivation_path
        print(f"{spec.fund_code:<12} {derivation_path:<20} {wallet.address}")


def _raise_partial_wallet_set(existing_map: dict[str, FundWallet]) -> None:
    existing_codes = [code for code in EXPECTED_FUND_CODES if code in existing_map]
    missing_codes = [code for code in EXPECTED_FUND_CODES if code not in existing_map]

    print("")
    print(
        "Partial settlement wallet set detected. Refusing to create missing wallets "
        "because this could mix different seed phrases. Expected either 0 or 6 "
        "active settlement wallets."
    )
    print("")
    print("Existing active wallets:")
    for code in existing_codes:
        wallet = existing_map[code]
        print(f"- {code}: {wallet.derivation_path or '-'} {wallet.address}")

    print("")
    print("Missing active wallets:")
    for code in missing_codes:
        print(f"- {code}")

    raise RuntimeError("partial_settlement_wallet_set")


def _create_all_wallets_from_new_seed(db: Session, funds_by_code: dict[str, Fund]) -> None:
    # eth-account deliberately marks HD wallet features as unaudited.
    # We use it only for deterministic local key derivation from one mnemonic.
    Account.enable_unaudited_hdwallet_features()

    _first_account, mnemonic = Account.create_with_mnemonic(num_words=12)

    print("")
    print("IMPORTANT: save this seed phrase offline. It will not be stored.")
    print("Seed phrase:", mnemonic)
    print("")
    print("Fund wallets:")

    created_rows: list[tuple[str, str, str]] = []

    for spec in FUND_WALLET_SPECS:
        fund = funds_by_code[spec.fund_code]

        account = Account.from_mnemonic(
            mnemonic,
            account_path=spec.derivation_path,
        )

        private_key = _normalize_private_key(account.key.hex())
        address = Web3.to_checksum_address(account.address)
        encrypted_private_key = encrypt_private_key(private_key)

        wallet = FundWallet(
            fund_id=fund.id,
            blockchain="BSC",
            wallet_type="settlement",
            address=address,
            encrypted_private_key=encrypted_private_key,
            derivation_path=spec.derivation_path,
            derivation_index=spec.derivation_index,
            is_active=True,
        )
        db.add(wallet)
        db.flush()

        created_rows.append((spec.fund_code, spec.derivation_path, address))

    db.commit()

    for fund_code, path, address in created_rows:
        print(f"{fund_code:<12} {path:<20} {address}")

    print("")
    print("Seed phrase was printed once and was not stored in DB.")
    print("Private keys were encrypted with existing WALLET_ENC_KEY.")


def create_fund_settlement_wallets() -> int:
    db = SessionLocal()
    try:
        funds_by_code = _get_funds_by_code(db)
        existing_map = get_existing_active_wallets_by_fund(db)
        state = validate_wallet_set_state(existing_map)

        if state == "complete":
            print("")
            print("Complete active settlement wallet set already exists. No changes made.")
            print("")
            _print_existing_wallets(existing_map)
            return 0

        if state == "partial":
            _raise_partial_wallet_set(existing_map)

        # state == "empty"
        _create_all_wallets_from_new_seed(db, funds_by_code)
        return 0

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    load_dotenv()

    args = parse_args()
    if not args.yes:
        print("No changes made. Re-run with --yes to create settlement wallets.")
        return 1

    try:
        return create_fund_settlement_wallets()
    except RuntimeError as exc:
        if str(exc) == "partial_settlement_wallet_set":
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())