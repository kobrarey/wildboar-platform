from __future__ import annotations

import argparse
from dataclasses import dataclass

from dotenv import load_dotenv
from eth_account import Account
from sqlalchemy.orm import Session
from web3 import Web3

from app.db import SessionLocal
from app.models import Fund, FundWallet
from app.wallets import encrypt_private_key


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


def _get_active_settlement_wallet(db: Session, fund_id: int) -> FundWallet | None:
    return (
        db.query(FundWallet)
        .filter(
            FundWallet.fund_id == fund_id,
            FundWallet.blockchain == "BSC",
            FundWallet.wallet_type == "settlement",
            FundWallet.is_active == True,
        )
        .first()
    )


def create_fund_settlement_wallets() -> None:
    # eth-account deliberately marks HD wallet features as unaudited.
    # We use them only for deterministic local key derivation from one mnemonic.
    Account.enable_unaudited_hdwallet_features()

    first_account, mnemonic = Account.create_with_mnemonic(num_words=12)

    print("")
    print("IMPORTANT: save this seed phrase offline. It will not be stored.")
    print("Seed phrase:", mnemonic)
    print("")
    print("Fund wallets:")

    created_rows: list[tuple[str, str, str]] = []
    skipped_rows: list[tuple[str, str, str]] = []

    db = SessionLocal()
    try:
        for spec in FUND_WALLET_SPECS:
            fund = _get_fund_by_code(db, spec.fund_code)

            existing = _get_active_settlement_wallet(db, fund.id)
            if existing is not None:
                skipped_rows.append(
                    (
                        spec.fund_code,
                        existing.derivation_path or spec.derivation_path,
                        existing.address,
                    )
                )
                continue

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

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    for fund_code, path, address in created_rows:
        print(f"{fund_code:<12} {path:<20} {address}")

    if skipped_rows:
        print("")
        print("Skipped existing active settlement wallets:")
        for fund_code, path, address in skipped_rows:
            print(f"{fund_code:<12} {path:<20} {address}")

    print("")
    print("Seed phrase was printed once and was not stored in DB.")
    print("Private keys were encrypted with existing WALLET_ENC_KEY.")


def main() -> int:
    load_dotenv()

    args = parse_args()
    if not args.yes:
        print("No changes made. Re-run with --yes to create missing settlement wallets.")
        return 1

    create_fund_settlement_wallets()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())