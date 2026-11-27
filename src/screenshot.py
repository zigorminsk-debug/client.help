"""
screenshot.py — захват скриншота экрана (PIL.ImageGrab, кроссплатформенно).
"""

import os
import tempfile

from PIL import ImageGrab


def capture() -> str:
    """
    Делает скриншот всего экрана и сохраняет во временный PNG.
    Возвращает путь к файлу или бросает исключение.
    """
    img = ImageGrab.grab()
    fd, path = tempfile.mkstemp(prefix="csl_shot_", suffix=".png")
    os.close(fd)
    img.save(path, "PNG")
    return path
