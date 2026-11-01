"""Create synthetic desktop tour data in an explicitly isolated DATA_DIR."""
from __future__ import annotations
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
target = (root / '.build-tmp' / 'ui-tour-data').resolve()
if Path(os.environ.get('DATA_DIR', target)).resolve() != target:
    raise SystemExit('DATA_DIR must be .build-tmp/ui-tour-data')
os.environ['DATA_DIR'] = str(target)
from app import db

path = target / 'app.sqlite3'
if path.exists():
    raise SystemExit(f'Refusing to overwrite existing demo database: {path}')
target.mkdir(parents=True, exist_ok=True)
db.init_desktop_db(path)
niches = ['Кофейни', 'Салоны красоты', 'Автосервисы', 'Стоматологии', 'Цветы',
          'Фитнес', 'Рестораны', 'Ателье', 'Пекарни', 'Книжные магазины']
cities = ['Казань', 'Москва', 'Самара', 'Пермь', 'Уфа']
count = 0
for ni, niche in enumerate(niches):
    for ci, city in enumerate(cities[:1 + ni % 5]):
        jobs = db.create_desktop_parse_jobs({'niche': niche, 'country': 'Россия', 'types': []}, [city], path=path)
        run = jobs[0]
        for bi in range(5 + (ni * 7 + ci * 3) % 36):
            business_id = f'demo-{ni:02d}-{ci:02d}-{bi:02d}'
            data = {'id': business_id, 'name': f'{niche} · пример {bi + 1}', 'category': niche,
                    'phones': [f'+7 900 100 {ni:02d} {bi:02d}'], 'socials': [],
                    'website': f'https://example.org/{business_id}', 'site_type': 'own_site',
                    'rating': 3.5 + (bi % 15) / 10, 'reviews': (bi * 11) % 120,
                    'address': f'{city}, Тестовая улица, {bi + 1}',
                    'map_url': f'https://yandex.ru/maps/org/{business_id}/100000{ni:02d}{ci:02d}{bi:02d}/'}
            db.upsert_business(data, (niche, 'Россия', city), run['id'], path=path,
                               parse_run_id=run['run_id'])
            count += 1
        db.finish_parse_run(run['run_id'], 'done', path=path)
        db.update_job(run['id'], status='done', path=path)
(target / 'first_run_complete').write_text('demo', encoding='utf-8')
print(f'{count} synthetic organizations, {len(niches)} niches, {path}')
