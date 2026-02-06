"""
LifeOS Assistant — Entry Point.

Single entry point: `python main.py` starts the Telegram bot.
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from src.bot.telegram_bot import main

if __name__ == "__main__":
    main()
