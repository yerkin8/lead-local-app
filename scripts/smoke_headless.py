"""Read-only smoke test of headless Chrome and one Yandex card."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.lead_parser import make_driver, parse_org, visible_links

url = os.getenv("SMOKE_ORG_URL", "https://yandex.ru/maps/org/podocenter/118790092997/")
driver = make_driver()
try:
    driver.set_page_load_timeout(35)
    driver.get("data:text/html,<title>headless-ok</title><h1>ready</h1>")
    assert driver.title == "headless-ok"
    print("Headless Chrome started")
    try:
        row = parse_org(driver, url)
        print("socials:", [x["url"] for x in row["socials"]])
        print({"name": row["name"], "site_type": row["site_type"],
               "phones": len(row["phones"]), "socials": len(row["socials"]),
               "rating": row["rating"], "reviews": row["reviews"]})
        print("website:", row["website"], "engine:", row["site_engine"])
        driver.get("https://yandex.ru/maps/?text=%D0%9A%D0%B0%D0%B7%D0%B0%D0%BD%D1%8C%20%D0%BC%D0%B0%D0%BD%D0%B8%D0%BA%D1%8E%D1%80")
        from selenium.webdriver.support.ui import WebDriverWait
        WebDriverWait(driver, 20).until(lambda d: len(visible_links(d)) > 0)
        print("search links:", len(visible_links(driver)))
    except Exception:
        print("url:", driver.current_url)
        print("title:", driver.title.encode("unicode_escape").decode())
        print("body:", driver.find_element("tag name", "body").text[:350].encode("unicode_escape").decode())
        raise
finally:
    driver.quit()
