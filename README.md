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
   pip install openai playwright psutil pyfiglet rich
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

Contoh entri penting pada `bot_config.json`:

```json
{
  "search_query": "(jual OR beli) laptop -giveaway",
  "positive_keywords": ["beli", "butuh", "mencari"],
  "negative_keywords": ["giveaway", "hadiah", "bot"],
  "ai_enabled": true,
  "reply_template": "Halo! Kami bisa bantu kebutuhan laptop Anda.",
  "cooldown_seconds": 30
}
```

Penjelasan singkat:

- `search_query` — Query pencarian X standar dengan operator AND/OR/NOT.
- `positive_keywords` — Kata yang wajib ada agar tweet dianggap relevan.
- `negative_keywords` — Kata yang memicu penolakan otomatis.
- `ai_enabled` — Mengaktifkan modul `ai.py`. Pastikan OPENAI_API_KEY tersedia.
- `reply_template` — Pesan dasar yang akan dikirim. Anda bisa memformat ulang di
  kode untuk personalisasi lebih lanjut.
- `cooldown_seconds` — Interval minimal antar balasan agar tidak dianggap spam.

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

| Masalah                                     | Penyebab Umum                                         | Solusi                                                                 |
| ------------------------------------------- | ----------------------------------------------------- | ---------------------------------------------------------------------- |
| Playwright gagal start Chromium             | Dependensi sistem kurang                             | Jalankan `playwright install --with-deps chromium` dan pastikan paket GTK terpasang. |
| Bot macet karena CAPTCHA                    | X mendeteksi aktivitas otomatis                      | Selesaikan CAPTCHA secara manual, kemudian lanjutkan eksekusi.         |
| Galat `OPENAI_API_KEY not set`             | Variabel lingkungan belum tersedia                   | Ekspor `OPENAI_API_KEY` atau buat `.env` sesuai panduan.               |
| Tweet di-skip terus                         | Filter terlalu ketat atau cooldown terlalu tinggi    | Tinjau `bot_config.json`, kurangi kata negatif atau percepat `cooldown`.
| `replied_ids.json` tumbuh besar             | Bot berjalan lama tanpa pembersihan                  | Arsipkan atau hapus entri lama secara berkala (tetap backup terlebih dahulu).

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
