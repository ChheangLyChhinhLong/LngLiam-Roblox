# PU_MMO169_Sale_Mod — Telegram Digital Key Shop

Project នេះជា Telegram TopUp shop ដែលមាន Roblox (Robux Send/Code), wallet, order history និងវីដេអូ How to Buy។ អ្នកប្រើអាចបញ្ចូលសមតុល្យតាម KHPAY KHQR ហើយបង់ទំនិញដោយ wallet ឬ KHPAY checkout។ Bot ផ្ញើ digital goods បន្ទាប់ពីការទូទាត់ត្រូវបានផ្ទៀងផ្ទាត់។ រូបភាព/វីដេអូ Welcome និង Tutorial ត្រូវបាន upload ទៅ Cloudinary ហើយ URL ត្រូវបានរក្សាទុកក្នុង Supabase។ Backend ដំណើរការលើ Render និងមាន health endpoint `/health`។

## មាតិកា

- [តម្រូវការមុនចាប់ផ្តើម](#តម្រូវការមុនចាប់ផ្តើម)
- [រៀបចំ Telegram Bot](#1-រៀបចំ-telegram-bot)
- [រៀបចំ Supabase](#2-រៀបចំ-supabase)
- [កំណត់ Environment Variables](#3-កំណត់-environment-variables)
- [ដំណើរការលើម៉ាស៊ីនផ្ទាល់ខ្លួន](#4-ដំណើរការលើម៉ាស៊ីនផ្ទាល់ខ្លួន)
- [បង្កើត Catalog, Package និង Stock](#5-បង្កើត-catalog-package-និង-stock)
- [កំណត់វីដេអូណែនាំ និងរូបភាព](#6-កំណត់វីដេអូណែនាំ-និងរូបភាព)
- [ដាក់ពី GitHub ទៅ Render](#7-ដាក់ពី-github-ទៅ-render)
- [ភ្ជាប់ KHPAY Webhook](#8-ភ្ជាប់-khpay-webhook)
- [ត្រួតពិនិត្យ Render ដោយ UptimeRobot](#9-ត្រួតពិនិត្យ-render-ដោយ-uptimerobot)
- [របៀបប្រើក្នុង Telegram](#របៀបប្រើក្នុង-telegram)
- [សុវត្ថិភាព និងថែទាំ](#សុវត្ថិភាព-និងថែទាំ)
- [ដោះស្រាយបញ្ហា](#ដោះស្រាយបញ្ហា)

## តម្រូវការមុនចាប់ផ្តើម

- Python និង PowerShell សម្រាប់ការដំឡើង/សាកល្បងក្នុង Windows។
- Python 3.13 (តាម `.python-version`; បញ្ជាក់ interpreter ជាក់លាក់ពេលបង្កើត virtual environment)។
- Telegram account និង bot token ពី [@BotFather](https://t.me/BotFather)។
- Supabase project, KHPAY API key និង Cloudinary account (ត្រូវការ Cloudinary ប្រសិនបើប្រើរូបភាព ឬវីដេអូផលិតផល)។
- GitHub repository ដែល Render អាចចូលប្រើបាន ប្រសិនបើ deploy តាម Render។

## 1. រៀបចំ Telegram Bot

1. បើក [@BotFather](https://t.me/BotFather) ហើយផ្ញើ `/newbot`។
2. ជ្រើសឈ្មោះ និង username សម្រាប់ bot រួចរក្សាទុក token ដែល BotFather ផ្តល់ឲ្យ។
3. រកលេខ Telegram user ID របស់អ្នក ដើម្បីដាក់ក្នុង `TELEGRAM_ADMIN_IDS`។ ដាក់ជា ID លេខសុទ្ធ មិនមែន username; អាចដាក់ admin ច្រើន ដូចជា 2 នាក់ ដោយបំបែកជាមួយសញ្ញាក្បៀស៖ `123456789,987654321`។
4. កុំផ្ញើ bot token ទៅអ្នកដទៃ និងកុំដាក់វាក្នុង source control។

## 2. រៀបចំ Supabase

1. បង្កើត project ថ្មីនៅ [Supabase](https://supabase.com/) ហើយរង់ចាំ database រួចរាល់។
2. បើក **SQL Editor → New query**។ បើក [schema.sql](schema.sql), ចម្លង SQL ទាំងមូលទៅក្នុង editor ហើយចុច **Run**។ Script នេះបង្កើត tables, indexes និង database functions ដែល bot ត្រូវការ។
3. បើមាន database ចាស់ អាច run `schema.sql` ម្តងទៀត ដើម្បីអនុវត្តការបន្ថែម schema ដែល idempotent។ វាបន្ថែម `products.video_url`, តម្លៃ Reseller, តារាង `resellers`, wallet/deposit tables, scheduled broadcast jobs និង functions សម្រាប់បញ្ចូល/ប្រើសមតុល្យ។ វាក៏បង្កើត Catalog `Roblox (Robux Send)` និង `Roblox (Robux Code)` ប្រសិនបើមិនទាន់មាន។ Package ចាស់នឹងមានតម្លៃ Reseller ស្មើតម្លៃអតិថិជន រហូតដល់អ្នកកែវា។ ត្រូវ run schema ចុងក្រោយមុនប្រើមុខងារទាំងនេះ។
4. បើក **Project Settings → API** រួចចម្លង Project URL និង `service_role` key ទៅ environment variables។ ប្រើ `service_role` key តែក្នុង backend/Render ប៉ុណ្ណោះ។
5. ទុក Row Level Security (RLS) ឲ្យបើក។ កុំបង្កើត public policies សម្រាប់ stock ឬ orders។

> `service_role` key អាចមើល/កែទិន្នន័យទាំងមូលបាន។ កុំដាក់វាក្នុង Telegram, browser, public repository ឬ screenshot។

## 3. កំណត់ Environment Variables

នៅក្នុង project root ចម្លង `.env.example` ទៅ `.env`។ ក្នុង PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
```

បំពេញតម្លៃខាងក្រោម។ កុំដាក់ quote លើតម្លៃ លុះត្រាតែតម្លៃនោះត្រូវការវាពិតៗ។

| Variable | ត្រូវការ | អត្ថន័យ |
| --- | --- | --- |
| `BOT_TOKEN` | ចាំបាច់ | Token ពី BotFather។ |
| `TELEGRAM_ADMIN_IDS` | ណែនាំ | Telegram user IDs របស់ admin បំបែកដោយសញ្ញាក្បៀស។ ឧទាហរណ៍ `123456789,987654321`។ |
| `SUPABASE_URL` | ចាំបាច់ | Project URL ពី Supabase។ |
| `SUPABASE_SERVICE_ROLE_KEY` | ចាំបាច់ | `service_role` API key; រក្សាសម្ងាត់។ |
| `STOCK_ENCRYPTION_KEY` | ចាំបាច់ | Fernet key សម្រាប់ encrypt License Key។ បង្កើតម្តង ហើយរក្សាទុកឲ្យបានល្អ។ |
| `KHPAY_API_KEY` | ចាំបាច់ | KHPAY API key ដែលនៅមានសុពលភាព។ |
| `KHPAY_BASE_URL` | មាន default | ជាទូទៅ `https://khpay.site/api/v1`។ |
| `KHPAY_WEBHOOK_URL` | សម្រាប់ production | URL ពេញលេញទៅ `/webhook/khpay` របស់ service។ |
| `KHPAY_WEBHOOK_SECRET` | សម្រាប់ webhook | Secret ដែល KHPAY បង្កើតសម្រាប់ webhook។ |
| `PUBLIC_BASE_URL` | សម្រាប់ ABA link | URL HTTPS សាធារណៈរបស់ Render ដោយមិនបញ្ចូល path។ |
| `CLOUDINARY_CLOUD_NAME` | ស្រេចចិត្ត | Cloud name; ត្រូវបំពេញជាមួយ API key/secret ប្រសិនបើប្រើរូបភាព ឬវីដេអូ Package។ |
| `CLOUDINARY_API_KEY` | ស្រេចចិត្ត | Cloudinary API key។ |
| `CLOUDINARY_API_SECRET` | ស្រេចចិត្ត | Cloudinary API secret; រក្សាសម្ងាត់។ |
| `STOCK_NOTIFICATION_CHAT_IDS` | ស្រេចចិត្ត | Telegram user ID, Group/Channel chat ID ឬ public `@username` (group/channel) បំបែកដោយក្បៀស។ ប៊ូតុងក្នុងដំណឹងស្តុកនឹងបើក Bot ឯកជននៅ Package ដែលមានស្តុកថ្មី។ |
| `ORDER_NOTIFICATION_CHAT_IDS` | ស្រេចចិត្ត | Group/Channel chat ID ឬ public `@username` (group/channel) សម្រាប់ទទួល Order Log ពេលការទូទាត់ជោគជ័យ និងប្រគល់ Key រួច។ បំបែកច្រើនគោលដៅដោយក្បៀស។ |
| `SUPPORT_USERNAME` | ស្រេចចិត្ត | Telegram username របស់ផ្នែក support ដោយមិនចាំបាច់មាន `@`។ |
| `POLL_INTERVAL_SECONDS` | មាន default | ចន្លោះពេលពិនិត្យ payment background; default `20` វិនាទី។ |
| `PORT` | មាន default | Port របស់ web service; default `10000`។ Render អាចកំណត់ port ផ្ទាល់។ |

បង្កើត encryption key ម្តងដោយប្រើ Python 3.13 ក្នុង virtual environment បន្ទាប់ពីដំឡើង dependencies:

```powershell
.\.venv313\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

ចម្លងលទ្ធផលទៅ `STOCK_ENCRYPTION_KEY`។ កុំបង្កើត key ថ្មីក្រោយមាន stock encrypted រួច ព្រោះ stock ចាស់មិនអាច decrypt ដោយ key ថ្មីបានទេ។ Backup key ដាច់ដោយឡែកពី database។

> កុំ commit `.env`។ ពិនិត្យឲ្យប្រាកដថា `.env` មិនត្រូវបានបញ្ចូលក្នុង Git មុន push។

## 4. ដំណើរការលើម៉ាស៊ីនផ្ទាល់ខ្លួន

បើក PowerShell នៅ project root (`D:\Projects\PU_MMO169_Sale_Mod`)។ ដំឡើង Python 3.13 ជាមុន ហើយពិនិត្យថា Python Launcher រកឃើញវា:

```powershell
py -3.13 -V
py -3.13 -m venv .venv313
.\.venv313\Scripts\python.exe -m pip install --upgrade pip
.\.venv313\Scripts\python.exe -m pip install -r requirements.txt
```

ប្រើ Python executable ផ្ទាល់ដើម្បីជៀសវាងការរត់ interpreter ខុស ពេល virtual environment ផ្សេងកំពុង active។ បើក PowerShell រារាំងការបើក activation script មិនចាំបាច់ activate ក៏បាន; បើចង់ activate `.venv313` សម្រាប់ session នេះ:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv313\Scripts\Activate.ps1
```

ចាប់ផ្តើម bot និង web server:

```powershell
.\.venv313\Scripts\python.exe -m app.main
```

ទុក process នេះឲ្យដំណើរការ ហើយបើក browser ទៅ `http://127.0.0.1:10000/health`។ លទ្ធផលធម្មតាគឺ `{"status":"ok"}`។ បន្ទាប់មកបើក bot ក្នុង Telegram ហើយផ្ញើ `/start`។

Webhook និង ABA deep link ត្រូវការអាសយដ្ឋាន HTTPS ដែល KHPAY/Telegram អាចចូលដល់បាន។ ដូច្នេះការសាកល្បង webhook ក្នុង local machine ត្រូវការប្រើ HTTPS tunnel; បើមិនបានកំណត់ webhook ទេ bot នៅតែអាចពិនិត្យ payment តាម background polling និងប៊ូតុងពិនិត្យ payment។

### រត់ Tests

`pytest` មិនត្រូវបានដំឡើងជាមួយ dependencies សម្រាប់ runtime ទេ។ ដំឡើងវាក្នុង `.venv313` ម្តង រួចរត់ test suite:

```powershell
.\.venv313\Scripts\python.exe -m pip install pytest
.\.venv313\Scripts\python.exe -m pytest -q
```

## 5. បង្កើត Catalog, Package និង Stock

### តាម Admin panel ក្នុង Telegram

1. បន្ថែម Telegram ID របស់អ្នកក្នុង `TELEGRAM_ADMIN_IDS` ហើយចាប់ផ្តើម bot ឡើងវិញ។
2. បើក bot ដោយ admin account ហើយចុច **Store admin → Catalogs and packages → Add Catalog**។
3. ចុច **Add catalog** ហើយបញ្ចូលឈ្មោះ និងព័ត៌មានលម្អិតតាមសំណួរដែល bot បង្ហាញម្តងមួយៗ។ ព័ត៌មានលម្អិតអាចរំលងបាន។

4. បើក Catalog ដែលទើបបង្កើត ហើយចុច **Add Package**។ Wizard នឹងសួរតាមលំដាប់៖ រូបភាព/វីដេអូ, ឈ្មោះ, ព័ត៌មានលម្អិត, តម្លៃអតិថិជន, តម្លៃ Reseller, សារ/របៀបប្រើក្រោយ Order, រូបភាព និងវីដេអូក្រោយ Order។ ចុច **Skip** លើជំហានស្រេចចិត្តដើម្បីរំលង។ ឈ្មោះ និងតម្លៃអតិថិជនត្រូវបញ្ចូល។ ពិនិត្យព័ត៌មាន រួចចុច **Confirm save**។ ពេលកែ Package អាច Skip ដើម្បីទុកតម្លៃដដែល ឬ Clear រូបភាព/វីដេអូក្រោយ Order. ចុច **Cancel** បានគ្រប់ជំហាន។

តម្លៃទាំងពីរត្រូវធំជាងសូន្យ ហើយតម្លៃ Reseller ដែលបញ្ចូលត្រូវទាបជាងតម្លៃអតិថិជន។ Media ជាជម្រើស; បើផ្ញើរូបភាព/វីដេអូ ត្រូវកំណត់ Cloudinary variables ទាំងបី។ Package នឹងបង្ហាញ media នៅលើទំព័រព័ត៌មានរបស់វា។

សារ រូបភាព និងវីដេអូក្រោយ Order ត្រូវបានកំណត់តាម **Edit Package** ហើយ Bot ផ្ញើវាបន្ទាប់ពីទូទាត់ជោគជ័យ មុនពេលផ្ញើ Key។ License Key/Product Code នៅតែបញ្ចូលតាម **Add stock/Import stock (.txt)** សម្រាប់ Package នីមួយៗ; Bot រក្សាទុកជាទម្រង់ encrypted និងផ្ញើតាមចំនួនដែលបានទិញ។

មុនប្រើមុខងារនេះ សូម run `schema.sql` ចុងក្រោយក្នុង Supabase SQL Editor ដើម្បីបន្ថែម delivery fields លើ products។ រូបភាព និងវីដេអូក្រោយ Order ត្រូវបាន upload ទៅ Cloudinary ដោយប្រើ Cloudinary variables ទាំងបីដូច Package media។

Admin អាចបើក **Store admin → Manage resellers → Add reseller** រួចផ្ញើ Telegram user ID ជាលេខ។ ចុចលើ ID ក្នុងបញ្ជីដើម្បីដកសិទ្ធិ។ Reseller ប្រើ bot ដូចអតិថិជនធម្មតា ប៉ុន្តែ catalog, ព័ត៌មាន package និងការកក់ទិញនឹងប្រើតម្លៃ Reseller។ តម្លៃ checkout ត្រូវបានគណនាដោយ Supabase តាម Telegram ID មិនមែនតាមតម្លៃដែលបានផ្ញើពី UI ទេ។

5. ចូល package ហើយប្រើ **Add stock** ដើម្បីបញ្ចូល License Key មួយក្នុងមួយបន្ទាត់។ Bot encrypt ទិន្នន័យមុនរក្សាទុក។
6. ឬប្រើទម្រង់សារនេះពី admin account ដោយផ្ទាល់:

```text
addstock PRODUCT_UUID | key1, key2
```

អាចប្រើបន្ទាត់ថ្មីជំនួស comma បាន។ Key ដែលមានស្រាប់ក្នុង stock `available` ឬ `reserved` នឹងត្រូវរំលង; ប៉ុន្តែបើ Key ដូចគ្នាត្រូវបានលក់រួច អ្នកអាចបញ្ចូលវាវិញជាស្តុកថ្មីបាន។ កុំផ្ញើ stock ទៅ chat សាធារណៈ។

### បញ្ចូល stock ជា `.txt`

ក្នុង Admin panel ជ្រើស **Import stock (.txt)**, ជ្រើស package ហើយផ្ញើឯកសារ `.txt`។ ឯកសារមិនអាចធំជាង 1 MB; ដាក់ License Key មួយក្នុងមួយបន្ទាត់។

### Broadcast

នៅ **Store admin → Broadcast** អាចបង្កើតសារផ្សព្វផ្សាយជាអត្ថបទ, រូបភាព, វីដេអូ, ឯកសារ, audio/voice, GIF/animation, video note, sticker, contact, location, poll និង album បាន។ អាចផ្ញើច្រើនមាតិកាជាប់គ្នា រួចចុច **Preview** ដើម្បីពិនិត្យមាតិកា និងចំនួនអ្នកទទួលមុនពេល Confirm។ ជម្រើស **Add buttons** ទទួល URL មួយក្នុងមួយបន្ទាត់ ដូចជា `បើកហាង | https://example.com`; អាចបញ្ចូលបានអតិបរមា 8 តំណ។

Broadcast ផ្ញើទៅអ្នកប្រើដែលបានចាប់ផ្តើម Bot និង chat IDs ដែលកំណត់នៅ **Settings → Notifications** (Stock និង Order notifications)។ អ្នកទទួលត្រូវបាន deduplicate; អ្នកប្រើដែល block bot ឬ chat ដែល bot មិនមានសិទ្ធិ post នឹងរាប់ជា Failed។ ពេលផ្ញើអាចមើល Sent/Failed/Progress និងចុច **Stop** ដើម្បីបញ្ឈប់។

ចុច **Schedule** ដើម្បីកំណត់ការផ្ញើម្តងតាមម៉ោងភ្នំពេញ ដោយប្រើទម្រង់ `YYYY-MM-DD HH:MM`។ អាចមើល/លុប Schedule នៅ **Scheduled Broadcasts**។ សូម run `schema.sql` ចុងក្រោយ ដើម្បីបង្កើតតារាង `broadcast_jobs`; bot នឹងរក្សា Schedule និងចាប់ផ្តើមផ្ញើក្រោយ restart បាន។ សូមកុំលុបសារដើមដែល Admin ផ្ញើសម្រាប់ draft ដែលបាន Schedule រហូតដល់ការផ្ញើចប់ ឬ Schedule ត្រូវបានលុប។

អាចប្រើ command-line importer ផងដែរ។ ដាក់ License Key មួយក្នុងមួយបន្ទាត់:

```text
FF-1H-KEY-001
FF-1D-KEY-002
```

រក Product UUID នៅក្នុង admin panel រួចរត់ពី project root:

```powershell
.\.venv313\Scripts\python.exe -m scripts.import_stock PRODUCT_UUID .\stock.txt
```

Script អាន `.env` ពី project root។ វាត្រូវការ `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` និង `STOCK_ENCRYPTION_KEY`។ Key ដែលមានស្រាប់ជា `available` ឬ `reserved` នឹងត្រូវរំលង; Key ដែលបានលក់រួចអាច import វិញជាស្តុកថ្មី។ បន្ទាប់ពី import សូមលុប/ផ្លាស់ទីឯកសារ stock ឲ្យមានសុវត្ថិភាព ព្រោះក្នុងនោះមាន License Key ជាអក្សរធម្មតា។

### កែប្រែ និងបិទ Catalog/Package

- **Edit Catalog**: `ឈ្មោះ | ព័ត៌មានលម្អិត`
- **Edit Package**: `ឈ្មោះ | តម្លៃ | ព័ត៌មានលម្អិត`
- **Remove stock**: បញ្ចូល License Key ដើមមួយក្នុងមួយបន្ទាត់។ លុបបានតែ stock ដែលមានស្ថានភាព `available`; stock កំពុង reserve ឬលក់រួចមិនត្រូវបានលុបទេ។
- **Deactivate**: បិទ Catalog នឹងបិទ packages របស់វាទាំងអស់; បិទ Package បិទតែ package នោះ។ វាមិនមែនជា delete ទេ។
- **Sales**: មើលចំនួន paid orders និង revenue។

## 6. កំណត់វីដេអូណែនាំ និងរូបភាព

### វីដេអូ “How to buy”

នៅ **Store admin → Settings → Media → How to Buy** admin អាចផ្ញើវីដេអូសម្រាប់ upload ទៅ Cloudinary ហើយ URL HTTPS ត្រូវបានរក្សាទុកក្នុង `public.bot_settings.how_to_buy_video_url`។ Caption ខ្មែរ/អង់គ្លេសអាចកំណត់នៅ **Settings → Captions**។ ក៏អាចកែ URL/caption ដោយផ្ទាល់តាម Supabase SQL Editor; បើគ្មាន caption វីដេអូនឹងផ្ញើដោយគ្មាន caption។

```sql
insert into public.bot_settings (key, value)
values
    ('how_to_buy_video_url', 'PASTE_CLOUDINARY_HTTPS_VIDEO_URL'),
    ('how_to_buy_caption_km', 'ការណែនាំអំពីរបៀបទិញ'),
    ('how_to_buy_caption_en', 'How to buy tutorial')
on conflict (key) do update set value = excluded.value;
```

Bot ទាញ settings ថ្មីរាល់ពេលអ្នកប្រើបើក tutorial ដូច្នេះមិនចាំបាច់ restart bot បន្ទាប់ពីកែទេ។

នៅ **Store admin → Settings → Media → Welcome media** admin អាចផ្ញើរូបភាព ឬវីដេអូ។ Bot upload ទៅ Cloudinary ហើយរក្សា URL នៅក្នុង Supabase `bot_settings`; media ប្រភេទថ្មីនឹងជំនួស media ចាស់ ហើយមានប៊ូតុង Clear តែមួយសម្រាប់លុប Welcome media ទាំងពីរប្រភេទ។ Settings → **Store links** អាចកំណត់ Support username និង **Notifications** អាចកំណត់ group IDs សម្រាប់ stock/order notifications។

```sql
insert into public.bot_settings (key, value)
values
    ('welcome_video_url', 'PASTE_TELEGRAM_FILE_ID_OR_PUBLIC_VIDEO_URL')
on conflict (key) do update set value = excluded.value;
```

ចាប់ផ្តើមប្រើ bot ដោយផ្ញើ `/start`។ Bot នឹងបង្ហាញ Welcome media និងម៉ឺនុយសេវាកម្ម។

### Wallet និងម៉ឺនុយ

ទំព័រ `/start` បង្ហាញ Welcome media ហើយប៊ូតុងប្រើជា reply keyboard នៅខាងក្រោមប្រអប់សរសេរសារ៖ **Account**, **Add funds**, **Games Service**, **My Orders** និង **How to Buy**។ ជ្រើស Roblox package តាម **Games Service**។ Add funds គាំទ្រចំនួន $1, $5, $10, $15, $20, $50 ឬចំនួនផ្ទាល់ខ្លួនពី $1 ដល់ $500; សមតុល្យត្រូវបានបន្ថែមតែបន្ទាប់ពី KHPAY បញ្ជាក់ transaction និងចំនួនទឹកប្រាក់។ បើសមតុល្យគ្រប់គ្រាន់ ទំព័រ Package ផ្តល់ជម្រើសបង់ដោយ Wallet ផងដែរ។ ដើម្បីប្រើ wallet សូម run `schema.sql` ចុងក្រោយ។

### រូបភាព និងវីដេអូ Package

បំពេញ `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY` និង `CLOUDINARY_API_SECRET`។ ដើម្បីដាក់/ប្តូរ media package ដែលមានស្រាប់ ផ្ញើរូបភាព ឬវីដេអូឲ្យ bot ជាមួយ caption:

```text
setmedia PRODUCT_UUID
```

រក្សាភាពចាស់ដដែលផងដែរ ដោយប្រើ `setphoto PRODUCT_UUID` ជាមួយរូបភាព។ Bot upload រូបភាព/វីដេអូទៅ Cloudinary ហើយរក្សា secure URL ក្នុង `products.image_url` ឬ `products.video_url`។ ត្រូវ run `schema.sql` ឡើងវិញដើម្បីបន្ថែម `video_url` មុនប្រើមុខងារវីដេអូ។

## 7. ដាក់ពី GitHub ទៅ Render

### 7.1 Push code ទៅ GitHub

1. បង្កើត ឬបើក repository [ChheangLyChhinhLong/PU_MMO169_Sale_Mod](https://github.com/ChheangLyChhinhLong/PU_MMO169_Sale_Mod)។ Branch ដែល Render ត្រូវភ្ជាប់គឺ `main`។
2. ពី project root ពិនិត្យការកែប្រែមុន commit និង push:

```powershell
git status
git add .env.example README.md app render.yaml schema.sql scripts tests
git commit -m "Describe the change"
git push origin main
```

បើគ្មានការកែប្រែថ្មី មិនចាំបាច់បង្កើត commit ទេ។ ឯកសារ `.env` មាន secrets ហើយត្រូវបាន Git ignore; កុំប្រើ `git add -f .env` ឬបញ្ចូល token/key ទៅក្នុង GitHub។

### 7.2 បង្កើត Render service ពី Blueprint

1. ចូល [Render Dashboard](https://dashboard.render.com/) ហើយជ្រើស **New → Blueprint**។
2. ភ្ជាប់ GitHub account បើមិនទាន់ភ្ជាប់, ជ្រើស repository `PU_MMO169_Sale_Mod` និង branch `main`។ សូមមើល [Render Blueprints guide](https://render.com/docs/infrastructure-as-code) បើត្រូវការជំនួយក្នុងជំហានភ្ជាប់ repository។
3. Render រកឃើញ `render.yaml` នៅ project root. ពិនិត្យ configuration ហើយចុច **Deploy Blueprint**។ Blueprint កំណត់ Python web service, build/start command, health check `/health` និង `numInstances: 1`។
4. នៅពេល Render ស្នើ environment variables សូមបញ្ចូល secrets ពិតតាមតារាងក្នុងផ្នែកទី 3។ តម្លៃចាំបាច់យ៉ាងហោចណាស់មាន `BOT_TOKEN`, `TELEGRAM_ADMIN_IDS`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `KHPAY_API_KEY` និង `STOCK_ENCRYPTION_KEY`។ កុំប្រើតម្លៃឧទាហរណ៍ក្នុង `.env.example` ជា production secrets។
5. កំណែ Python ត្រូវបានកំណត់ដោយ `.python-version` (`3.13`)។ Render អនុវត្តកំណែនេះ ហើយប្រើ patch release ចុងក្រោយដែលមានសម្រាប់ Python 3.13។ មើល [Render Python version guide](https://render.com/docs/python-version)។
6. បើប្រើ media Package ត្រូវបន្ថែម `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY` និង `CLOUDINARY_API_SECRET` នៅ Render **Environment**។ `PUBLIC_BASE_URL` ក៏ត្រូវបញ្ចូលក្រោយទទួលបាន service URL ដែរ។ អាចទុក `STOCK_NOTIFICATION_CHAT_IDS`, `ORDER_NOTIFICATION_CHAT_IDS` និង `SUPPORT_USERNAME` ទទេ បើមិនប្រើ។
7. រង់ចាំ deploy ជោគជ័យ។ ក្នុង service **Settings/Overview** ចម្លង URL HTTPS ដែល Render ផ្ដល់ឲ្យ (`https://YOUR-SERVICE.onrender.com`)។
8. នៅ Render **Environment** បន្ថែម/កែ:

```text
PUBLIC_BASE_URL=https://YOUR-SERVICE.onrender.com
KHPAY_WEBHOOK_URL=https://YOUR-SERVICE.onrender.com/webhook/khpay
```

ចុច **Save, rebuild, and deploy** (ឬប៊ូតុង deploy ដែល Render បង្ហាញ)។ បន្ទាប់ពី commit/push ទៅ `main` Render នឹង deploy commit ថ្មីដោយស្វ័យប្រវត្តិតាមការកំណត់ auto-deploy។
9. បើក `https://YOUR-SERVICE.onrender.com/health`។ លទ្ធផលដែលរំពឹងទុកគឺ `{"status":"ok"}`. ពិនិត្យ Render **Logs** និងសាកល្បង bot ក្នុង Telegram; `/health` បញ្ជាក់ web service ឆ្លើយតបប៉ុណ្ណោះ មិនធានាថា Supabase, Telegram polling ឬ KHPAY ដំណើរការត្រឹមត្រូវទាំងអស់ទេ។
10. បង្កើត KHPAY webhook តាមផ្នែកទី 8 ហើយរក្សា `KHPAY_WEBHOOK_SECRET` ក្នុង Render **Environment**។ បន្ទាប់ពីប្តូរ secret ត្រូវ redeploy។

កុំបង្កើត Render instances ច្រើនជាងមួយ ខណៈ bot ប្រើ Telegram long polling ព្រោះ instances ច្រើនអាចប៉ះទង្គិចគ្នា។ ក្រោយ deployment លើកដំបូង សូមពិនិត្យ Render Logs ម្តងទៀត ដើម្បីប្រាកដថាមិនមាន database/schema error។

## 8. ភ្ជាប់ KHPAY Webhook

Checkout ប្រើ KHPAY `/qr/generate` ជា USD។ រៀបចំ KHPAY API key, account/product និង payout settings តាមការកំណត់ក្នុង KHPAY account របស់អ្នក។ Webhook ជូនដំណឹងអំពី paid/expired/failed ប៉ុន្តែ backend នៅតែសួរ KHPAY API ដើម្បីផ្ទៀងផ្ទាត់ status និង amount មុនពេលបញ្ជូន stock។

### បង្កើត webhook ថ្មីតាម PowerShell

បើក PowerShell session ថ្មី ហើយកែ `$webhookUrl` ឲ្យត្រូវនឹង URL Render របស់អ្នក។ Script ខាងក្រោមលុប webhook ចាស់ៗដែលមាន URL ដូចគ្នា បង្កើត webhook ថ្មីសម្រាប់ paid/expired/failed events ហើយចម្លង secret ទៅ clipboard ដោយមិន print secret ចេញ។ វាត្រូវការ KHPAY API key ដែលបាន rotate រួច:

```powershell
$secureKey = Read-Host "Rotated KHPAY API key" -AsSecureString
$apiKey = [System.Net.NetworkCredential]::new("", $secureKey).Password
$headers = @{ Authorization = "Bearer $apiKey" }
$webhookUrl = "https://YOUR-SERVICE.onrender.com/webhook/khpay"

$existing = Invoke-RestMethod `
    -Method Get `
    -Uri "https://khpay.site/api/v1/webhooks" `
    -Headers $headers

$matching = @($existing.data | Where-Object { $_.url -eq $webhookUrl })
foreach ($hook in $matching) {
    Invoke-RestMethod `
        -Method Delete `
        -Uri "https://khpay.site/api/v1/webhooks/$($hook.id)" `
        -Headers $headers | Out-Null
}

$body = @{
    url = $webhookUrl
    events = @("payment.paid", "payment.expired", "payment.failed")
} | ConvertTo-Json

$created = Invoke-RestMethod `
    -Method Post `
    -Uri "https://khpay.site/api/v1/webhooks" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body

Set-Clipboard -Value $created.data.secret
Write-Host "Webhook created. Secret copied to clipboard; it was not printed."

$secureKey = $null
$apiKey = $null
$headers = $null
$body = $null
$existing = $null
$matching = $null
$created = $null
```

ចូល KHPAY Dashboard → Webhooks ដើម្បីបញ្ជាក់ថា URL និង events ត្រឹមត្រូវ។ បន្ទាប់មក paste secret ពី clipboard ទៅ Render Environment ជា `KHPAY_WEBHOOK_SECRET` ហើយ redeploy។ កុំដាក់ webhook secret ក្នុង README, GitHub ឬ chat។ បិទ PowerShell session បន្ទាប់ពីរួចរាល់ និងកុំទុក secret នៅ clipboard យូរ។

Webhook endpoint ទទួល `POST /webhook/khpay`, ពិនិត្យ HMAC signature លើ raw request body, សួរ KHPAY ដើម្បីផ្ទៀងផ្ទាត់ status ហើយផ្ទៀងផ្ទាត់ទឹកប្រាក់មុន delivery។ កុំប្រើ browser redirect ជាភស្តុតាងទូទាត់។

## 9. ត្រួតពិនិត្យ Render ដោយ UptimeRobot

បង្កើត monitor បន្ទាប់ពី Render service deploy ជោគជ័យ និង `/health` បង្ហាញ `{"status":"ok"}`។

1. ចូល [UptimeRobot](https://uptimerobot.com/) ហើយបង្កើត monitor ថ្មីប្រភេទ **HTTP(s)**។
2. ដាក់ឈ្មោះ monitor ឧទាហរណ៍ `PU_MMO169_Sale_Mod Render Health`។
3. ដាក់ URL `https://YOUR-SERVICE.onrender.com/health` (ប្រើ URL ពិតរបស់ Render និងបញ្ចូល path `/health`)។
4. ជ្រើស monitoring interval ដែលគណនីរបស់អ្នកអនុញ្ញាត និងកំណត់ email/contact សម្រាប់ alert ពេល monitor មិនឆ្លើយតប។
5. Save ហើយរង់ចាំ check ដំបូង។ ត្រូវបង្ហាញ **Up/Online** និង HTTP 200។ ប្រើ **Test/Send test alert** ប្រសិនបើ dashboard ផ្ដល់ជម្រើសនេះ ដើម្បីផ្ទៀងផ្ទាត់ថាអ្នកទទួលសារ alert។

Monitor នេះពិនិត្យតែ web health route។ វាមិនសាកល្បងការទិញ, Telegram polling, database schema, KHPAY payment ឬ webhook ដោយស្វ័យប្រវត្តិទេ។ បើ monitor បង្ហាញ Down សូមពិនិត្យ Render **Events/Deploys/Logs** និងបើក `/health` ដោយផ្ទាល់។ UptimeRobot ជាការជូនដំណឹងបន្ថែម មិនជំនួសការពិនិត្យ logs និងសាកល្បង flow ទិញពិតទេ។

## របៀបប្រើក្នុង Telegram

1. អ្នកទិញចុច **Games Service**, ជ្រើស Catalog និង Package (ឧ. Roblox Send/Code) ហើយកំណត់ចំនួនទិញ។ ចំនួនមួយ order មានអតិបរមា 3 ហើយមិនអាចលើសស្តុកនៅសល់។
2. ពេលបង្កើត order, Supabase កក់ stock ជាអាតូមិក ដើម្បីកុំឲ្យអតិថិជនពីរនាក់បាន License Key ដូចគ្នា។
3. អ្នកទិញបង់តាម KHQR ឬបើក ABA Mobile។ អាចចុចប៊ូតុងពិនិត្យ payment ដោយខ្លួនឯងបាន។
4. បន្ទាប់ពី KHPAY បញ្ជាក់ `paid` bot ផ្ញើ License Key ជា code block (ចុចលើ Key ដើម្បី copy) និងមានប៊ូតុង **📋 Copy Key** សម្រាប់ Key នីមួយៗ រួចលុបសារដែលមាន QR។ ករណី expired/failed stock ដែលបាន reserve នឹងត្រឡប់ទៅ available។
5. អ្នកប្រើអាចមើល Account/Balance, បញ្ចូលប្រាក់, មើល order history និងចាក់វីដេអូ How to Buy។

Admin panel អាចបន្ថែម/កែ Catalog និង Package, បិទធាតុ, បញ្ចូល/ដក stock, upload `.txt`, Broadcast និងមើល sales summary។ អ្នកប្រើដែលមិនមាន ID ក្នុង `TELEGRAM_ADMIN_IDS` មិនអាចគ្រប់គ្រងហាងបានទេ។

## សុវត្ថិភាព និងថែទាំ

- រក្សា bot token, Supabase service role key, KHPAY API key/webhook secret, Cloudinary API secret និង stock encryption key ជាសម្ងាត់។ ប្រសិនបើសង្ស័យថាបែកធ្លាយ សូម rotate/revoke key នោះភ្លាមៗ ហើយ update secret នៅ Render។
- Backup `STOCK_ENCRYPTION_KEY` នៅកន្លែងសុវត្ថិភាពដាច់ពី database។ បើបាត់ key នេះ License Key ដែលបាន encrypt រួចមិនអាចយកមកប្រើបានទេ។
- កុំបិទ Supabase RLS និងកុំបង្កើត public access policies សម្រាប់ orders/stock។ Backend ប្រើ service-role key ដោយផ្ទាល់។
- `STOCK_NOTIFICATION_CHAT_IDS` និង `ORDER_NOTIFICATION_CHAT_IDS` គាំទ្រទាំង **group និង channel**។ ដាក់ chat ID ជាលេខ (ឧ. `-1001234567890`) សម្រាប់ private group/channel ឬ `@username` សម្រាប់ public group/channel; អាចបញ្ចូលច្រើន ដោយបំបែកជាមួយ comma ឧទាហរណ៍ `-1001234567890,@myshopnews`។ បន្ថែម bot ទៅ group ហើយអនុញ្ញាតឲ្យផ្ញើសារ; សម្រាប់ channel ត្រូវបន្ថែម bot ជា admin ដែលអាច post បាន។
- `STOCK_NOTIFICATION_CHAT_IDS` ក៏អាចដាក់ Telegram user ID ដើម្បីផ្ញើដំណឹងឯកជន (អ្នកប្រើត្រូវចាប់ផ្តើម Bot មុន)។ Bot ក៏ជូនដំណឹងទៅ users ដែលបានប្រើ bot រួច។ ប៊ូតុង **View product / មើល Package** បើក private chat ជាមួយ Bot ហើយបង្ហាញ Package ដែលមានស្តុកថ្មី។
- `ORDER_NOTIFICATION_CHAT_IDS` ទទួល Order Log បន្ទាប់ពីទទួល payment និងប្រគល់ Key ជោគជ័យ។ Log មានអតិថិជន, Telegram ID, Package, ចំនួនទឹកប្រាក់/ប្រភេទតម្លៃ, transaction reference និងម៉ោង Asia/Phnom_Penh។
- License Key ក្នុង database ត្រូវបាន encrypt។ ឯកសារ `.txt` មុន import និងសារ stock ក្នុង Telegram នៅតែជា plaintext ដូច្នេះកុំទុក/បញ្ជូនវាក្នុងកន្លែងមិនមានសុវត្ថិភាព។
- កុំ run bot local និង Render instance ច្រើនក្នុងពេលតែមួយដោយប្រើ bot token ដូចគ្នា ពេល long polling កំពុងដំណើរការ។
- ការបង្កើត payment មាន idempotency key ដោយប្រើ order ID។ ការបញ្ជូន order កើតឡើងតែបន្ទាប់ពី KHPAY API បញ្ជាក់ការទូទាត់ប៉ុណ្ណោះ។

## ដោះស្រាយបញ្ហា

| រោគសញ្ញា | អ្វីដែលត្រូវពិនិត្យ |
| --- | --- |
| Bot មិនឆ្លើយតប | ពិនិត្យ `BOT_TOKEN`, Render Logs និងថាមាន polling process តែមួយប៉ុណ្ណោះ។ |
| Games Service មិនបង្ហាញ Catalog | ពិនិត្យថា bot កំពុងដំណើរការ និងមាន Catalog ដែល `active = true`។ បើមាន error សូមមើល logs សម្រាប់ Supabase schema/table error។ |
| Bot បង្ហាញថាមិនមាន Catalog | ពិនិត្យថាមាន `categories` ដែល `active = true` ក្នុង Supabase។ |
| Admin panel មិនបង្ហាញ | ពិនិត្យ Telegram numeric ID ក្នុង `TELEGRAM_ADMIN_IDS` ហើយ restart/redeploy។ |
| Supabase request បរាជ័យ | ពិនិត្យ `SUPABASE_URL`, service-role key និងថា `schema.sql` បាន run ជោគជ័យ។ កុំបិទ RLS ដើម្បីជៀសបញ្ហា។ |
| `PGRST205` រកមិនឃើញ `user_wallets` ឬ `wallet_deposits` | បើក Supabase SQL Editor ហើយ run `schema.sql` ចុងក្រោយ។ បន្ទាប់មកសាកល្បង Account/Add funds ម្តងទៀត។ Bot នឹងប្រាប់ថា Wallet មិនទាន់រៀបចំ ជំនួសឲ្យបរាជ័យដោយ unhandled exception។ |
| Import stock មិនជោគជ័យ | ពិនិត្យ Product UUID, file UTF-8, format មួយ License Key ក្នុងមួយបន្ទាត់ និង encryption key។ |
| រូបភាព/វីដេអូមិន upload ឬមិនបង្ហាញ | បំពេញ Cloudinary variables ទាំងបី, ពិនិត្យ `CLOUDINARY_CLOUD_NAME`, API key/secret នៅ local/Render និង run schema ចុងក្រោយដែលមាន `products.video_url`។ |
| KHPAY checkout បរាជ័យ | ពិនិត្យ API key, `KHPAY_BASE_URL`, KHPAY account/payout setup និង Render Logs។ |
| Webhook បាន `401` | ពិនិត្យថា `KHPAY_WEBHOOK_SECRET` ត្រូវនឹង secret របស់ webhook ដែលកំពុងសកម្ម ហើយ redeploy បន្ទាប់ពី update។ |
| ABA button មិនដំណើរការ | `PUBLIC_BASE_URL` ត្រូវជា public HTTPS URL របស់ Render ដោយគ្មាន `/webhook/khpay`; ពិនិត្យថា order នៅ pending។ |
| Health check មិនឆ្លើយ | សាកល្បង `/health`, ពិនិត្យ Render Logs និង environment variables ចាំបាច់។ |

## សាកល្បងមុនបើកលក់

បង្កើត Catalog និង Package សាកល្បងដែលមានតម្លៃទាប, បញ្ចូល License Key សាកល្បងមួយ, ទិញពី Telegram account ផ្សេង, បញ្ជាក់ថា payment status ត្រឹមត្រូវ និង Key ត្រូវបានផ្ញើតែម្តង។ បន្ទាប់មកសាកល្បង expired/failed payment, order history, admin stock notification និង webhook logs មុនដាក់លក់ពិត។