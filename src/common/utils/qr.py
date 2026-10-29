from io import BytesIO

from pydantic import SecretStr


def qr_png(link: SecretStr) -> bytes:
    import qrcode

    qr = qrcode.QRCode(border=4, box_size=8, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(link.get_secret_value())
    qr.make(fit=True)
    stream = BytesIO()
    qr.make_image(fill_color='black', back_color='white').save(stream, format='PNG')
    return stream.getvalue()
