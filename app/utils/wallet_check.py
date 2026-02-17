import re
import importlib.util


# Компилируем паттерн: адрес должен начинаться с "0x" и далее 40 символов 0-9 или A-F (регистр игнорируется)
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-f]{40}$", re.IGNORECASE)


def is_valid_eth_address(address: str) -> bool:
    """Проверяет формат Ethereum-адреса по правилам:
    - начинается с "0x"
    - далее ровно 40 символов из диапазона 0-9, A-F (игнорируя регистр)
    """
    if not isinstance(address, str):
        return False
    addr = address.strip()
    return bool(ADDRESS_PATTERN.fullmatch(addr))


def validate_eth_address(address: str) -> str:
    """Возвращает адрес, если формат корректный, иначе выбрасывает ValueError.

    Дополнительно: если адрес содержит смешанный регистр (и строчные, и заглавные
    буквы), проверяется корректность контрольной суммы по EIP-55. В случае
    несоответствия — выбрасывается ValueError.
    """
    if not is_valid_eth_address(address):
        raise ValueError(
            "Неверный формат адреса Ethereum: ожидается '0x' + 40 символов [0-9A-F]"
        )

    addr = address.strip()
    hex_part = addr[2:]
    has_lower = any(c in "abcdef" for c in hex_part)
    has_upper = any(c in "ABCDEF" for c in hex_part)

    if has_lower and has_upper:
        expected = to_checksum_address_eip55(addr)
        if addr != expected:
            raise ValueError(
                f"Неверная контрольная сумма (EIP-55). Ожидается: {expected}"
            )

    return addr


def _has_module(module_name: str) -> bool:
    spec = importlib.util.find_spec(module_name)
    return spec is not None


def _keccak256_hex(text: str) -> str:
    """Возвращает hex-строку Keccak-256(text) без префикса 0x.

    Пытается использовать один из доступных бекендов:
    - web3 (через eth_utils внутри)
    - eth_hash.auto
    - pysha3 (модуль sha3)
    - pycryptodome (Crypto.Hash.keccak)
    """
    # Попытка через eth_hash.auto (если модуль доступен; ожидает bytes)
    if _has_module("eth_hash"):
        try:
            from eth_hash.auto import keccak as _keccak  # type: ignore

            return _keccak(text.encode("ascii")).hex()
        except Exception:
            pass

    # Попытка через pysha3 (sha3)
    try:
        import sha3  # type: ignore

        h = sha3.keccak_256()
        h.update(text.encode("ascii"))
        return h.hexdigest()
    except Exception:
        pass

    # Попытка через pycryptodome
    try:
        from Crypto.Hash import keccak  # type: ignore

        k = keccak.new(digest_bits=256)
        k.update(text.encode("ascii"))
        return k.hexdigest()
    except Exception:
        pass

    raise RuntimeError(
        "Для проверки EIP-55 требуется Keccak-256. Установите один из пакетов: "
        "'web3', 'eth-hash', 'pysha3' или 'pycryptodome'."
    )


def to_checksum_address_eip55(address: str) -> str:
    """Возвращает адрес в формате EIP-55. При наличии Web3 использует его.

    Если Web3 недоступен, использует локальную реализацию на основе Keccak-256.
    Ожидает вход в формате '0x' + 40 hex-символов.
    """
    # Попытка использовать Web3, если установлен
    try:
        from web3 import Web3  # type: ignore

        return Web3.to_checksum_address(address)
    except Exception:
        pass

    # Локальная реализация EIP-55
    if not ADDRESS_PATTERN.fullmatch(address):
        raise ValueError("Некорректный формат адреса для чексанирования EIP-55")

    hex_addr = address.lower()[2:]
    hash_hex = _keccak256_hex(hex_addr)
    checksummed = ["0x"]
    for i, ch in enumerate(hex_addr):
        if ch in "abcdef":
            # Бит правила: если соответствующий хекс-символ хэша >= 8 — букву делаем верхним регистром
            if int(hash_hex[i], 16) >= 8:
                checksummed.append(ch.upper())
            else:
                checksummed.append(ch)
        else:
            checksummed.append(ch)
    return "".join(checksummed)


def validate_address_status(address: str) -> str:
    """
    Returns:
      - "valid"    : адрес валиден (формат ок; если mixed-case — checksum корректен)
      - "checksum" : формат ок, но mixed-case checksum неверный
      - "invalid"  : формат невалиден
    """
    if not address:
        return "invalid"

    addr = address.strip()
    if not addr:
        return "invalid"

    # 1) Формат (0x + 40 hex)
    if not is_valid_eth_address(addr):
        return "invalid"

    # 2) Определяем mixed-case
    body = addr[2:]
    has_lower = any(c.isalpha() and c.islower() for c in body)
    has_upper = any(c.isalpha() and c.isupper() for c in body)

    # Если НЕ mixed-case — считаем валидным (формально подходит)
    if not (has_lower and has_upper):
        return "valid"

    # 3) Для mixed-case проверяем checksum
    try:
        validate_eth_address(addr)
        return "valid"
    except ValueError:
        # формат ок (мы уже проверили), но checksum не сошёлся
        return "checksum"
    except Exception:
        return "invalid"


if __name__ == "__main__":
    # Пример проверки с указанным адресом
    address = ""
    try:
        validated = validate_eth_address(address)
        print("Адрес валиден по формату:", validated)
    except ValueError as exc:
        print("Неверный формат адреса:", exc)
