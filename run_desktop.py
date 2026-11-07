"""CryptoHub masaüstü penceresi. Veritabanına yazmaz."""

try:
    from PySide6.QtWidgets import QApplication  # noqa: F401
except ImportError as exc:
    raise SystemExit(
        "PySide6 kurulu değil. Kurmak için: python -m pip install -r requirements-desktop.txt"
    ) from exc

from desktop.app import main

if __name__ == "__main__":
    raise SystemExit(main())
