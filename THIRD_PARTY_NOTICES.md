# Сторонние источники и лицензии

MIT-лицензия этого проекта распространяется на его код. Сторонние зависимости,
изображения и товарные знаки сохраняют собственные лицензии и права.

## Исследование протокола Маркета

Источник исследования: [xob0t/market-coin-farmer](https://github.com/xob0t/market-coin-farmer).
Ссылки на исследованные участки приведены в [документации API](docs/api-research.md).
MIT этого проекта не является перелицензированием исходников Go-референса.

## QR-поток Passport

Реализация протокола опиралась на
[AlexxIT/YandexStation](https://github.com/AlexxIT/YandexStation),
[исходная лицензия MIT](https://github.com/AlexxIT/YandexStation/blob/master/LICENSE.md).
Уведомление об авторстве и разрешении сохраняется ниже.

```text
MIT License

Copyright (c) 2020 AlexxIT

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Telegram-иконки

Интерфейс использует custom emoji из [Telegram iOS Icons](https://t.me/addemoji/tgiosicons).
В репозитории находятся идентификаторы, а не файлы этого набора. Использование и отображение
иконок регулируется условиями Telegram и правами их авторов.

## Оформление QR-кода

- `src/assets/market-coin.png`: [монета с CDN Яндекс Маркета](https://avatars.mds.yandex.net/get-market-wheel/14319203/2a0000019fdc7e589e480044c4e0dfe267b3/56x56), предоставленная пользователем.
- `src/assets/market-logo.svg`: оригинальный SVG из публичной шапки [Яндекс Маркета](https://market.yandex.ru/), получен 30.10.2026.

`src/assets/market-mono.svg` — монохромная адаптация знака «М» из оригинального SVG; `market-mono.png` — его растровая версия для генератора QR.

Права на изображения и товарные знаки принадлежат их правообладателям; MIT-лицензия проекта на них не распространяется. Проект не является официальным продуктом Яндекса.
