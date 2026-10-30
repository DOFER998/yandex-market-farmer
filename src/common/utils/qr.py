from io import BytesIO
from pathlib import Path

from pydantic import SecretStr

ASSETS = Path(__file__).resolve().parents[2] / 'assets'


def qr_png(link: SecretStr) -> bytes:
    import qrcode
    from PIL import Image, ImageDraw
    from qrcode.image.styledpil import StyledPilImage
    from qrcode.image.styles.colormasks import SolidFillColorMask
    from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer

    # H compensates for the small central badge; four clear modules remain untouched.
    box, border = 12, 4
    qr = qrcode.QRCode(
        border=border, box_size=box, error_correction=qrcode.constants.ERROR_CORRECT_H
    )
    qr.add_data(link.get_secret_value())
    qr.make(fit=True)
    picture = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(radius_ratio=1),
        color_mask=SolidFillColorMask(front_color=(24, 24, 24)),
    ).convert('RGB')
    draw = ImageDraw.Draw(picture)
    offset = border * box
    far = (border + qr.modules_count - 7) * box
    for x, y in ((offset, offset), (far, offset), (offset, far)):
        draw.rectangle((x, y, x + 7 * box - 1, y + 7 * box - 1), fill='white')
        draw.rounded_rectangle((x, y, x + 7 * box - 1, y + 7 * box - 1), radius=25, fill='#181818')
        draw.rounded_rectangle(
            (x + box, y + box, x + 6 * box - 1, y + 6 * box - 1),
            radius=13,
            fill='white',
        )
        draw.rounded_rectangle(
            (x + 2 * box, y + 2 * box, x + 5 * box - 1, y + 5 * box - 1),
            radius=8,
            fill='#181818',
        )
    center = picture.width // 2
    draw.rounded_rectangle(
        (center - 54, center - 54, center + 54, center + 54), radius=20, fill='white'
    )
    with Image.open(ASSETS / 'market-mono.png') as source:
        mark = source.convert('RGBA')
        mark.thumbnail((84, 84), Image.Resampling.LANCZOS)
        picture.paste(mark, (center - mark.width // 2, center - mark.height // 2), mark)
    stream = BytesIO()
    picture.save(stream, format='PNG')
    return stream.getvalue()
