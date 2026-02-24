import json
import re
from pathlib import Path
from urllib.request import urlopen, Request

DEFAULT_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"

RX_EVM = re.compile(r"0x[a-fA-F0-9]{40}")

def main():
    root = Path(__file__).resolve().parent.parent
    out = root / "data" / "ofac_addresses.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    req = Request(DEFAULT_URL, headers={"User-Agent": "WildBoar/1.0"})
    with urlopen(req, timeout=30) as r:
        content = r.read().decode("utf-8", errors="ignore")

    addrs = sorted({m.group(0).lower() for m in RX_EVM.finditer(content)})
    out.write_text(json.dumps(addrs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(addrs)} addresses to {out}")

if __name__ == "__main__":
    main()