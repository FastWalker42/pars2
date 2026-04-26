import asyncio
import json
from playwright.async_api import async_playwright
from typing import List, Dict, Set
import re
from pathlib import Path
import aiohttp
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image,
    PageBreak,
    Table,
    TableStyle,
)
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from io import BytesIO
import base64
import os


class AvitoParser:
    def __init__(self):
        self.items: Dict[str, Dict] = {}
        self.seen_ids: Set[str] = set()
        self.category_map: Dict[str, str] = {}
        self.usd_rate: float = 0.0
        self.image_cache: Dict[str, bytes] = {}

    async def load_category_map(self):
        """Загружает маппинг категорий"""
        try:
            with open("categorymap.json", "r", encoding="utf-8") as f:
                self.category_map = json.load(f)
            print(f"✓ Загружено категорий: {len(self.category_map)}")
        except FileNotFoundError:
            print("⚠ Файл categorymap.json не найден, категории не будут определяться")
            self.category_map = {}
        except Exception as e:
            print(f"⚠ Ошибка загрузки категорий: {e}")
            self.category_map = {}

    async def fetch_usd_rate(self):
        """Получает курс USD/RUB с ЦБ РФ"""
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://www.cbr-xml-daily.ru/daily_json.js"
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.usd_rate = data["Valute"]["USD"]["Value"]
                        print(f"✓ Курс USD: {self.usd_rate:.2f} ₽")
                    else:
                        raise Exception(f"HTTP {response.status}")
        except Exception as e:
            print(f"⚠ Не удалось получить курс USD: {e}")
            self.usd_rate = 75.27  # Fallback значение
            print(f"  Используется резервный курс: {self.usd_rate} ₽")

    def detect_category(self, url: str) -> str:
        """Определяет категорию по URL"""
        if not url:
            return "Не определена"

        for key, value in self.category_map.items():
            if f"/{key}" in url or f"/{key}/" in url:
                return value

        return "Не определена"

    def rub_to_usd(self, rub_price: int) -> float:
        """Конвертирует рубли в доллары"""
        if self.usd_rate > 0:
            return rub_price / self.usd_rate
        return 0.0

    async def fetch_image_as_blob(self, page, image_url: str) -> bytes:
        """Получает изображение как blob через Playwright без повторных запросов"""
        if image_url in self.image_cache:
            return self.image_cache[image_url]

        try:
            # Используем CDP (Chrome DevTools Protocol) для получения изображения
            image_data = await page.evaluate(
                """
                async (url) => {
                    try {
                        const response = await fetch(url);
                        const blob = await response.blob();
                        return new Promise((resolve) => {
                            const reader = new FileReader();
                            reader.onloadend = () => resolve(reader.result.split(',')[1]);
                            reader.readAsDataURL(blob);
                        });
                    } catch (e) {
                        return null;
                    }
                }
            """,
                image_url,
            )

            if image_data:
                blob = base64.b64decode(image_data)
                self.image_cache[image_url] = blob
                return blob

        except Exception as e:
            print(f"  ⚠ Ошибка получения изображения: {e}")

        return b""

    async def extract_item_data_from_element(self, item) -> Dict:
        """Извлекает данные напрямую из элемента через Playwright"""
        data = {}

        try:
            # ID товара
            item_id = await item.get_attribute("data-item-id")
            if item_id:
                data["id"] = item_id

            # Название
            try:
                title_elem = await item.locator(
                    'h2[itemprop="name"] a, [data-marker="item-title"]'
                ).first.get_attribute("title")
                if title_elem:
                    data["title"] = title_elem
            except:
                pass

            # URL
            try:
                url_elem = await item.locator('a[itemprop="url"]').first.get_attribute(
                    "href"
                )
                if url_elem:
                    data["url"] = (
                        "https://www.avito.ru" + url_elem
                        if url_elem.startswith("/")
                        else url_elem
                    )
                    # Определяем категорию
                    data["category"] = self.detect_category(data["url"])
            except:
                pass

            # Цена
            try:
                price_elem = await item.locator(
                    'meta[itemprop="price"]'
                ).first.get_attribute("content")
                if price_elem:
                    price_rub = int(price_elem)
                    price_usd = self.rub_to_usd(price_rub)

                    data["price_rub"] = price_rub
                    data["price_usd"] = price_usd
                    data["price_formatted"] = f"{price_rub:,}".replace(",", " ") + " ₽"
                    data["price_usd_formatted"] = f"${price_usd:.2f}"
            except:
                try:
                    price_text = await item.locator(
                        '[data-marker="item-price"]'
                    ).first.inner_text()
                    price_clean = re.sub(r"[^\d]", "", price_text)
                    if price_clean:
                        price_rub = int(price_clean)
                        price_usd = self.rub_to_usd(price_rub)

                        data["price_rub"] = price_rub
                        data["price_usd"] = price_usd
                        data["price_formatted"] = price_text.strip()
                        data["price_usd_formatted"] = f"${price_usd:.2f}"
                except:
                    pass

            # Изображение
            try:
                img_elem = await item.locator(
                    'img[itemprop="image"]'
                ).first.get_attribute("src")
                if img_elem:
                    data["image_url"] = img_elem
            except:
                pass

            # Местоположение
            try:
                location_elem = await item.locator(
                    '[data-marker="item-location"]'
                ).first.inner_text()
                if location_elem:
                    data["location"] = location_elem.strip()
            except:
                pass

            # Дата
            try:
                date_elem = await item.locator(
                    '[data-marker="item-date"]'
                ).first.inner_text()
                if date_elem:
                    data["date"] = date_elem.strip()
            except:
                pass

            # Описание
            try:
                desc_elem = await item.locator(
                    'meta[itemprop="description"]'
                ).first.get_attribute("content")
                if desc_elem:
                    data["description"] = desc_elem
            except:
                pass

        except Exception as e:
            print(f"  ⚠ Ошибка извлечения данных: {e}")

        return data

    async def scroll_and_parse(self, page, url: str) -> List[Dict]:
        """Прокручивает страницу и собирает все товары"""
        print("Загружаю страницу...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"Ошибка загрузки страницы: {e}")
            print("Пытаюсь продолжить...")

        print("Жду появления товаров на странице...")
        try:
            await page.wait_for_selector(
                '[data-marker*="item_list_with_filters/item"]', timeout=30000
            )
            print("Товары найдены!")
        except Exception as e:
            print(f"⚠ Не удалось дождаться товаров: {e}")

        await asyncio.sleep(3)

        previous_count = 0
        no_new_items_count = 0
        max_no_new_attempts = 5
        scroll_attempt = 0

        print("Начинаю сбор товаров...\n")

        while True:
            scroll_attempt += 1
            print(f"{'='*60}")
            print(f"Прокрутка #{scroll_attempt}")
            print(f"{'='*60}")

            items = []
            try:
                items = await page.locator(
                    '[data-marker*="item_list_with_filters/item"]'
                ).all()
                print(f"Найдено элементов на странице: {len(items)}")
            except Exception as e:
                print(f"⚠ Ошибка поиска элементов: {e}")

            if not items:
                print("❌ Товары не найдены!")
                break

            new_items_in_batch = 0
            for idx, item in enumerate(items, 1):
                try:
                    item_id = await item.get_attribute("data-item-id")
                    if not item_id or item_id in self.seen_ids:
                        continue

                    item_data = await self.extract_item_data_from_element(item)

                    if item_data.get("id"):
                        # Получаем изображение как blob
                        if item_data.get("image_url"):
                            image_blob = await self.fetch_image_as_blob(
                                page, item_data["image_url"]
                            )
                            if image_blob:
                                item_data["image_blob"] = image_blob

                        self.items[item_id] = item_data
                        self.seen_ids.add(item_id)
                        new_items_in_batch += 1

                        title_preview = item_data.get("title", "Без названия")[:40]
                        price_rub = item_data.get("price_formatted", "—")
                        price_usd = item_data.get("price_usd_formatted", "—")
                        category = item_data.get("category", "—")
                        print(
                            f"  [{idx}] ✓ {title_preview}... | {price_rub} ({price_usd}) | {category}"
                        )

                except Exception as e:
                    print(f"  [{idx}] ✗ Ошибка: {e}")
                    continue

            current_count = len(self.items)
            print(f"\n📊 Статистика:")
            print(f"  • Новых в этой порции: {new_items_in_batch}")
            print(f"  • Всего уникальных: {current_count}")

            if current_count == previous_count:
                no_new_items_count += 1
                print(
                    f"  ⚠ Попыток без новых товаров: {no_new_items_count}/{max_no_new_attempts}"
                )
                if no_new_items_count >= max_no_new_attempts:
                    print(f"\n{'='*60}")
                    print("✓ Достигнут конец списка товаров")
                    print(f"{'='*60}\n")
                    break
            else:
                no_new_items_count = 0

            previous_count = current_count

            print("\n⬇ Прокручиваю страницу вниз...")
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

            for i in range(3):
                await page.mouse.wheel(0, 1000)
                await asyncio.sleep(0.5)

            print()

        return list(self.items.values())

    def register_fonts(self):
        """Регистрирует шрифты с кириллицей"""
        # Список путей где искать шрифты Times New Roman (100% есть в Windows 7+)
        font_paths = [
            # Windows
            (r"C:\Windows\Fonts\times.ttf", r"C:\Windows\Fonts\timesbd.ttf"),
            (r"C:\WINDOWS\Fonts\times.ttf", r"C:\WINDOWS\Fonts\timesbd.ttf"),
            # Linux DejaVu (запасной вариант)
            (
                "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            ),
            # macOS
            (
                "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
                "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
            ),
        ]

        font_registered = False

        for regular_path, bold_path in font_paths:
            try:
                if os.path.exists(regular_path):
                    pdfmetrics.registerFont(TTFont("TimesNew", regular_path))
                    print(f"✓ Обычный шрифт: {regular_path}")

                    if os.path.exists(bold_path):
                        pdfmetrics.registerFont(TTFont("TimesNewBold", bold_path))
                        print(f"✓ Жирный шрифт: {bold_path}")
                    else:
                        # Используем обычный как жирный
                        pdfmetrics.registerFont(TTFont("TimesNewBold", regular_path))

                    font_registered = True
                    break
            except Exception as e:
                continue

        if not font_registered:
            print("⚠ Times New Roman не найден!")
            print("⚠ Используются встроенные шрифты (БЕЗ поддержки кириллицы)")
            return ("Helvetica", "Helvetica-Bold")

        return ("TimesNew", "TimesNewBold")

    def create_pdf(self, items: List[Dict], filename: str = "avito_items.pdf"):
        """Создает PDF с товарами"""
        print("\n📄 Генерирую PDF...")

        doc = SimpleDocTemplate(
            filename, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm
        )
        story = []

        # Регистрируем шрифты
        font_name, font_bold = self.register_fonts()

        styles = getSampleStyleSheet()

        # Стили
        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontName=font_bold,
            fontSize=16,
            textColor=colors.HexColor("#2d3436"),
            spaceAfter=12,
        )

        heading_style = ParagraphStyle(
            "CustomHeading",
            parent=styles["Heading2"],
            fontName=font_bold,
            fontSize=12,
            textColor=colors.HexColor("#0984e3"),
            spaceAfter=6,
        )

        normal_style = ParagraphStyle(
            "CustomNormal",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=10,
            textColor=colors.HexColor("#2d3436"),
        )

        # Заголовок отчета
        story.append(Paragraph(f"Отчет по товарам Avito", title_style))
        story.append(
            Paragraph(
                f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}", normal_style
            )
        )
        story.append(Paragraph(f"Курс USD: {self.usd_rate:.2f} ₽", normal_style))
        story.append(Paragraph(f"Всего товаров: {len(items)}", normal_style))
        story.append(Spacer(1, 0.5 * cm))

        # Товары
        for idx, item in enumerate(items, 1):
            # Название
            title = item.get("title", "Без названия")
            story.append(Paragraph(f"{idx}. {title}", heading_style))

            # Таблица с данными
            data = [
                ["Категория:", item.get("category", "—")],
                [
                    "Цена:",
                    f"{item.get('price_formatted', '—')} ({item.get('price_usd_formatted', '—')})",
                ],
                ["Местоположение:", item.get("location", "—")],
                ["Дата:", item.get("date", "—")],
            ]

            t = Table(data, colWidths=[4 * cm, 12 * cm])
            t.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), font_name),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#636e72")),
                        ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#2d3436")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            story.append(t)
            story.append(Spacer(1, 0.2 * cm))

            # Описание
            if item.get("description"):
                desc = item["description"][:300] + (
                    "..." if len(item["description"]) > 300 else ""
                )
                story.append(Paragraph(f"<b>Описание:</b> {desc}", normal_style))
                story.append(Spacer(1, 0.2 * cm))

            # Изображение
            if item.get("image_blob"):
                try:
                    img = Image(
                        BytesIO(item["image_blob"]), width=8 * cm, height=6 * cm
                    )
                    story.append(img)
                except Exception as e:
                    print(f"  ⚠ Ошибка вставки изображения для товара {idx}: {e}")

            # URL
            if item.get("url"):
                story.append(
                    Paragraph(
                        f'<link href="{item["url"]}">{item["url"]}</link>', normal_style
                    )
                )

            story.append(Spacer(1, 0.8 * cm))

            # Разделитель страницы каждые 3 товара
            if idx % 3 == 0 and idx < len(items):
                story.append(PageBreak())

        doc.build(story)
        print(f"✓ PDF сохранен: {filename}")


async def main():
    url = input("Введите URL страницы Avito: ").strip()

    if not url:
        print("❌ URL не указан!")
        return

    parser = AvitoParser()

    # Загружаем категории и курс валют
    await parser.load_category_map()
    await parser.fetch_usd_rate()

    async with async_playwright() as p:
        print("\n🚀 Запускаю браузер...")
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="ru-RU",
        )

        page = await context.new_page()

        await page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """
        )

        try:
            items = await parser.scroll_and_parse(page, url)

            if not items:
                print("\n⚠ Товары не были собраны")
                return

            # Сохраняем JSON
            output_json = "avito_items.json"
            with open(output_json, "w", encoding="utf-8") as f:
                # Убираем blob из JSON (слишком большой)
                items_for_json = []
                for item in items:
                    item_copy = item.copy()
                    if "image_blob" in item_copy:
                        del item_copy["image_blob"]
                    items_for_json.append(item_copy)
                json.dump(items_for_json, f, ensure_ascii=False, indent=2)

            print(f"\n{'='*60}")
            print(f"✅ ПАРСИНГ ЗАВЕРШЕН!")
            print(f"{'='*60}")
            print(f"📦 Всего собрано товаров: {len(items)}")
            print(f"💾 JSON сохранен: {output_json}")

            # Создаем PDF
            parser.create_pdf(items)

            print(f"{'='*60}\n")

        except Exception as e:
            print(f"\n❌ Критическая ошибка: {e}")
            import traceback

            traceback.print_exc()
        finally:
            print("\n🔄 Закрываю браузер...")
            await asyncio.sleep(2)
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
