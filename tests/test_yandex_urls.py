import unittest

from app.yandex_urls import normalize_yandex_url


class YandexUrlTests(unittest.TestCase):
    def test_normalizes_all_supported_com_hosts(self):
        cases = {
            "https://yandex.com/maps/org/name/123/": "https://yandex.ru/maps/org/name/123/",
            "https://www.yandex.com/maps/org/name/123/?a=1": "https://yandex.ru/maps/org/name/123/?a=1",
            "https://maps.yandex.com/org/name/123/": "https://yandex.ru/maps/org/name/123/",
            "https://yandex.com/maps/?text=test": "https://yandex.ru/maps/?text=test",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(normalize_yandex_url(source), expected)

    def test_does_not_rewrite_cdn_or_unrelated_hosts(self):
        for url in ("https://avatars.mds.yandex.net/get-altay/1", "https://yastatic.net/a.js",
                    "https://example.com/yandex.com/maps/"):
            with self.subTest(url=url):
                self.assertEqual(normalize_yandex_url(url), url)


if __name__ == "__main__":
    unittest.main()
