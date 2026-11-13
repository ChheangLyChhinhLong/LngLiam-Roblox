# PU_MMO169_Sale_Mod — Telegram Digital Key Shop

Project នេះជា Telegram shop សម្រាប់លក់ Digital Key, Code និងផលិតផល Digital ផ្សេងៗដែល Admin បង្កើតតាម Catalog និង Package ដោយសេរី។ វាមាន wallet, order history និង How to Buy។ អ្នកប្រើអាចបញ្ចូលសមតុល្យតាម KHQRPay ហើយបង់ទំនិញដោយ wallet ឬ ABA KHQR checkout។ Bot ផ្ញើ digital goods បន្ទាប់ពីការទូទាត់ត្រូវបានផ្ទៀងផ្ទាត់ជាមួយ KHQRPay។ Database ប្រើ Supabase; ឯកសារ media ដែល Admin upload ត្រូវបាន upload ទៅ Cloudinary ហើយ Supabase រក្សាទុក URL។ Telegram post links ដែលបាន paste នៅតែអាចប្រើជាតំណដើម។ Backend ដំណើរការលើ Render និងមាន health endpoint `/health`។

## មាតិកា

- [PU\_MMO169\_Sale\_Mod — Telegram Digital Key Shop](#pu_mmo169_sale_mod--telegram-digital-key-shop)
  - [មាតិកា](#មាតិកា)
  - [តម្រូវការមុនចាប់ផ្តើម](#តម្រូវការមុនចាប់ផ្តើម)
  - [1. រៀបចំ Telegram Bot](#1-រៀបចំ-telegram-bot)
  - [2. រៀបចំ Supabase](#2-រៀបចំ-supabase)
  - [3. កំណត់ Environment Variables](#3-កំណត់-environment-variables)
  - [4. ដំណើរការលើម៉ាស៊ីនផ្ទាល់ខ្លួន](#4-ដំណើរការលើម៉ាស៊ីនផ្ទាល់ខ្លួន)
    - [រត់ Tests](#រត់-tests)
  - [5. បង្កើត Catalog, Package និង Stock](#5-បង្កើត-catalog-package-និង-stock)
    - [តាម Admin panel ក្នុង Telegram](#តាម-admin-panel-ក្នុង-telegram)
    - [បញ្ចូល stock ជា `.txt`](#បញ្ចូល-stock-ជា-txt)
    - [Broadcast](#broadcast)
    - [កែប្រែ និងបិទ Catalog/Package](#កែប្រែ-និងបិទ-catalogpackage)
  - [6. កំណត់វីដេអូណែនាំ និងរូបភាព](#6-កំណត់វីដេអូណែនាំ-និងរូបភាព)
    - [វីដេអូ “How to buy”](#វីដេអូ-how-to-buy)
    - [Wallet និងម៉ឺនុយ](#wallet-និងម៉ឺនុយ)
    - [រូបភាព និងវីដេអូ Package](#រូបភាព-និងវីដេអូ-package)
  - [7. ដាក់ពី GitHub ទៅ Render](#7-ដាក់ពី-github-ទៅ-render)
    - [7.1 Push code ទៅ GitHub](#71-push-code-ទៅ-github)
    - [7.2 បង្កើត Render service ពី Blueprint](#72-បង្កើត-render-service-ពី-blueprint)
  - [8. ភ្ជាប់ KHQRPay](#8-ភ្ជាប់-khqrpay)
  - [9. ត្រួតពិនិត្យ Render ដោយ UptimeRobot](#9-ត្រួតពិនិត្យ-render-ដោយ-uptimerobot)
  - [របៀបប្រើក្នុង Telegram](#របៀបប្រើក្នុង-telegram)
  - [សុវត្ថិភាព និងថែទាំ](#សុវត្ថិភាព-និងថែទាំ)
  - [ដោះស្រាយបញ្ហា](#ដោះស្រាយបញ្ហា)
  - [សាកល្បងមុនបើកលក់](#សាកល្បងមុនបើកលក់)

## តម្រូវការមុនចាប់ផ្តើម

- Python និង PowerShell សម្រាប់ការដំឡើង/សាកល្បងក្នុង Windows។
- Python 3.13 (តាម `.python-version`; បញ្ជាក់ interpreter ជាក់លាក់ពេលបង្កើត virtual environment)។
- Telegram account និង bot token ពី [@BotFather](https://t.me/BotFather)។
- Supabase project និង KHQRPay API key។ Cloudinary ត្រូវការសម្រាប់ upload media ថ្មីតាម Admin panel។
- GitHub repository ដែល Render អាចចូលប្រើបាន ប្រសិនបើ deploy តាម Render។

## 1. រៀបចំ Telegram Bot

1. បើក [@BotFather](https://t.me/BotFather) ហើយផ្ញើ `/newbot`។
2. ជ្រើសឈ្មោះពេញ និង username សម្រាប់ bot រួចរក្សាទុក token ដែល BotFather ផ្តល់ឲ្យ។ Welcome message ប្រើឈ្មោះពេញ bot ពី Telegram ដោយស្វ័យប្រវត្តិ; បើប្តូរឈ្មោះ bot សូម restart bot ដើម្បីឲ្យឈ្មោះថ្មីបង្ហាញ។
3. រកលេខ Telegram user ID របស់អ្នក ដើម្បីដាក់ក្នុង `TELEGRAM_ADMIN_IDS`។ ដាក់ជា ID លេខសុទ្ធ មិនមែន username; អាចដាក់ admin ច្រើន ដូចជា 2 នាក់ ដោយបំបែកជាមួយសញ្ញាក្បៀស៖ `123456789,987654321`។
4. កុំផ្ញើ bot token ទៅអ្នកដទៃ និងកុំដាក់វាក្នុង source control។

## 2. រៀបចំ Supabase

1. បង្កើត project ថ្មីនៅ [Supabase](https://supabase.com/) ហើយរង់ចាំ database រួចរាល់។
2. បើក **SQL Editor → New query**។ បើក [schema.sql](schema.sql), ចម្លង SQL ទាំងមូលទៅក្នុង editor ហើយចុច **Run**។ Script នេះបង្កើត tables, indexes និង database functions ដែល bot ត្រូវការ។
3. បើមាន database ចាស់ អាច run `schema.sql` ម្តងទៀត ដើម្បីអនុវត្តការបន្ថែម schema ដែល idempotent។ វាបន្ថែម `products.video_url`, តម្លៃ Reseller, តារាង `resellers`, wallet/deposit tables, scheduled broadcast jobs, payment provider fields និង functions សម្រាប់បញ្ចូល/ប្រើសមតុល្យ។ វាបន្ថែមសារ និង Media ក្រោយ Order ទៅ `categories`; ការកំណត់ចាស់ក្នុង packages នឹងផ្ទេរដោយស្វ័យប្រវត្តិតែពេលគ្រប់ package ក្នុង catalog មាន settings ដូចគ្នា។ បើ settings ខុសគ្នា សូមកំណត់ឡើងវិញតាម **Edit catalog**។ `schema.sql` មិនបង្កើតទិន្នន័យ Catalog ឬ Package ទេ។ Package ចាស់នឹងមានតម្លៃ Reseller ស្មើតម្លៃអតិថិជន រហូតដល់អ្នកកែវា។ ត្រូវ run schema ចុងក្រោយមុនប្រើមុខងារទាំងនេះ។
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
| `STOCK_ENCRYPTION_KEY` | ចាំបាច់ | Fernet key សម្រាប់ encrypt Digital Key/Code។ បង្កើតម្តង ហើយរក្សាទុកឲ្យបានល្អ។ |
| `KHQRPAY_ABA_KEY` | ស្រេចចិត្ត | ABA API key ពី [KHQRPay Dashboard](https://khqrpay.site/dashboard)។ ចាំបាច់ប្រសិនបើបង់តាម ABA។ |
| `KHQRPAY_BAKONG_KEY` | ស្រេចចិត្ត | Bakong API key ពី KHQRPay Dashboard។ ចាំបាច់ប្រសិនបើបង់តាម Bakong។ ត្រូវកំណត់ key យ៉ាងហោចណាស់មួយក្នុង ABA/Bakong។ |
| `KHQRPAY_BASE_URL` | មាន default | `https://khqrpay.site/api/v1`។ |
| `KHQRPAY_WEBHOOK_URL` | ស្រេចចិត្ត | URL សាធារណៈ HTTPS ដូចជា `https://YOUR-SERVICE.onrender.com/webhook/khqrpay`។ |
| `KHQRPAY_ABA_WEBHOOK_SECRET` | ស្រេចចិត្ត | Webhook secret របស់ ABA key។ ចាំបាច់បើប្រើ webhook សម្រាប់ ABA។ |
| `KHQRPAY_BAKONG_WEBHOOK_SECRET` | ស្រេចចិត្ត | Webhook secret របស់ Bakong key។ ចាំបាច់បើប្រើ webhook សម្រាប់ Bakong។ |
| `KHQRPAY_WEBHOOK_SECRET` | ស្រេចចិត្ត | Secret រួម/legacy; អាចប្រើដដែល ប្រសិនបើទាំងពីរ key ប្រើ secret ដូចគ្នា។ |
| `PUBLIC_BASE_URL` | សម្រាប់ ABA link | URL HTTPS សាធារណៈរបស់ Render ដោយមិនបញ្ចូល path។ |
| `CLOUDINARY_CLOUD_NAME` | ត្រូវការ សម្រាប់ upload media | Cloud name របស់ Cloudinary។ |
| `CLOUDINARY_API_KEY` | ត្រូវការ សម្រាប់ upload media | Cloudinary API key។ |
| `CLOUDINARY_API_SECRET` | ត្រូវការ សម្រាប់ upload media | Cloudinary API secret; រក្សាសម្ងាត់។ |
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

Webhook និង ABA deep link ត្រូវការអាសយដ្ឋាន HTTPS ដែល KHQRPay/Telegram អាចចូលដល់បាន។ ដូច្នេះការសាកល្បង webhook ក្នុង local machine ត្រូវការប្រើ HTTPS tunnel; បើមិនបានកំណត់ webhook ទេ bot នៅតែអាចពិនិត្យ payment តាម background polling និងប៊ូតុងពិនិត្យ payment។

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
3. ចុច **Add catalog** ហើយបញ្ចូលឈ្មោះ, ព័ត៌មានលម្អិត និងសារ/Media ក្រោយ Order ដែលចង់ប្រើរួមសម្រាប់ Package ទាំងអស់ក្នុង Catalog នោះ។ ការកំណត់សារ និង Media អាចរំលងបាន។

4. បើក Catalog ដែលទើបបង្កើត ហើយចុច **Add Package**។ Wizard នឹងសួរតាមលំដាប់៖ រូបភាព/វីដេអូ Package, ឈ្មោះ, ព័ត៌មានលម្អិត, តម្លៃអតិថិជន និងតម្លៃ Reseller។ ចុច **Skip** លើជំហានស្រេចចិត្តដើម្បីរំលង។ ឈ្មោះ និងតម្លៃអតិថិជនត្រូវបញ្ចូល។ ពិនិត្យព័ត៌មាន រួចចុច **Confirm save**។ ចុច **Cancel** បានគ្រប់ជំហាន។

តម្លៃទាំងពីរត្រូវធំជាងសូន្យ។ តម្លៃ Reseller អាចស្មើ ឬខ្ពស់ជាងតម្លៃអតិថិជនបាន។ Media ជាជម្រើស។ ផ្ញើរូបភាព, វីដេអូ, GIF ឬឯកសារក្នុងជំហាន media; Bot upload file ទៅ Cloudinary ហើយ Supabase រក្សាទុក HTTPS URL។ Telegram post link ដែល paste នឹងរក្សាទុកជាតំណដើម ហើយត្រូវប្រាកដថា Bot អាចចូល source chat/channel បាន។ Package នឹងបង្ហាញ media នោះក្នុងទំព័រព័ត៌មានរបស់វា។

សារ និង Media ក្រោយ Order កំណត់តែម្តងតាម **Edit catalog** ហើយប្រើដូចគ្នាសម្រាប់គ្រប់ Package ក្នុង Catalog។ ផ្ញើរូបភាព, វីដេអូ, GIF ឬឯកសារនៅជំហាន Media ក្រោយ Order; Bot upload ទៅ Cloudinary ហើយផ្ញើបន្ទាប់ពីទូទាត់ជោគជ័យ មុនពេលផ្ញើ Digital Key/Code។ Digital Key ឬ Code បញ្ចូលតាម **Add stock/Import stock (.txt)** សម្រាប់ Package នីមួយៗ; Bot រក្សាទុកជាទម្រង់ encrypted និងផ្ញើតាមចំនួនដែលបានទិញ។

មុនប្រើមុខងារនេះ សូម run `schema.sql` ចុងក្រោយក្នុង Supabase SQL Editor ដើម្បីបន្ថែម delivery fields លើ categories។ ត្រូវកំណត់ Cloudinary credentials ក្នុង environment ដើម្បីឲ្យ Bot upload ឯកសារ។

Admin អាចបើក **Store admin → Manage resellers → Add reseller** រួចផ្ញើ Telegram user ID ជាលេខ។ ចុចលើ ID ក្នុងបញ្ជីដើម្បីដកសិទ្ធិ។ Reseller ប្រើ bot ដូចអតិថិជនធម្មតា ប៉ុន្តែ catalog, ព័ត៌មាន package និងការកក់ទិញនឹងប្រើតម្លៃ Reseller។ តម្លៃ checkout ត្រូវបានគណនាដោយ Supabase តាម Telegram ID មិនមែនតាមតម្លៃដែលបានផ្ញើពី UI ទេ។

5. ចូល package ហើយប្រើ **Add stock** ដើម្បីបញ្ចូល Digital Key ឬ Code មួយក្នុងមួយបន្ទាត់។ Bot encrypt ទិន្នន័យមុនរក្សាទុក។
6. ឬប្រើទម្រង់សារនេះពី admin account ដោយផ្ទាល់:

```text
addstock PRODUCT_UUID | key1, key2
```

អាចប្រើបន្ទាត់ថ្មីជំនួស comma បាន។ Key ដែលមានស្រាប់ក្នុង stock `available` ឬ `reserved` នឹងត្រូវរំលង; ប៉ុន្តែបើ Key ដូចគ្នាត្រូវបានលក់រួច អ្នកអាចបញ្ចូលវាវិញជាស្តុកថ្មីបាន។ កុំផ្ញើ stock ទៅ chat សាធារណៈ។

### បញ្ចូល stock ជា `.txt`

ក្នុង Admin panel ជ្រើស **Import stock (.txt)**, ជ្រើស Catalog រួចជ្រើស Package ហើយផ្ញើឯកសារ `.txt`។ ឯកសារមិនអាចធំជាង 1 MB; ដាក់ Digital Key ឬ Code មួយក្នុងមួយបន្ទាត់។

### Broadcast

នៅ **Store admin → Broadcast** អាចបង្កើតសារផ្សព្វផ្សាយជាអត្ថបទ, រូបភាព, វីដេអូ, ឯកសារ, audio/voice, GIF/animation, video note, sticker, contact, location, poll និង album បាន។ អាចផ្ញើច្រើនមាតិកាជាប់គ្នា ឬ paste តំណ Telegram post ដូចជា `https://t.me/channel/123` ឬ `https://t.me/c/1234567890/456`។ Bot ចម្លង post តាម Telegram ដោយផ្ទាល់ មិនទាញយក/បញ្ចូលឯកសារឡើងវិញទេ; ដូច្នេះគ្មានកំណត់ទំហំ upload ផ្ទាល់ខ្លួន ប៉ុន្តែ Telegram នៅតែកំណត់លើប្រភពដែល bot អាចចូលដំណើរការ និងការផ្ញើទៅអ្នកទទួល។ ចុច **Preview** ដើម្បីពិនិត្យមាតិកា និងចំនួនអ្នកទទួលមុនពេល Confirm។ ជម្រើស **Add buttons** ទទួល URL មួយក្នុងមួយបន្ទាត់ ដូចជា `បើកហាង | https://example.com`; អាចបញ្ចូលបានអតិបរមា 8 តំណ។

Broadcast ផ្ញើទៅអ្នកប្រើដែលបានចាប់ផ្តើម Bot និង chat IDs ដែលកំណត់នៅ **Settings → Notifications** (Stock និង Order notifications)។ អ្នកទទួលត្រូវបាន deduplicate; អ្នកប្រើដែល block bot ឬ chat ដែល bot មិនមានសិទ្ធិ post នឹងរាប់ជា Failed។ ពេលផ្ញើអាចមើល Sent/Failed/Progress និងចុច **Stop** ដើម្បីបញ្ឈប់។

ចុច **Schedule** ដើម្បីកំណត់ការផ្ញើម្តងតាមម៉ោងភ្នំពេញ ដោយប្រើទម្រង់ `YYYY-MM-DD HH:MM`។ អាចមើល/លុប Schedule នៅ **Scheduled Broadcasts**។ សូម run `schema.sql` ចុងក្រោយ ដើម្បីបង្កើតតារាង `broadcast_jobs`; bot នឹងរក្សា Schedule និងចាប់ផ្តើមផ្ញើក្រោយ restart បាន។ សូមកុំលុបសារដើមដែល Admin ផ្ញើសម្រាប់ draft ដែលបាន Schedule រហូតដល់ការផ្ញើចប់ ឬ Schedule ត្រូវបានលុប។

អាចប្រើ command-line importer ផងដែរ។ ដាក់ Digital Key ឬ Code មួយក្នុងមួយបន្ទាត់:

```text
FF-1H-KEY-001
FF-1D-KEY-002
```

រក Product UUID នៅក្នុង admin panel រួចរត់ពី project root:

```powershell
.\.venv313\Scripts\python.exe -m scripts.import_stock PRODUCT_UUID .\stock.txt
```

Script អាន `.env` ពី project root។ វាត្រូវការ `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` និង `STOCK_ENCRYPTION_KEY`។ Key ដែលមានស្រាប់ជា `available` ឬ `reserved` នឹងត្រូវរំលង; Key ដែលបានលក់រួចអាច import វិញជាស្តុកថ្មី។ បន្ទាប់ពី import សូមលុប/ផ្លាស់ទីឯកសារ stock ឲ្យមានសុវត្ថិភាព ព្រោះក្នុងនោះមាន Digital Key/Code ជាអក្សរធម្មតា។

### កែប្រែ និងបិទ Catalog/Package

- **Edit Catalog**: `ឈ្មោះ | ព័ត៌មានលម្អិត`
- **Edit Package**: `ឈ្មោះ | តម្លៃ | ព័ត៌មានលម្អិត`
- **Remove stock**: បញ្ចូល Digital Key ឬ Code ដើមមួយក្នុងមួយបន្ទាត់។ លុបបានតែ stock ដែលមានស្ថានភាព `available`; stock កំពុង reserve ឬលក់រួចមិនត្រូវបានលុបទេ។
- **Deactivate**: បិទ Catalog នឹងបិទ packages របស់វាទាំងអស់; បិទ Package បិទតែ package នោះ។ វាមិនមែនជា delete ទេ។
- **Sales**: មើលចំនួន paid orders និង revenue។

## 6. កំណត់វីដេអូណែនាំ និងរូបភាព

### វីដេអូ “How to buy”

នៅ **Store admin → Settings → Media → How to Buy** ផ្ញើ Video, Media ឬឯកសារ ហើយចុច **Save media**។ Bot upload ឯកសារទៅ Cloudinary ហើយ Supabase រក្សាទុក URL ក្នុង `how_to_buy_video_url` ដើម្បីផ្ញើឯកសារនោះ។ បើ paste Telegram post link វារក្សាទុកជា URL ដើម ដូចជា `https://t.me/channel/123`; Bot ព្យាយាមចម្លង post ហើយបង្ហាញតំណបើមិនអាចចូល source បាន។ ដើម្បីឲ្យ Bot ផ្ញើ Video ដោយផ្ទាល់ សូម upload Video file ជំនួស post link។ Caption ខ្មែរ/អង់គ្លេសអាចកំណត់នៅ **Settings → Captions**។

```sql
insert into public.bot_settings (key, value)
values
    ('how_to_buy_video_url', 'how_to_buy_video_url'),
    ('how_to_buy_caption_km', 'ការណែនាំអំពីរបៀបទិញ'),
    ('how_to_buy_caption_en', 'How to buy tutorial')
on conflict (key) do update set value = excluded.value;
```

Bot ទាញ settings ថ្មីរាល់ពេលអ្នកប្រើបើក tutorial ដូច្នេះមិនចាំបាច់ restart bot បន្ទាប់ពីកែទេ។

នៅ **Store admin → Settings → Media → Welcome media** upload រូបភាព, វីដេអូ, GIF ឬឯកសារ ហើយចុច **Save media**។ Bot upload ទៅ Cloudinary រួចរក្សាទុក URL ក្នុង `welcome_photo_url` ឬ `welcome_video_url` ក្នុង Supabase។ Telegram post link ដែល paste នឹងរក្សាទុកជា URL ដើម។ ក្នុង Settings → **Store links** អាចកំណត់ Support username និង Join group URL (បង្ហាញជាប៊ូតុង **Join Group** ក្នុងម៉ឺនុយ) ហើយ **Notifications** អាចកំណត់ group IDs សម្រាប់ stock/order notifications។ ការកំណត់ទាំងនេះត្រូវរក្សាទុកក្នុង Admin panel មិនមែន Environment Variables ទេ។

```sql
insert into public.bot_settings (key, value)
values
    ('welcome_video_url', 'https://t.me/channel/123')
on conflict (key) do update set value = excluded.value;
```

ចាប់ផ្តើមប្រើ bot ដោយផ្ញើ `/start`។ អ្នកប្រើថ្មីត្រូវជ្រើសភាសាមុន ដោយ **ភាសាខ្មែរ** ជាជម្រើសដំបូង និងជាភាសាលំនាំដើម; បន្ទាប់ពីជ្រើសរួច Bot នឹងរក្សាទុកជម្រើស ហើយបង្ហាញ Welcome media និងម៉ឺនុយ។

### Wallet និងម៉ឺនុយ

ទំព័រ `/start` បង្ហាញ Welcome media ហើយប៊ូតុងប្រើជា reply keyboard នៅខាងក្រោមប្រអប់សរសេរសារ៖ **Account**, **Add funds**, **Digital Products**, **My Orders** និង **How to Buy**។ ជ្រើសផលិតផលតាម **Digital Products**។ Add funds គាំទ្រចំនួន $1, $5, $10, $15, $20, $50 ឬចំនួនផ្ទាល់ខ្លួនពី $1 ដល់ $500; សមតុល្យត្រូវបានបន្ថែមតែបន្ទាប់ពី KHQRPay បញ្ជាក់ transaction និងចំនួនទឹកប្រាក់។ បើសមតុល្យគ្រប់គ្រាន់ ទំព័រផលិតផលផ្តល់ជម្រើសបង់ដោយ Wallet ផងដែរ។ ដើម្បីប្រើ wallet សូម run `schema.sql` ចុងក្រោយ។

### រូបភាព និងវីដេអូ Package

នៅពេលបង្កើត ឬកែ Package ផ្ញើរូបភាព, វីដេអូ, GIF ឬឯកសារក្នុងជំហាន media/after-order រួចចុច **Save media**។ Bot upload ឯកសារទៅ Cloudinary ហើយ Supabase រក្សាទុក URL; Telegram post link ដែល paste រក្សាទុកជាតំណដើម។ ដើម្បីដាក់/ប្តូរ media package ដែលមានស្រាប់តាម command ផ្ញើរូបភាព/វីដេអូជាមួយ caption:

```text
setmedia PRODUCT_UUID
```

ឬប្រើ `setphoto PRODUCT_UUID` ជាមួយរូបភាព។ Command `setmedia`/`setphoto` upload file ទៅ Cloudinary ហើយរក្សាទុក URL ក្នុង `products.image_url` ឬ `products.video_url`។ ត្រូវ run `schema.sql` ឡើងវិញដើម្បីបន្ថែម `video_url` មុនប្រើមុខងារវីដេអូ។

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
4. នៅពេល Render ស្នើ environment variables សូមបញ្ចូល secrets ពិតតាមតារាងក្នុងផ្នែកទី 3។ តម្លៃចាំបាច់យ៉ាងហោចណាស់មាន `BOT_TOKEN`, `TELEGRAM_ADMIN_IDS`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, key មួយក្នុងចំណោម `KHQRPAY_ABA_KEY`/`KHQRPAY_BAKONG_KEY` និង `STOCK_ENCRYPTION_KEY`។ កុំប្រើតម្លៃឧទាហរណ៍ក្នុង `.env.example` ជា production secrets។
5. កំណែ Python ត្រូវបានកំណត់ដោយ `.python-version` (`3.13`)។ Render អនុវត្តកំណែនេះ ហើយប្រើ patch release ចុងក្រោយដែលមានសម្រាប់ Python 3.13។ មើល [Render Python version guide](https://render.com/docs/python-version)។
6. កំណត់ Cloudinary credentials ដើម្បីឲ្យ Admin upload រូបភាព/វីដេអូ/ឯកសារបាន។ `PUBLIC_BASE_URL` ត្រូវបញ្ចូលក្រោយទទួលបាន service URL។ Support, Join group និង notification destinations អាចកំណត់ក្នុង Telegram តាម **Admin → Settings**។
7. រង់ចាំ deploy ជោគជ័យ។ ក្នុង service **Settings/Overview** ចម្លង URL HTTPS ដែល Render ផ្ដល់ឲ្យ (`https://YOUR-SERVICE.onrender.com`)។
8. នៅ Render **Environment** បន្ថែម/កែ:

```text
PUBLIC_BASE_URL=https://YOUR-SERVICE.onrender.com
KHQRPAY_WEBHOOK_URL=https://YOUR-SERVICE.onrender.com/webhook/khqrpay
```

ចុច **Save, rebuild, and deploy** (ឬប៊ូតុង deploy ដែល Render បង្ហាញ)។ បន្ទាប់ពី commit/push ទៅ `main` Render នឹង deploy commit ថ្មីដោយស្វ័យប្រវត្តិតាមការកំណត់ auto-deploy។
9. បើក `https://YOUR-SERVICE.onrender.com/health`។ លទ្ធផលដែលរំពឹងទុកគឺ `{"status":"ok"}`. ពិនិត្យ Render **Logs** និងសាកល្បង bot ក្នុង Telegram; `/health` បញ្ជាក់ web service ឆ្លើយតបប៉ុណ្ណោះ មិនធានាថា Supabase, Telegram polling ឬ KHQRPay ដំណើរការត្រឹមត្រូវទាំងអស់ទេ។
10. កំណត់ KHQRPay តាមផ្នែកទី 8។ បើប្រើ webhook ត្រូវដាក់ secret តាម key ក្នុង Render **Environment** និង deploy ឡើងវិញ។

កុំបង្កើត Render instances ច្រើនជាងមួយ ខណៈ bot ប្រើ Telegram long polling ព្រោះ instances ច្រើនអាចប៉ះទង្គិចគ្នា។ ក្រោយ deployment លើកដំបូង សូមពិនិត្យ Render Logs ម្តងទៀត ដើម្បីប្រាកដថាមិនមាន database/schema error។

## 8. ភ្ជាប់ KHQRPay

មុនប្ដូរ provider សូមដោះស្រាយ ឬត្រួតពិនិត្យ payment ចាស់ៗដែលនៅ pending ជាមុន។ Payment ID មកពី provider មួយមិនអាចយកទៅសួរ provider ថ្មីបានទេ; កុំបញ្ចេញ stock ឬបញ្ចូល wallet ដោយសន្មត់ថាបានទូទាត់។

បើមាន database ចាស់ សូម run [schema.sql](schema.sql) ចុងក្រោយក្នុង Supabase SQL Editor មុន deploy ដើម្បីរក្សាទុកប្រភេទ ABA/Bakong របស់ payment នីមួយៗ និងប្រើ API key ត្រឹមត្រូវពេលពិនិត្យស្ថានភាព។

1. ចូល [KHQRPay Dashboard](https://khqrpay.site/dashboard) ហើយបង្កើត key មួយ ឬទាំងពីរ៖ **ABA key** ជាមួយ ABA static PayWay link ដាក់ជា `KHQRPAY_ABA_KEY`; **Bakong key** ដាក់ជា `KHQRPAY_BAKONG_KEY` ក្នុង `.env` ឬ Render Environment។ ប្រសិនបើកំណត់ key ទាំងពីរ អ្នកទិញអាចជ្រើស ABA ឬ Bakong នៅពេលទូទាត់។ បើមានតែ key មួយ bot ប្រើវិធីនោះដោយស្វ័យប្រវត្តិ។
2. កំណត់ `KHQRPAY_BASE_URL=https://khqrpay.site/api/v1`។ Bot ប្រើ `POST /aba/create` ឬ `POST /bakong/create` ជាមួយ key តាមវិធីដែលបានជ្រើស ហើយពិនិត្យស្ថានភាពតាម `GET /payment?id=...`។ មើល [ABA API docs](https://khqrpay.site/aba), [Bakong API docs](https://khqrpay.site/bakong), [status docs](https://khqrpay.site/status) និង [errors](https://khqrpay.site/errors)។
3. អាចទុក webhook មិនកំណត់បាន; bot នឹងពិនិត្យតាម `POLL_INTERVAL_SECONDS`។ បើប្រើ webhook សូមកំណត់ `KHQRPAY_WEBHOOK_URL` ជា URL សាធារណៈ HTTPS ដូចជា `https://YOUR-SERVICE.onrender.com/webhook/khqrpay`។ Bot នឹងបញ្ជូន URL នេះពេលបង្កើត QR។
4. ក្នុង Dashboard កំណត់ webhook secret សម្រាប់ key នីមួយៗ (បើប្រើ) ហើយដាក់ជា `KHQRPAY_ABA_WEBHOOK_SECRET` និង/ឬ `KHQRPAY_BAKONG_WEBHOOK_SECRET`។ បើ key ទាំងពីរប្រើ secret ដូចគ្នា អាចប្រើ `KHQRPAY_WEBHOOK_SECRET`។ មើល [webhook docs](https://khqrpay.site/webhooks)។

KHQRPay ផ្ញើ `payment.update` ដែលមាន `id`, `status` និង `amount`។ Bot ផ្ទៀងផ្ទាត់ signature HMAC-SHA256 លើ raw body ហើយសួរ KHQRPay API ម្តងទៀត ដើម្បីផ្ទៀងផ្ទាត់ status និង amount មុនបញ្ចូលសមតុល្យ ឬប្រគល់ stock។ Webhook មិនចាំបាច់ register ដាច់ដោយឡែកទេ។ កុំប្រើ browser redirect ជាភស្តុតាងទូទាត់។

## 9. ត្រួតពិនិត្យ Render ដោយ UptimeRobot

បង្កើត monitor បន្ទាប់ពី Render service deploy ជោគជ័យ និង `/health` បង្ហាញ `{"status":"ok"}`។

1. ចូល [UptimeRobot](https://uptimerobot.com/) ហើយបង្កើត monitor ថ្មីប្រភេទ **HTTP(s)**។
2. ដាក់ឈ្មោះ monitor ឧទាហរណ៍ `PU_MMO169_Sale_Mod Render Health`។
3. ដាក់ URL `https://YOUR-SERVICE.onrender.com/health` (ប្រើ URL ពិតរបស់ Render និងបញ្ចូល path `/health`)។
4. ជ្រើស monitoring interval ដែលគណនីរបស់អ្នកអនុញ្ញាត និងកំណត់ email/contact សម្រាប់ alert ពេល monitor មិនឆ្លើយតប។
5. Save ហើយរង់ចាំ check ដំបូង។ ត្រូវបង្ហាញ **Up/Online** និង HTTP 200។ ប្រើ **Test/Send test alert** ប្រសិនបើ dashboard ផ្ដល់ជម្រើសនេះ ដើម្បីផ្ទៀងផ្ទាត់ថាអ្នកទទួលសារ alert។

Monitor នេះពិនិត្យតែ web health route។ វាមិនសាកល្បងការទិញ, Telegram polling, database schema, KHQRPay payment ឬ webhook ដោយស្វ័យប្រវត្តិទេ។ បើ monitor បង្ហាញ Down សូមពិនិត្យ Render **Events/Deploys/Logs** និងបើក `/health` ដោយផ្ទាល់។ UptimeRobot ជាការជូនដំណឹងបន្ថែម មិនជំនួសការពិនិត្យ logs និងសាកល្បង flow ទិញពិតទេ។

## របៀបប្រើក្នុង Telegram

1. អ្នកទិញចុច **Digital Products**, ជ្រើស Catalog និង Package ដែល Admin បានបង្កើត រួចកំណត់ចំនួនទិញ។ ចំនួនមួយ order មានអតិបរមា 3 ហើយមិនអាចលើសស្តុកនៅសល់។
2. ពេលបង្កើត order, Supabase កក់ stock ជាអាតូមិក ដើម្បីកុំឲ្យអតិថិជនពីរនាក់បាន Digital Key/Code ដូចគ្នា។
3. អ្នកទិញបង់តាម KHQR ឬបើក ABA Mobile។ អាចចុចប៊ូតុងពិនិត្យ payment ដោយខ្លួនឯងបាន។
4. បន្ទាប់ពី KHQRPay បញ្ជាក់ `Paid` bot ផ្ញើ Digital Key/Code ជា code block (ចុចលើ Key ដើម្បី copy) និងមានប៊ូតុង **📋 Copy Key** សម្រាប់ធាតុនីមួយៗ រួចលុបសារដែលមាន QR។ ករណី expired/failed stock ដែលបាន reserve នឹងត្រឡប់ទៅ available។
5. អ្នកប្រើអាចមើល Account/Balance, បញ្ចូលប្រាក់, មើល order history និងចាក់វីដេអូ How to Buy។

Admin panel អាចបន្ថែម/កែ Catalog និង Package, បិទធាតុ, បញ្ចូល/ដក stock, upload `.txt`, Broadcast និងមើល sales summary។ អ្នកប្រើដែលមិនមាន ID ក្នុង `TELEGRAM_ADMIN_IDS` មិនអាចគ្រប់គ្រងហាងបានទេ។

## សុវត្ថិភាព និងថែទាំ

- រក្សា bot token, Supabase service role key, KHQRPay API key/webhook secret, Cloudinary API secret និង stock encryption key ជាសម្ងាត់។ ប្រសិនបើសង្ស័យថាបែកធ្លាយ សូម rotate/revoke key នោះភ្លាមៗ ហើយ update secret នៅ Render។
- Backup `STOCK_ENCRYPTION_KEY` នៅកន្លែងសុវត្ថិភាពដាច់ពី database។ បើបាត់ key នេះ Digital Key/Code ដែលបាន encrypt រួចមិនអាចយកមកប្រើបានទេ។
- កុំបិទ Supabase RLS និងកុំបង្កើត public access policies សម្រាប់ orders/stock។ Backend ប្រើ service-role key ដោយផ្ទាល់។
- ក្នុង **Admin → Settings → Notifications** កំណត់ Stock និង Order notification destinations។ វាគាំទ្រ Telegram user ID, private group/channel chat ID (ឧ. `-1001234567890`) ឬ public `@username`; អាចបញ្ចូលច្រើនដោយបំបែកជាមួយ comma។ បន្ថែម bot ទៅ group ហើយអនុញ្ញាតឲ្យផ្ញើសារ; សម្រាប់ channel ត្រូវបន្ថែម bot ជា admin ដែលអាច post បាន។
- Stock notification ក៏ផ្ញើទៅ users ដែលបានប្រើ bot រួច។ ប៊ូតុង **View product / មើល Package** បើក private chat ជាមួយ Bot ហើយបង្ហាញ Package ដែលមានស្តុកថ្មី។
- Order notification ផ្ញើ Order Log បន្ទាប់ពីទទួល payment និងប្រគល់ Key ជោគជ័យ ហើយក៏ជូនដំណឹងអំពី Wallet deposit ដែលបានបញ្ចូលសមតុល្យរួចរាល់ផងដែរ។ Log មាន Telegram ID, ចំនួនទឹកប្រាក់ និង transaction reference។
- Digital Key/Code ក្នុង database ត្រូវបាន encrypt។ ឯកសារ `.txt` មុន import និងសារ stock ក្នុង Telegram នៅតែជា plaintext ដូច្នេះកុំទុក/បញ្ជូនវាក្នុងកន្លែងមិនមានសុវត្ថិភាព។
- កុំ run bot local និង Render instance ច្រើនក្នុងពេលតែមួយដោយប្រើ bot token ដូចគ្នា ពេល long polling កំពុងដំណើរការ។
- ការបង្កើត payment មាន idempotency key ដោយប្រើ order ID។ ការបញ្ជូន order កើតឡើងតែបន្ទាប់ពី KHQRPay API បញ្ជាក់ការទូទាត់ប៉ុណ្ណោះ។

## ដោះស្រាយបញ្ហា

| រោគសញ្ញា | អ្វីដែលត្រូវពិនិត្យ |
| --- | --- |
| Bot មិនឆ្លើយតប | ពិនិត្យ `BOT_TOKEN`, Render Logs និងថាមាន polling process តែមួយប៉ុណ្ណោះ។ |
| Digital Products មិនបង្ហាញ Catalog | ពិនិត្យថា bot កំពុងដំណើរការ និងមាន Catalog ដែល `active = true`។ បើមាន error សូមមើល logs សម្រាប់ Supabase schema/table error។ |
| Bot បង្ហាញថាមិនមាន Catalog | ពិនិត្យថាមាន `categories` ដែល `active = true` ក្នុង Supabase។ |
| Admin panel មិនបង្ហាញ | ពិនិត្យ Telegram numeric ID ក្នុង `TELEGRAM_ADMIN_IDS` ហើយ restart/redeploy។ |
| Supabase request បរាជ័យ | ពិនិត្យ `SUPABASE_URL`, service-role key និងថា `schema.sql` បាន run ជោគជ័យ។ កុំបិទ RLS ដើម្បីជៀសបញ្ហា។ |
| Supabase បង្ហាញ `column categories.delivery_media_url does not exist` ឬ PostgreSQL error `42703` | បើក Supabase SQL Editor របស់ project ដែល URL ត្រូវគ្នានឹង `SUPABASE_URL` ក្នុង Render ហើយ run [`scripts/fix_catalog_delivery_columns.sql`](scripts/fix_catalog_delivery_columns.sql)។ វាបន្ថែម delivery fields លើ categories ហើយ reload PostgREST schema cache។ បន្ទាប់មករង់ចាំបន្តិច ហើយសាកល្បងម្តងទៀត។ |
| `PGRST205` រកមិនឃើញ table ដូចជា `categories`, `users`, `orders`, `user_wallets` ឬ `broadcast_jobs` | បើក Supabase SQL Editor របស់ project ដែល URL ត្រូវគ្នានឹង `SUPABASE_URL` ក្នុង Render ហើយ run `schema.sql` ចុងក្រោយទាំងមូល ដោយពិនិត្យឲ្យប្រាកដថា query បញ្ចប់ដោយគ្មាន SQL error។ Script នឹង reload PostgREST schema cache នៅចុងក្រោយ។ បន្ទាប់មករង់ចាំបន្តិច ហើយសាកល្បងម្តងទៀត។ |
| Import stock មិនជោគជ័យ | ពិនិត្យ Product UUID, file UTF-8, format មួយ Digital Key/Code ក្នុងមួយបន្ទាត់ និង encryption key។ |
| Media មិនបង្ហាញ | ពិនិត្យ URL ដែលបានរក្សាទុកក្នុង Supabase និងសិទ្ធិ/ការកំណត់ delivery របស់ Cloudinary។ សម្រាប់ Telegram post links ឬ Telegram references ចាស់ សូមប្រាកដថា Bot អាចចូល source chat/channel និងសារដើមនៅមាន។ ពិនិត្យ schema ចុងក្រោយដែលមាន `products.video_url` ផងដែរ។ |
| KHQRPay checkout បរាជ័យ | ពិនិត្យ ABA API key, `KHQRPAY_BASE_URL`, ABA static PayWay link ក្នុង Dashboard និង Render Logs។ |
| Webhook បាន `401` | ពិនិត្យថា webhook secret សម្រាប់ ABA/Bakong key ត្រូវនឹង secret ក្នុង KHQRPay Dashboard ហើយ redeploy បន្ទាប់ពី update។ |
| ABA button មិនដំណើរការ | `PUBLIC_BASE_URL` ត្រូវជា public HTTPS URL របស់ Render ដោយគ្មាន path; ពិនិត្យថា order នៅ pending។ |
| Health check មិនឆ្លើយ | សាកល្បង `/health`, ពិនិត្យ Render Logs និង environment variables ចាំបាច់។ |

## សាកល្បងមុនបើកលក់

បង្កើត Catalog និង Package សាកល្បងដែលមានតម្លៃទាប, បញ្ចូល Digital Key/Code សាកល្បងមួយ, ទិញពី Telegram account ផ្សេង, បញ្ជាក់ថា payment status ត្រឹមត្រូវ និង Key ត្រូវបានផ្ញើតែម្តង។ បន្ទាប់មកសាកល្បង expired/failed payment, order history, admin stock notification និង webhook logs មុនដាក់លក់ពិត។