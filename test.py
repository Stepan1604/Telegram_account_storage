# ============================================================================
# Проверка полей карточки лота LZT (только чтение, без покупки).
#
# Запуск из папки проекта (рядом с config.py):
#     python test_card.py 267935081
#     python test_card.py 267935081 267763744      # несколько лотов сразу
#
# Выводит:
#   - все ключи карточки (отсортированные);
#   - значения ключей, похожих на спамблок / статус / блокировку;
#   - сырые значения telegram_* полей для сравнения лотов.
# ============================================================================

import asyncio
import re
import sys

from LOLZTEAM.Client import Market

import config as cfg

SPAM_PATTERN = re.compile(r"spam|block|restrict|limit|ban|flood", re.IGNORECASE)


def walk(d, prefix=""):
    """Обходит вложенный JSON и отдаёт (полный_путь, значение)."""
    if isinstance(d, dict):
        for k, v in d.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            yield path, v
            yield from walk(v, path)
    elif isinstance(d, list):
        for i, x in enumerate(d[:20]):
            yield from walk(x, f"{prefix}[{i}]")


async def show_card(market, item_id: int):
    print("=" * 80)
    print(f"Лот {item_id}")
    print("=" * 80)

    resp = await asyncio.wait_for(market.request("GET", f"/{item_id}"), 60)
    print(f"HTTP {resp.status_code}")
    if resp.status_code != 200:
        print("тело:", (resp.text or "")[:300])
        return

    data = resp.json()
    card = data.get("item", data) if isinstance(data, dict) else {}

    print(f"\nВсего ключей верхнего уровня: {len(card)}")
    print("Ключи:", ", ".join(sorted(card.keys())))

    print("\n--- Поля, похожие на спамблок / блокировку (все уровни) ---")
    found = False
    for path, value in walk(card):
        leaf = path.split(".")[-1]
        if SPAM_PATTERN.search(leaf):
            found = True
            print(f"{path} = {str(value)[:120]!r}")
    if not found:
        print("(нет ни одного поля с такими словами)")

    print("\n--- telegram_* поля верхнего уровня ---")
    for k in sorted(card.keys()):
        if k.startswith("telegram_") and not isinstance(card[k], (dict, list)):
            print(f"{k} = {str(card[k])[:80]!r}")
    print()


async def main(ids):
    market = Market(token=cfg.LZT_API_TOKEN)
    if getattr(cfg, "LZT_PROXY", ""):
        market.settings.proxy = cfg.LZT_PROXY
    for item_id in ids:
        try:
            await show_card(market, item_id)
        except Exception as e:
            print(f"Лот {item_id}: {type(e).__name__}: {str(e)[:200]}\n")


if __name__ == "__main__":
    ids = [int(x) for x in sys.argv[1:]] or [267935081]
    asyncio.run(main(ids))



