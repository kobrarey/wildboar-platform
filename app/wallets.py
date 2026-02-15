import logging
from typing import Optional, TYPE_CHECKING

from cryptography.fernet import Fernet
from eth_account import Account
from web3 import Web3

from sqlalchemy.exc import IntegrityError

from app.config import settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import User, UserWallet

log = logging.getLogger(__name__)

# -----------------------
# Encryption layer
# -----------------------
_WALLET_ENC_KEY = settings.WALLET_ENC_KEY
if not _WALLET_ENC_KEY or _WALLET_ENC_KEY in {"CHANGE_ME", "CHANGE_ME_TO_FERNET_KEY"}:
    raise RuntimeError("WALLET_ENC_KEY is not set (or is a placeholder)")

fernet = Fernet(_WALLET_ENC_KEY.encode("utf-8"))

# -----------------------
# BSC RPC (optional for now)
# -----------------------
_BSC_RPC_URL = settings.BSC_RPC_URL
w3: Optional[Web3] = None
if _BSC_RPC_URL:
    w3 = Web3(Web3.HTTPProvider(_BSC_RPC_URL))

def encrypt_private_key(priv_hex: str) -> str:
    """Encrypts '0x....' private key to a base64 token string."""
    token = fernet.encrypt(priv_hex.encode("utf-8"))
    return token.decode("utf-8")

def decrypt_private_key(enc: str) -> str:
    """Decrypts token back to '0x....' private key string."""
    data = fernet.decrypt(enc.encode("utf-8"))
    return data.decode("utf-8")

def create_bsc_wallet_for_user(db: "Session", user: "User", commit: bool = True) -> "UserWallet":
    """
    Guarantees exactly one BSC wallet per user.
    If exists - returns it; otherwise creates wallet.
    If commit=False -> only db.add() + db.flush(), commit must be done by caller.
    """
    from app.models import UserWallet

    existing = (
        db.query(UserWallet)
        .filter(UserWallet.user_id == user.id, UserWallet.blockchain == "BSC")
        .first()
    )
    if existing:
        return existing

    acct = Account.create()
    priv_hex = acct.key.hex()
    address = Web3.to_checksum_address(acct.address)

    enc_priv = encrypt_private_key(priv_hex)

    wallet = UserWallet(
        user_id=user.id,
        blockchain="BSC",
        address=address,
        encrypted_private_key=enc_priv,
    )
    db.add(wallet)

    if commit:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            # на случай гонки за уникальность
            existing = (
                db.query(UserWallet)
                .filter(UserWallet.user_id == user.id, UserWallet.blockchain == "BSC")
                .first()
            )
            if existing:
                return existing
            raise
        db.refresh(wallet)
    else:
        # чтобы id/created_at подтянулись в рамках транзакции (не обязательно, но полезно)
        db.flush()

    return wallet
