"""Display login QR directly on the terminal, with a four-module quiet zone."""

import sys
from typing import TextIO

from pydantic import SecretStr


def show_qr(link: SecretStr, output: TextIO | None = None) -> None:
    import qrcode

    output = output if output is not None else sys.stdout
    qr = qrcode.QRCode(border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(link.get_secret_value())
    qr.make(fit=True)
    output.write('\nСканируй QR приложением Яндекса и подтверди вход:\n\n')
    qr.print_ascii(out=output, tty=output.isatty(), invert=True)
    output.write('\n')
    output.flush()
