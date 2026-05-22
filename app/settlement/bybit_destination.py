from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import FundBybitAccount


class BybitDestinationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BybitDepositDestination:
    deposit_address: str
    deposit_tag: str | None
    chain_type: str
    bybit_sub_uid: str


def get_active_bybit_deposit_destination(
    db: Session,
    fund_id: int,
    coin: str = "USDT",
    chain_type: str = "BSC",
) -> BybitDepositDestination:
    row = (
        db.query(FundBybitAccount)
        .filter(
            FundBybitAccount.fund_id == fund_id,
            FundBybitAccount.coin == coin.strip().upper(),
            FundBybitAccount.chain_type == chain_type.strip(),
            FundBybitAccount.is_active == True,
        )
        .first()
    )

    if row is None:
        raise BybitDestinationError(
            f"Active Bybit deposit destination not found for fund_id={fund_id} "
            f"coin={coin} chain_type={chain_type}"
        )

    if not row.deposit_address:
        raise BybitDestinationError(
            f"Bybit deposit destination has empty address for fund_id={fund_id}"
        )

    return BybitDepositDestination(
        deposit_address=row.deposit_address,
        deposit_tag=row.deposit_tag,
        chain_type=row.chain_type,
        bybit_sub_uid=row.bybit_sub_uid,
    )