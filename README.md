# Maktab LMS: joylashuv va AI tutor overview (backend)

Ota-onalar uchun ikki asosiy API va AI tutor webhook'i uchun minimal backend.

- `POST /v1/location-updates`: bolaning qurilmasidan koordinata qabul qiladi.
- `GET /v1/parent/children/overview`: ota-onaning bolalari, oxirgi koordinata va bugungi tutor vaqti.
- `POST /v1/webhooks/ai-tutor/sessions`: AI tutor provayderidan sessiya natijasi (qo'shimcha).

**Stack:** Python 3.14, Django 6.1, Django REST Framework, drf-spectacular (OpenAPI/Swagger), PostgreSQL 18, Docker Compose.

## Ishga tushirish

### Docker (tavsiya etiladi)

```bash
cp .env.example .env          # productionda secretlarni almashtiring
docker compose up -d --build  # db + web (gunicorn) + partitions
docker compose exec web python manage.py test
```

API `http://localhost:8000` da ochiladi, Swagger UI esa `http://localhost:8000/api/docs/` da (port band bo'lsa, `.env` dagi `APP_PORT` ni o'zgartiring).
`web` ishga tushishda migratsiyalarni va partitionlarni avtomatik yaratadi. `partitions` servisi
har soatda yangi kunlik partitionlarni yaratadi va 7 kundan eskilarini o'chiradi.

### Lokal

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
createdb lms_testtask
.venv/bin/python manage.py migrate
.venv/bin/python manage.py rotate_location_partitions   # productionda: kunlik cron
.venv/bin/python manage.py test
.venv/bin/python manage.py runserver
```

Baza sozlamalari `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` env o'zgaruvchilaridan olinadi.

## Loyiha tuzilishi

| Fayl | Mazmuni |
|---|---|
| `tracking/models.py` | Jadvallar, constraintlar, indekslar |
| `tracking/migrations/0002_location_updates.py` | Partition qilingan tarix jadvali (toza SQL) |
| `tracking/services.py` | Biznes mantiq va barcha SQL: koordinata, overview, webhook |
| `tracking/views.py` | Endpointlar va kirish validatsiyasi |
| `tracking/auth.py` | Auth qatlami (stub) va webhook HMAC tekshiruvi |
| `tracking/partitions.py` | Kunlik partitionlar va 7 kunlik saqlash |
| `tracking/schema.py` | Auth sxemalarining OpenAPI tavsifi |
| `tracking/tests.py` | 19 ta test, haqiqiy PostgreSQL ustida |
| `docs/schema.sql` | Migratsiyalar yaratgan haqiqiy DDL (`pg_dump`) |
| `docs/openapi.yaml` | OpenAPI 3 spetsifikatsiyasi (generatsiya qilingan) |

## API

### Swagger / OpenAPI

| URL | Mazmuni |
|---|---|
| `/api/docs/` | Swagger UI: so'rovlarni brauzerdan yuborib ko'rish mumkin |
| `/api/schema/` | OpenAPI 3 sxemasi (YAML) |
| [`docs/openapi.yaml`](docs/openapi.yaml) | Repodagi nusxasi, Postman yoki Insomnia'ga import qilish uchun |

Swagger'da **Authorize** tugmasi orqali `DeviceAuth` (`X-Device-Id`) yoki `ParentAuth`
(`X-Parent-Id`) kiritiladi; har bir endpointda faqat u qabul qiladigan auth turi ko'rsatiladi.
Webhook HMAC imzo talab qiladi, shuning uchun uni Swagger'dan to'g'ridan-to'g'ri chaqirib bo'lmaydi
(imzo formati endpoint tavsifida yozilgan). Hujjat sahifalari ochiq, endpointlar esa o'z auth'ini
baribir tekshiradi.

Sxemani qayta generatsiya qilish:

```bash
.venv/bin/python manage.py spectacular --validate --fail-on-warn --file docs/openapi.yaml
```

Autentifikatsiya: shartga ko'ra tayyor auth qatlami bor deb olingan. `tracking/auth.py` dagi stub
gateway tekshirgan identifikatorni `X-Device-Id` yoki `X-Parent-Id` headeridan oladi. Qolgan kod
faqat `request.user` (Principal) ga tayanadi, headerlarga emas, shuning uchun haqiqiy JWT/session
qatlamiga almashtirish faqat shu faylga tegadi.

### `POST /v1/location-updates`

```http
POST /v1/location-updates
X-Device-Id: 2
Content-Type: application/json

{"latitude": 41.311, "longitude": 69.279, "recorded_at": "2026-10-14T05:40:53Z"}
```

```json
{"stored": true, "is_latest": true}
```

| Holat | Javob |
|---|---|
| Yangi nuqta | `200 {"stored": true, "is_latest": true}` |
| Eski nuqta (offline'dan keyin kelgan) | `200 {"stored": true, "is_latest": false}`: tarixga yoziladi, oxirgi joylashuv o'zgarmaydi |
| Takroriy yuborish | `200 {"stored": false, "is_latest": false}` |
| Kelajakdagi vaqt (> 5 daqiqa) yoki 7 kundan eski | `400` |
| Noma'lum yoki o'chirilgan qurilma | `403` |
| Ota-ona tokeni bilan | `403` |

Takror va eski nuqtalarga ham `200` qaytariladi: aks holda telefon bir xil nuqtani cheksiz qayta yuboraveradi.

### `GET /v1/parent/children/overview`

```json
{
  "children": [
    {
      "child_id": 3,
      "full_name": "Ali",
      "last_location": {"latitude": 41.31, "longitude": 69.28, "recorded_at": "2026-10-14T05:40:53Z"},
      "tutor_seconds_today": 1800
    },
    {"child_id": 4, "full_name": "Vali", "last_location": null, "tutor_seconds_today": 0}
  ]
}
```

### `POST /v1/webhooks/ai-tutor/sessions`

```json
{"external_id": "sess-123", "child_id": 3, "status": "completed",
 "started_at": "2026-10-14T03:40:00Z", "ended_at": "2026-10-14T04:10:00Z"}
```

Headerlar: `X-Tutor-Timestamp` (unix soniya) va `X-Tutor-Signature` =
`hex(HMAC-SHA256(secret, "<timestamp>.<raw body>"))`. Javob: `{"applied": true|false}`.

## Database sxemasi

```
parents ──< parent_children >── children ──< devices
                                   │
                                   ├── child_last_location   (1:1, bolaga bitta qator)
                                   ├──< location_updates     (7 kunlik tarix, kunlik partition)
                                   └──< tutor_sessions
```

| Jadval | Muhim constraint va indekslar | Nima uchun |
|---|---|---|
| `parent_children` | `UNIQUE (parent_id, child_id)`, `INDEX (child_id)` | M:N: bir ota-onaning bir nechta bolasi, bolaning ikki ota-onasi. Unique indeks overview'dagi "ota-onaning bolalari" so'roviga ham xizmat qiladi, shuning uchun `parent_id` ga alohida indeks qo'yilmagan. Kirish huquqining yagona manbai shu jadval. |
| `devices` | `UNIQUE (child_id) WHERE is_active` | Bolada bir vaqtda bitta aktiv qurilma; eskilari tarix uchun qoladi. |
| `child_last_location` | `PK (child_id)`, lat/lng `CHECK` | Overview tarixni skanerlamaydi, har bola uchun PK bo'yicha bitta qator o'qiydi. |
| `location_updates` | `PK (child_id, recorded_at)`, `PARTITION BY RANGE (recorded_at)` | PK bir vaqtning o'zida takrorlarga qarshi kalit va bolaning trekini vaqt bo'yicha o'qish indeksi. Bola bir lahzada ikki joyda bo'lolmaydi, shuning uchun bu tabiiy kalit. FK yo'q: eng issiq yozuv yo'li. |
| `tutor_sessions` | `UNIQUE (external_id)`, `CHECK (ended_at > started_at)`, `CHECK (completed ⇒ ended_at NOT NULL)`, `INDEX (child_id, ended_at) WHERE status = 'completed'` | Webhook idempotentligi; partial indeks aynan overview so'rovini qoplaydi (index-only scan, `EXPLAIN` bilan tekshirilgan). |

To'liq DDL: [`docs/schema.sql`](docs/schema.sql).

## Asosiy texnik qarorlar

### 1. Eski koordinata yangisini almashtirmaydi

Tarixga yozish va oxirgi joylashuvni yangilash bitta tranzaksiyada bajariladi:

```sql
INSERT INTO location_updates (...) VALUES (...)
ON CONFLICT (child_id, recorded_at) DO NOTHING;            -- takror: tarixga ikki marta yozilmaydi

INSERT INTO child_last_location (...) VALUES (...)
ON CONFLICT (child_id) DO UPDATE SET ...
 WHERE EXCLUDED.recorded_at > child_last_location.recorded_at;   -- eski nuqta: yangilamaydi
```

Taqqoslash va yozish bitta statement ichida, qator locki ostida bajariladi. Shu sababli
`SELECT` → tekshirish → `UPDATE` dagi race condition yo'q: parallel kelgan so'rovlar qaysi
tartibda bajarilmasin, eng katta `recorded_at` g'olib bo'ladi. Buni
`ConcurrentLocationUpdateTests` haqiqiy parallel tranzaksiyalar bilan tekshiradi.

"Yangiroq" degani qurilmaning `recorded_at` vaqti, serverga kelgan vaqt emas: offline'dan
keyin kelgan nuqta serverga kech yetadi, lekin joylashuv sifatida eski.

### 2. Qurilma soati xatosidan himoya

`recorded_at` kelajakda 5 daqiqadan ko'p bo'lsa, rad etiladi. Aks holda soati noto'g'ri
(masalan, 2030-yil) bitta qurilma oxirgi joylashuvni abadiy "qotirib" qo'yadi, chunki keyingi
barcha to'g'ri nuqtalar "eski" bo'lib qoladi. 7 kundan eski nuqta ham qabul qilinmaydi (saqlash muddatidan tashqarida).

### 3. Ruxsatlar

- Koordinata: `child_id` so'rov tanasidan olinmaydi, autentifikatsiyadan kelgan `device_id`
  orqali `devices` jadvalidan topiladi. Qurilma boshqa bolaga koordinata yubora olmaydi;
  o'chirilgan qurilma `403` oladi.
- Overview: `parent_id` faqat auth kontekstidan olinadi, URL yoki query parametr yo'q (IDOR'ning
  oldi olinadi). So'rov `parent_children` dan boshlanadi, shuning uchun bog'lanmagan bola
  natijaga umuman tusha olmaydi.
- Webhook: HMAC imzo (`hmac.compare_digest`) va 5 daqiqalik timestamp oynasi (replay'dan himoya).

### 4. 7 kunlik saqlash

`location_updates` kunlik partitionlarga bo'lingan; eskisi `DROP TABLE` bilan bir zumda
o'chiriladi. Kuniga ~115 mln qatorda `DELETE` jadvalni bloat qiladi, vacuum'ga og'ir yuk beradi
va insertlar bilan raqobatlashadi. Partitionlar 2 kun oldinga yaratiladi va servis har soatda
ishlaydi, shuning uchun bir-ikki marta ishlamay qolishi ham xavfsiz.

### 5. "Bugungi" tutor vaqti

```sql
SUM(LEAST(ended_at, day_end) - GREATEST(started_at, day_start))
WHERE status = 'completed' AND ended_at > day_start AND started_at < day_end
```

- `day_start` / `day_end`: `Asia/Tashkent` bo'yicha yarim tunlar, PostgreSQL'da hisoblanadi
  (`date_trunc('day', now AT TIME ZONE tz) AT TIME ZONE tz`).
- Yarim tundan o'tgan sessiya kun chegarasiga qirqiladi: 23:30–00:20 sessiyadan bugunga 20 daqiqa tushadi.
- Faqat `completed` sessiyalar hisoblanadi.
- Toshkentda soat 02:00 bo'lganda UTC bo'yicha hali kechagi kun; test aynan shu holatni tekshiradi.

### 6. Overview SQL

```sql
WITH bounds AS (
    SELECT d.day AT TIME ZONE %(tz)s                      AS day_start,
           (d.day + interval '1 day') AT TIME ZONE %(tz)s AS day_end
      FROM (SELECT date_trunc('day', %(now)s AT TIME ZONE %(tz)s) AS day) d
)
SELECT c.id, c.full_name, l.latitude, l.longitude, l.recorded_at,
       COALESCE(t.seconds, 0)::bigint AS tutor_seconds_today
  FROM parent_children pc
  JOIN children c ON c.id = pc.child_id
 CROSS JOIN bounds b
  LEFT JOIN child_last_location l ON l.child_id = c.id
  LEFT JOIN LATERAL (
           SELECT SUM(EXTRACT(EPOCH FROM
                      LEAST(s.ended_at, b.day_end) - GREATEST(s.started_at, b.day_start))) AS seconds
             FROM tutor_sessions s
            WHERE s.child_id = c.id
              AND s.status = 'completed'
              AND s.ended_at > b.day_start
              AND s.started_at < b.day_end
       ) t ON TRUE
 WHERE pc.parent_id = %(parent_id)s
 ORDER BY c.id;
```

`LEFT JOIN` va `COALESCE` sababli koordinatasi yoki sessiyasi yo'q bola ham chiqadi. `now`
parametr qilib berilgan, shuning uchun vaqtga bog'liq mantiqni testda aniq sanada tekshirish mumkin.

### 7. AI tutor webhook

- `ON CONFLICT (external_id) DO UPDATE`: takroriy event o'sha qatorni yangilaydi, yangi qator yaratmaydi.
- `completed` yakuniy holat: `WHERE tutor_sessions.status <> 'completed'` sababli keyin kelgan
  takror yoki kechikkan `in_progress` sessiyani qayta ochmaydi va hisoblangan vaqtni o'zgartirmaydi.
- `AND tutor_sessions.child_id = EXCLUDED.child_id`: event sessiyani boshqa bolaga ko'chira olmaydi.
- Takrorga ham `200 {"applied": false}`, aks holda provayder cheksiz qayta yuboradi.

## Testlar

```bash
docker compose exec web python manage.py test     # yoki: .venv/bin/python manage.py test
```

Hammasi haqiqiy PostgreSQL ustida ishlaydi (SQLite'da `ON CONFLICT ... WHERE`, partition va timezone mantiqi boshqacha).

| Guruh | Nimani tekshiradi |
|---|---|
| `LocationUpdateTests` | eski koordinata, takroriy update, kelajakdagi vaqt, o'chirilgan/noma'lum qurilma, ota-ona tokeni bilan yuborish |
| `ConcurrentLocationUpdateTests` | 16 ta parallel tranzaksiya: eng yangi nuqta g'olib; bir xil nuqta aniq bir marta yoziladi |
| `OverviewTests` | faqat o'z bolalari, umumiy bola ikkala ota-onada, ma'lumoti yo'q bola, 401/403, yarim tundan o'tgan sessiya, Toshkent va UTC farqi |
| `TutorWebhookTests` | takroriy event, kechikkan `in_progress`, bolani almashtirishga urinish, noma'lum bola, noto'g'ri imzo, eski timestamp |
| `ApiDocsTests` | Swagger UI va sxema autentifikatsiyasiz ochiladi |

## Muhokama savollari

### 20 000 qurilma har 15 soniyada: yuk va saqlash

| Ko'rsatkich | Hisob |
|---|---|
| So'rovlar | 20 000 / 15 ≈ **1 333 req/s** (o'rtacha; tanaffus va maktabdan chiqish vaqtida pik 2–3 baravar) |
| Kuniga qatorlar | 1 333 × 86 400 ≈ **115 mln** |
| 7 kunga | ≈ **806 mln** qator |
| Bir qator | nazariy: tuple header 24 B + 6 × 8 B ma'lumot + item pointer + PK indeks yozuvi ≈ 110 B; **o'lchangan: 127 B** (2 mln qator, jadval + indeks) |
| Hajm | 115 mln × 127 B ≈ **14.6 GB/kun**, 7 kun ≈ **102 GB** (+1 kunlik partition zaxirasi bilan ≈ 117 GB); WAL va replikalar bunga qo'shimcha |
| Trafik | 1 333 × ~300 B ≈ 0.4 MB/s, muammo emas |
| `child_last_location` | atigi 20 000 qator, lekin sekundiga 1 333 marta yangilanadi |

Qator hajmi taxmin emas: 2 mln qator yozib, `pg_total_relation_size` bilan o'lchangan (pastdagi "O'lchangan natijalar" bo'limiga qarang).

Xulosa: bitta yaxshi PostgreSQL instansi buni ko'taradi, lekin quyidagilar kerak bo'ladi:
- Doimiy DB ulanishlari (`CONN_MAX_AGE`, sozlangan): ularsiz har bir so'rov yangi Postgres ulanishi ochadi va o'tkazuvchanlik 15 baravar past bo'ladi (o'lchangan).
- PgBouncer (transaction pooling): gunicorn workerlari ko'payganda ulanishlar soni cheklanadi.
- `child_last_location` uchun `fillfactor = 70`: yangilanadigan ustunlar indekslanmagan, shuning
  uchun HOT update ishlaydi va bloat kamayadi.
- Kerak bo'lsa, telefon nuqtalarni 1 daqiqalik batch qilib yuboradi (so'rovlar 4 baravar kamayadi, offline sinxronlash ham shu yo'l bilan).

### O'lchangan natijalar

Laptop (Apple Silicon), PostgreSQL 18. Baza: 20 000 bola, 15 000 ota-ona, 400 000 tutor sessiya,
2 mln koordinata tarixi.

| Nima | Natija |
|---|---|
| Overview so'rovi (`EXPLAIN ANALYZE`, disk'dan o'qish bilan) | **1.5 ms** bajarilish; hamma qadam index scan, `location_updates` ga umuman tegilmaydi, shuning uchun tarix hajmiga bog'liq emas |
| `POST /v1/location-updates`, gunicorn 4 worker, 16 parallel ulanish, `CONN_MAX_AGE=0` | 172 req/s, o'rtacha 93 ms |
| Xuddi shu, `CONN_MAX_AGE=60` | **2 579 req/s**, o'rtacha 6 ms, p99 13 ms |

Benchmark'da bir xil nuqta qayta-qayta yuborilgan (conflict yo'li); yangi qatorni haqiqatan yozish
biroz qimmatroq. Natijalar bitta mashinadagi taxminiy ko'rsatkich, production sig'imini rejalash
uchun emas.

Overview'da planning vaqti (≈3 ms) bajarilishdan (1.5 ms) uzoqroq: yuk oshsa, prepared statement'lar
(psycopg server-side prepare) buni olib tashlaydi.

### Overview sekinlashsa

**O'lchash:**
1. Endpoint bo'yicha p50/p95/p99 latency (APM: Sentry, Datadog, OpenTelemetry).
2. Vaqt qayerga ketyapti: DB, connection pool kutish yoki serializatsiya.
3. `pg_stat_statements`: so'rovning o'rtacha va maksimal vaqti, chaqiruvlar soni.
4. `EXPLAIN (ANALYZE, BUFFERS)`: indeks ishlatilyaptimi, nechta sahifa o'qilyapti.
5. Bloat va autovacuum holati (`pg_stat_user_tables`), ayniqsa `child_last_location` da.

**Optimallashtirish (oddiydan murakkabga):**
1. Indekslar allaqachon so'rovga moslangan: `parent_children` unique indeksi,
   `child_last_location` PK, tutor uchun partial index-only scan.
2. Overview'ni read replica'dan o'qish: bir necha soniyalik lag joylashuv uchun qabul qilinadi.
3. Ota-ona bo'yicha qisqa TTL'li kesh (Redis, 5–10 s): koordinata baribir 15 soniyada yangilanadi.
4. Webhook paytida `tutor_daily_totals (child_id, day)` jadvalini yangilash (yarim tundan o'tgan
   sessiya ikki kunga bo'linadi). Overview esa SUM o'rniga bitta qator o'qiydi.

### Parallel so'rovlar, takrorlar va yuk o'sishi

- **Parallellik:** to'g'rilik ilova darajasidagi lock'ga emas, PostgreSQL'ning atomar
  `INSERT ... ON CONFLICT ... WHERE` iga tayanadi. Test bilan isbotlangan.
- **Takrorlar:** tarixda tabiiy kalit `(child_id, recorded_at)`, webhook'da `external_id`.
  Ikkalasi ham idempotent, javob har doim `200`.
- **Yuk o'sishi:**
  1. Stateless web servislar gorizontal masshtablanadi.
  2. PgBouncer, keyin ko'p qatorli insert bilan batch endpoint.
  3. Qurilma bo'yicha rate limit (masalan, 5 soniyada 1 marta) va `429 Retry-After` bilan backpressure.
  4. Juda katta yukda: API → navbat (Kafka yoki Redis Streams) → batch bilan yozadigan worker.
     API tezda `202` qaytaradi, DB'ga yozish tekis taqsimlanadi.
  5. Tarixni TimescaleDB yoki ClickHouse'ga ko'chirish; `child_last_location` PostgreSQL'da qoladi.

### AI tutor webhook kechiksa yoki ikki marta kelsa

Amalga oshirilgan (`TutorWebhookTests` bilan):
- HMAC imzo va timestamp tekshiruvi.
- `external_id` bo'yicha idempotent upsert.
- `completed` yakuniy holat: kechikkan `in_progress` uni bekor qilmaydi.
- Event sessiyani boshqa bolaga ko'chira olmaydi.
- Takrorga ham `200`.

Productionda qo'shiladi:
- `webhook_events` inbox jadvali (`event_id UNIQUE`, raw payload): audit, debugging, qayta ishlash.
- Tez `200` qaytarib, og'ir ishni (bildirishnoma, statistika) fon worker'iga berish.
- Yo'qolgan webhook'lar uchun provayder API'si orqali davriy reconciliation.

### AI yozgan kodning to'g'riligini qanday tekshiraman

1. **Haqiqiy DB ustida test**, ayniqsa chegaraviy holatlar: yarim tun, UTC va Toshkent farqi,
   parallel tranzaksiyalar.
2. **Testlar haqiqatan xato topdi.** Webhook'da noma'lum bola uchun dastlab `IntegrityError`
   ushlanayotgan edi. Test bu kod ishlamasligini ko'rsatdi: Django FK'larni `DEFERRABLE INITIALLY
   DEFERRED` qilib yaratadi, xato `COMMIT` da chiqadi va `except` ga tushmaydi. Productionda bu 500
   bo'lardi. Endi bola oldindan tekshiriladi.
3. **Haqiqiy sxemani ko'rib chiqish.** `pg_dump --schema-only` ikkita ortiqcha indeksni ko'rsatdi
   (Django avtomatik qo'shgan `parent_id` va `external_id ... varchar_pattern_ops`); ular
   `0003` migratsiyada olib tashlandi.
4. **`EXPLAIN`** bilan indeks haqiqatan ishlatilishini tekshirish.
5. **Har bir SQL va constraintni o'zim qatorma-qator o'qib**, "nima uchun" degan savolga javob bera olish.

## Cheklovlar va productionda qo'shiladiganlar

- `tracking/auth.py`: auth stub, haqiqiy JWT/session qatlamiga almashtiriladi.
- Bitta update 3 ta so'rov qiladi (qurilma, tarix, oxirgi joylashuv). Yuk oshsa, bitta CTE
  so'roviga yig'iladi yoki qurilma → bola bog'lanishi keshlanadi.
- Partitionlar `partitions` servisiga bog'liq: ishlamay qolsa 2 kundan keyin insertlar to'xtaydi, monitoring kerak.
- Joylashuv tarixini o'qish endpointi (trek) talab qilinmagan; PK indeksi unga tayyor.
