import asyncio
import json
from playwright.async_api import async_playwright
from typing import List, Dict, Set
import re


class AvitoParser:
    def __init__(self):
        self.items: Dict[str, Dict] = {}
        self.seen_ids: Set[str] = set()

    async def extract_item_data_from_element(self, item) -> Dict:
        """Извлекает данные напрямую из элемента через Playwright"""
        data = {}

        try:
            # ID товара - из атрибута
            item_id = await item.get_attribute("data-item-id")
            if item_id:
                data["id"] = item_id

            # Название - ищем заголовок
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
            except:
                pass

            # Цена - из meta тега
            try:
                price_elem = await item.locator(
                    'meta[itemprop="price"]'
                ).first.get_attribute("content")
                if price_elem:
                    data["price"] = int(price_elem)
                    data["price_formatted"] = (
                        f"{data['price']:,}".replace(",", " ") + " ₽"
                    )
            except:
                # Пробуем альтернативный способ
                try:
                    price_text = await item.locator(
                        '[data-marker="item-price"]'
                    ).first.inner_text()
                    price_clean = re.sub(r"[^\d]", "", price_text)
                    if price_clean:
                        data["price"] = int(price_clean)
                        data["price_formatted"] = price_text.strip()
                except:
                    pass

            # Изображение
            try:
                img_elem = await item.locator(
                    'img[itemprop="image"]'
                ).first.get_attribute("src")
                if img_elem:
                    data["image"] = img_elem
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

        # Ждем появления товаров
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

            # Находим все элементы товаров
            items = []

            try:
                # Основной селектор для товаров
                items = await page.locator(
                    '[data-marker*="item_list_with_filters/item"]'
                ).all()
                print(f"Найдено элементов на странице: {len(items)}")
            except Exception as e:
                print(f"⚠ Ошибка поиска элементов: {e}")

            if not items:
                print("❌ Товары не найдены!")
                html = await page.content()
                with open("debug_page.html", "w", encoding="utf-8") as f:
                    f.write(html)
                print("HTML страницы сохранен в debug_page.html для отладки")
                break

            # Парсим каждый элемент
            new_items_in_batch = 0
            for idx, item in enumerate(items, 1):
                try:
                    # Получаем ID
                    item_id = await item.get_attribute("data-item-id")

                    if not item_id:
                        print(f"  [{idx}] ⚠ Пропускаю: нет ID")
                        continue

                    # Проверяем, не видели ли мы этот товар раньше
                    if item_id in self.seen_ids:
                        continue

                    # Извлекаем данные
                    item_data = await self.extract_item_data_from_element(item)

                    if item_data.get("id"):
                        self.items[item_id] = item_data
                        self.seen_ids.add(item_id)
                        new_items_in_batch += 1

                        title_preview = item_data.get("title", "Без названия")[:50]
                        price = item_data.get("price_formatted", "Цена не указана")
                        print(f"  [{idx}] ✓ #{item_id}: {title_preview}... | {price}")

                except Exception as e:
                    print(f"  [{idx}] ✗ Ошибка: {e}")
                    continue

            current_count = len(self.items)
            print(f"\n📊 Статистика:")
            print(f"  • Новых в этой порции: {new_items_in_batch}")
            print(f"  • Всего уникальных: {current_count}")

            # Проверяем прогресс
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

            # Прокручиваем вниз
            print("\n⬇ Прокручиваю страницу вниз...")
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

            # Дополнительные прокрутки
            for i in range(3):
                await page.mouse.wheel(0, 1000)
                await asyncio.sleep(0.5)

            print()

        return list(self.items.values())


async def main():
    url = input("Введите URL страницы Avito: ").strip()

    if not url:
        print("❌ URL не указан!")
        return

    parser = AvitoParser()

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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            locale="ru-RU",
        )

        page = await context.new_page()

        # Скрываем признаки автоматизации
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
                print("\n⚠ Товары не были собраны. Проверьте debug_page.html")
                return

            # Сохраняем результаты
            output_file = "avito_items.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)

            print(f"\n{'='*60}")
            print(f"✅ ПАРСИНГ ЗАВЕРШЕН УСПЕШНО!")
            print(f"{'='*60}")
            print(f"📦 Всего собрано товаров: {len(items)}")
            print(f"💾 Результаты сохранены в: {output_file}")
            print(f"{'='*60}\n")

            # Показываем примеры
            if items:
                print("📋 Пример первого товара:")
                print(json.dumps(items[0], ensure_ascii=False, indent=2))

                if len(items) > 1:
                    print(f"\n📋 Пример последнего товара:")
                    print(json.dumps(items[-1], ensure_ascii=False, indent=2))

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
