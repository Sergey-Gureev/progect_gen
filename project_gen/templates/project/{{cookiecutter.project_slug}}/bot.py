from pathlib import Path

from telebot import TeleBot
from vyper import v

config = Path(__file__).parent.joinpath("./").joinpath("config")
config_name = "stg"
v.set_config_name(config_name)
v.add_config_path(config)
v.read_in_config()


def send_file() -> None:
    chat_id = v.get("telegram.chat_id")
    token = v.get("telegram.token")
    telegram_bot = TeleBot(token)
    file_name = v.get("telegram.coverage_file_name")
    file_path = Path(__file__).parent.joinpath("./").joinpath(f"{file_name}")
    with open(file_path, "rb") as document:
        telegram_bot.send_document(
            chat_id=chat_id, document=document, caption="coverage"
        )



if __name__ == "__main__":
    send_file()