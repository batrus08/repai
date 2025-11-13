# Repai

Repai adalah bot otomatis untuk memindai pencarian di X (Twitter) dan
merespons tweet secara selektif. Proyek ini memanfaatkan Playwright untuk
mengendalikan browser Chromium, sedangkan OpenAI API digunakan secara opsional
untuk memilah tweet yang layak dibalas berdasarkan niat (jual, beli, promosi).

> **Catatan:** Bot ini tidak dimaksudkan untuk spam massal. Pastikan Anda
> mematuhi kebijakan platform X dan regulasi lokal terkait otomatisasi.

---

## Gambaran Umum Arsitektur

| Komponen            | Deskripsi                                                                                                                                          |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `twt.py`            | Skrip utama yang memuat konfigurasi, menjalankan Playwright, menerapkan filter kata kunci, menangani CAPTCHA manual, dan mengirim balasan otomatis. |
| `ai.py`             | Utilitas untuk berinteraksi dengan OpenAI API. Dipanggil hanya jika fitur klasifikasi AI diaktifkan.                                                |
| `bot_config.json`   | Sumber konfigurasi utama (kata kunci pencarian, filter positif/negatif, opsi AI, pengaturan jeda, dsb).                                              |
| `replied_ids.json`* | Cache ID tweet yang sudah dibalas supaya tidak diproses ulang. Dibuat otomatis ketika bot dijalankan.                                              |
| `.env`*             | Berisi rahasia (OPENAI_API_KEY, OPENAI_MODEL, dsb). Tidak wajib jika memakai variabel lingkungan langsung.                                          |

`*` = Berkas ini dibuat oleh pengguna ketika dibutuhkan.

---

## Fitur Utama

- **Pencarian Real-time** — Memindai tweet terbaru berdasarkan kata kunci.
- **Filter Kata** — Prefilter positif dan negatif dari `bot_config.json`.
- **Klasifikasi AI (opsional)** — Menggunakan OpenAI API untuk menyaring niat.
- **Statistik Proses** — Menampilkan durasi siklus, jumlah tweet dibalas/lewat.
- **Pencatatan Detail** — Log alasan tweet dilewati (sudah dibalas, ditandai
  negatif, balasan tertutup, gagal memuat, dsb).
- **Penyimpanan Status** — ID tweet yang sudah diproses disimpan ke
  `replied_ids.json` agar eksekusi berikutnya lebih efisien.
- **Penanganan CAPTCHA** — Jika X menampilkan CAPTCHA, bot berhenti sementara
  dan meminta intervensi pengguna melalui antarmuka Playwright.

---

## Persyaratan Sistem

- Ubuntu 22.04 LTS (berhasil diuji); versi 20.04/24.04 juga didukung dengan
  penyesuaian kecil.
- Python 3.9 atau lebih baru.
- Akses internet stabil (dibutuhkan untuk API dan memuat halaman X).
- Akun X (Twitter) dengan hak membalas tweet publik.

---

## Instalasi Lengkap di Ubuntu

Gunakan hak `sudo` untuk seluruh perintah kecuali disebutkan berbeda.

1. **Perbarui paket dasar**

   ```bash
   sudo apt update
   sudo apt upgrade -y
   ```

2. **Pasang dependensi sistem**

   ```bash
   sudo apt install -y git python3 python3-venv python3-pip \
       build-essential libnss3 libatk-bridge2.0-0 libgtk-3-0 \
       libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
       libasound2 libxshmfence1 libpangocairo-1.0-0 libpango-1.0-0 \
       fonts-liberation ca-certificates
   ```

3. **(Opsional) Buat akun layanan**

   ```bash
   sudo useradd -m -s /bin/bash repai
   sudo passwd repai
   sudo usermod -aG sudo repai
   ```

4. **Klon repositori dan siapkan struktur kerja**

   ```bash
   git clone https://example.com/repai.git
   cd repai
   mkdir -p logs data
   ```

5. **Buat lingkungan virtual Python**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   ```

6. **Pasang dependensi Python**

   ```bash
   pip install openai playwright psutil pyfiglet rich python-dotenv
   ```

   Jika Anda memiliki `requirements.txt`, jalankan `pip install -r requirements.txt`.

7. **Instal browser Playwright**

   ```bash
   playwright install --with-deps chromium
   ```

   Jalankan perintah ini di dalam virtual environment agar Playwright mengenali
   path Python yang tepat.

8. **Konfigurasikan aplikasi**

   - Salin contoh `bot_config.json` dan sesuaikan kata kunci, filter kata, serta
     opsi `ai_enabled`.
   - Buat `.env` (opsional) untuk menyimpan rahasia:

     ```ini
     OPENAI_API_KEY=sk-...
     OPENAI_MODEL=gpt-5-nano
     ```

   - Atur izin rahasia: `chmod 600 .env`.

9. **Verifikasi instalasi**

   ```bash
   python -m playwright --version
   python -c "import playwright, psutil; print('Instalasi sukses!')"
   ```

10. **(Opsional) Layanan systemd**

    Buat `/etc/systemd/system/repai.service`:

    ```ini
    [Unit]
    Description=Repai Twitter bot
    After=network-online.target

    [Service]
    Type=simple
    User=repai
    WorkingDirectory=/home/repai/repai
    Environment="PATH=/home/repai/repai/.venv/bin"
    ExecStart=/home/repai/repai/.venv/bin/python /home/repai/repai/twt.py
    Restart=on-failure

    [Install]
    WantedBy=multi-user.target
    ```

    Aktifkan:

    ```bash
    sudo systemctl daemon-reload
    sudo systemctl enable --now repai.service
    journalctl -u repai.service -f
    ```

---

## Konfigurasi Bot

File `bot_config.json` hanya memuat logika bisnis: kata kunci pencarian, pesan
balasan, pengaturan AI, dan opsi dashboard. Contoh ringkas:

```json
{
  "search_config": {
    "keyword": "chatgpt",
    "hashtag": "zonauang",
    "live": true
  },
  "positive_keywords": ["beli", "butuh", "mencari"],
  "negative_keywords": ["giveaway", "hadiah", "bot"],
  "reply_message": "Halo! Kami bisa bantu kebutuhan Anda.",
  "ai_enabled": true,
  "ai_timeout_ms": 4000,
  "pre_filter_keywords": true
}
```

Kustomisasi Chrome/profile kini **sepenuhnya** berpindah ke `.env` sehingga
`bot_config.json` tidak lagi memiliki blok `session`.

## Konfigurasi `.env`

Gunakan file `.env` di root proyek (dibaca otomatis oleh `python-dotenv`).
Contoh isi lengkap:

```ini
OPENAI_API_KEY=sk-....
OPENAI_MODEL=gpt-5-nano
CHROME_USER_DATA_DIR=C:/Users/Nama/AppData/Local/Google/Chrome/User Data
CHROME_PROFILE_NAME=Profile 2
CHROME_EXECUTABLE_PATH=C:/Program Files/Google/Chrome/Application/chrome.exe
TWITTER_COOKIES_PATH=D:/backup/twitter_cookies.json
```

Penjelasan variabel:

- **OPENAI_API_KEY** — Wajib jika `ai_enabled=true`.
- **CHROME_USER_DATA_DIR** — Path *root* user data Chrome, contoh
  `C:/Users/Anda/AppData/Local/Google/Chrome/User Data`. Cara termudah adalah
  membuka `chrome://version`, salin `Profile Path`, lalu hapus bagian akhir
  seperti `\Default` sehingga tersisa direktori `User Data`. Kosongkan nilai ini
  jika ingin memakai profil khusus bot (`./bot_session`).
- **CHROME_PROFILE_NAME** — Nama folder profil di dalam `User Data`, misalnya
  `Default`, `Profile 1`, `Profile 2`, atau `Profile 311`. Anda bisa melihat
  daftar folder tersebut langsung di dalam direktori `User Data` atau mencocokkan
  dengan `Profile Path` di `chrome://version`. Jika dikosongkan maka bot memakai
  `Default`.
- **CHROME_EXECUTABLE_PATH** *(opsional)* — Isi jika ingin memaksa binary
  tertentu. Contoh: `C:/Program Files/Google/Chrome/Application/chrome.exe`. Jika
  dibiarkan kosong, Playwright memakai channel `chrome` bawaannya.
- **TWITTER_COOKIES_PATH** *(opsional)* — Path ke file JSON export cookies X
  (misal dari extension Chrome). Jika tersedia, cookies tersebut akan di-load ke
  context setelah browser terbuka.

> **Tips mencari path dengan aman**
>
> 1. Tutup semua jendela Chrome terlebih dahulu.
> 2. Buka Chrome kembali, kunjungi `chrome://version`, dan catat `Profile Path`.
> 3. Salin direktori `User Data` untuk `CHROME_USER_DATA_DIR` dan nama folder
>    terakhir sebagai `CHROME_PROFILE_NAME`.
> 4. Jika ingin profil terpisah, biarkan `CHROME_USER_DATA_DIR` kosong sehingga
>    bot membuat folder `./bot_session` sendiri lalu login manual satu kali.

## Alur Pertama Kali Berjalan

1. Isi `.env` seperti contoh di atas.
2. Jika Anda menunjuk `CHROME_USER_DATA_DIR` milik Chrome utama, **tutup semua**
   jendela Chrome terlebih dahulu agar profil tidak terkunci.
3. Jalankan `python twt.py`. Browser akan terbuka menggunakan profil yang Anda
   pilih.
4. Jika bot mendeteksi bahwa Anda belum login:
   - Terminal akan menampilkan log `[INFO] X login required. Please login in the browser window.`
   - Jendela browser akan menampilkan halaman login (`https://x.com/login`).
   - Login secara manual sekali saja. Bot menunggu hingga indikator login muncul.
5. Setelah login sukses, bot menavigasi ulang ke halaman pencarian dan memulai
   siklus pemindaian. Eksekusi berikutnya akan langsung memakai sesi yang sama.

## Catatan Penting & Peringatan

- Jangan menjalankan bot bersamaan dengan Chrome biasa di `CHROME_USER_DATA_DIR`
  yang sama. Hal tersebut dapat menyebabkan pesan error
  `BrowserType.launch_persistent_context: Target page, context or browser has been closed`
  karena profil terkunci.
- Untuk penggunaan yang paling aman, biarkan `CHROME_USER_DATA_DIR` kosong agar
  bot membuat profil `./bot_session` sendiri lalu login khusus di profil itu.
- Jika ingin memakai profil produksi, pertimbangkan untuk menyalin seluruh
  folder `User Data/Profile X` ke lokasi berbeda agar tidak mengganggu aktivitas
  harian Anda.
- Pastikan file cookies eksternal (`TWITTER_COOKIES_PATH`) berasal dari export
  yang valid (`[ {"name": "auth_token", ... } ]`). Bot akan menolak file yang
  tidak bisa di-parse.

---

## Menjalankan Bot

```bash
# menjalankan bot; jika kunci belum tersedia, program akan memintanya
python twt.py

# atau langsung melalui variabel lingkungan
OPENAI_API_KEY=sk-xxx python twt.py
```

Saat dijalankan, bot akan:

1. Membuka Chromium melalui Playwright.
2. Meminta Anda login ke akun X (sekali saja per sesi).
3. Memulai pencarian dan memproses timeline sesuai filter.
4. Menampilkan log pada terminal serta menyimpan ringkasan di `logs/` (jika
   Anda menambahkan handler logging).

Hentikan dengan `Ctrl+C`. Playwright akan menutup browser secara otomatis.

---

## Automasi & Pengawasan

- **Systemd**: Lihat bagian instalasi untuk template layanan.
- **Supervisord / Docker**: Proyek dapat dibungkus container; pastikan volume
  untuk `.env`, `bot_config.json`, dan `replied_ids.json` dipetakan.
- **Monitoring**: Gunakan `journalctl`, `tail -f logs/bot.log`, atau integrasi
  dengan alat observabilitas lain (Grafana, Prometheus) melalui eksport metric
  di `twt.py` bila Anda menambahkannya.

---

## Troubleshooting

| Masalah                                     | Penyebab Umum                                         | Solusi                                                    |
| ------------------------------------------- | ----------------------------------------------------- | --------------------------------------------------------- |
| Playwright gagal start Chromium             | Dependensi sistem kurang                             | Jalankan `playwright install --with-deps chromium` dan pastikan paket GTK terpasang. |
| Bot macet karena CAPTCHA                    | X mendeteksi aktivitas otomatis                      | Selesaikan CAPTCHA secara manual, kemudian lanjutkan eksekusi.         |
| Galat `OPENAI_API_KEY not set`             | Variabel lingkungan belum tersedia                   | Ekspor `OPENAI_API_KEY` atau buat `.env` sesuai panduan.               |
| Tweet di-skip terus                         | Filter terlalu ketat atau cooldown terlalu tinggi    | Tinjau `bot_config.json`, kurangi kata negatif atau percepat `cooldown`.
| `replied_ids.json` tumbuh besar             | Bot berjalan lama tanpa pembersihan                  | Arsipkan atau hapus entri lama secara berkala (tetap backup terlebih dahulu).
| `BrowserType.launch_persistent_context: Target page, context or browser has been closed` | Profil Chrome sedang dipakai oleh Chrome biasa atau path user-data salah | Tutup semua Chrome di profil tersebut, atau kosongkan `CHROME_USER_DATA_DIR` agar bot memakai profil khusus (`./bot_session`). |
| Pesan `Sesi/cookies X tidak valid` / bot tidak mendeteksi login | Belum pernah login di profil itu atau cookies sudah kedaluwarsa | Jalankan bot, tunggu halaman login, lakukan login manual hingga muncul log `[INFO] Login detected. Session will be reused next time.]`. |

---

## Praktik Keamanan

- Jangan commit `.env` atau `replied_ids.json` ke repositori publik.
- Batasi hak akses file rahasia (`chmod 600 .env`).
- Pertimbangkan menggunakan VPN atau IP statis agar sesi X tidak sering
  memicu pemeriksaan keamanan.
- Terapkan jeda balasan realistis (`cooldown_seconds`) untuk mengurangi risiko
  diblokir oleh platform.

---

## Lisensi

Proyek ini dirilis tanpa lisensi khusus. Gunakan dengan risiko sendiri dan
pertimbangkan implikasi hukum di yurisdiksi Anda.
