"""Local UI smoke test with a headless browser; no Yandex/Telegram calls."""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DEV_AUTH"] = "1"

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
import uvicorn

with tempfile.TemporaryDirectory(dir=ROOT / "tests") as data_dir:
    os.environ["DATA_DIR"] = data_dir
    from app.api import app
    from app.lead_parser import make_driver

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8766, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    driver = make_driver()
    try:
        driver.set_window_size(390, 844)
        driver.get("http://127.0.0.1:8766/")
        WebDriverWait(driver, 15).until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "#country option")) >= 9)
        assert driver.find_element(By.ID, "page-title").text == "Поиск"
        driver.find_element(By.CSS_SELECTOR, '[data-page="scripts"]').click()
        WebDriverWait(driver, 15).until(lambda d: len(d.find_elements(By.CSS_SELECTOR, ".template-card")) == 18)
        driver.find_element(By.CSS_SELECTOR, '[data-page="stats"]').click()
        WebDriverWait(driver, 15).until(lambda d: len(d.find_elements(By.CSS_SELECTOR, ".stat-tile")) == 4)
        driver.find_element(By.CSS_SELECTOR, '[data-page="search"]').click()
        driver.find_element(By.ID, "theme-button").click()
        driver.find_element(By.CSS_SELECTOR, '[data-accent="violet"].swatch').click()
        assert driver.execute_script("return document.documentElement.dataset.accent") == "violet"
        driver.find_element(By.CSS_SELECTOR, '[data-accent="blue"].swatch').click()
        driver.find_element(By.ID, "theme-button").click()
        print("UI tabs, configuration, statistics and templates passed")
        screenshot = ROOT / ".testdata" / "ui-smoke.png"
        screenshot.parent.mkdir(exist_ok=True)
        driver.save_screenshot(str(screenshot))
        print(screenshot)
    finally:
        driver.quit()
        server.should_exit = True
        thread.join(timeout=5)
