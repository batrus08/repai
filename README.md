# Repai

Bot sederhana untuk memindai pencarian di X (Twitter) dan membalas secara otomatis.
`twt.py` menjalankan browser Playwright, menerapkan filter kata kunci, serta
opsional memanfaatkan OpenAI API untuk memilah tweet yang layak
dibalas.

## Fitur

- Memindai tweet terbaru sesuai kata kunci pencarian.
- Prefilter kata positif/negatif yang didefinisikan pada `bot_config.json`.
- Dukungan klasifikasi niat jual/beli melalui OpenAI (model dari variabel lingkungan `OPENAI_MODEL`, default `gpt-5-nano`; hanya diperlukan jika `ai_enabled=true`).
- Menampilkan statistik proses dan penggunaan sistem.
- Log aktivitas menampilkan tweet yang dibalas atau dilewati beserta alasannya.
- Menyimpan ID tweet yang sudah dibalas ke `replied_ids.json` sehingga tidak diulang ketika bot dijalankan kembali.
- Melewati otomatis tweet yang tidak dapat dibalas (misalnya karena balasan ditutup).
- Penanganan CAPTCHA secara manual.

## Panduan Instalasi Lengkap di Ubuntu

Langkah-langkah berikut diuji pada Ubuntu 22.04 LTS, tetapi tetap relevan untuk
versi 20.04/24.04 dengan penyesuaian minor. Seluruh perintah dijalankan dengan
hak `sudo` kecuali disebutkan berbeda.

1. **Perbarui indeks paket dan paket yang sudah terpasang**

   ```bash
   sudo apt update
   sudo apt upgrade -y
   ```

   Disarankan memulai dengan sistem yang bersih agar dependensi Playwright tidak
   berbenturan dengan paket lama.

2. **Pasang dependensi sistem dasar**

   ```bash
   sudo apt install -y git python3 python3-venv python3-pip \
     build-essential libnss3 libatk-bridge2.0-0 libgtk-3-0 \
     libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
     libasound2 libxshmfence1 libpangocairo-1.0-0 libpango-1.0-0 \
     fonts-liberation ca-certificates
   ```

   Paket grafis diperlukan agar Chromium yang dibundel Playwright dapat
   berjalan di lingkungan desktop maupun headless.

3. **(Opsional) Buat akun layanan khusus**

   ```bash
   sudo useradd -m -s /bin/bash repai
   sudo passwd repai
   sudo usermod -aG sudo repai
   ```

   Jalankan sisa langkah menggunakan akun ini demi keamanan operasional.

4. **Klon repositori dan siapkan struktur kerja**

   ```bash
   git clone https://example.com/repai.git
   cd repai
   mkdir -p logs data
   ```

   Folder `logs/` atau `data/` dapat digunakan untuk menyimpan keluaran tambahan
   (log khusus, cache, dsb.) bila diperlukan.

5. **Buat dan aktifkan lingkungan virtual Python**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   ```

   Mengisolasi dependensi mencegah konflik dengan paket Python lain di sistem.

6. **Pasang dependensi Python proyek**

   ```bash
   pip install openai playwright psutil pyfiglet rich
   ```

   Jika menggunakan file `requirements.txt` sendiri, ganti perintah di atas
   dengan `pip install -r requirements.txt`.

7. **Instal browser Playwright beserta dependensi tambahan**

   ```bash
   playwright install --with-deps chromium
   ```

   Opsi `--with-deps` memastikan paket sistem yang dibutuhkan Chromium ikut
   terpasang. Jalankan perintah ini di dalam virtual environment.

8. **Konfigurasikan berkas aplikasi**

   - Salin atau sunting `bot_config.json` sesuai kata kunci dan preferensi.
   - Jika ingin menyimpan rahasia di `.env`, buat berkas dengan isi minimal:

     ```ini
     OPENAI_API_KEY=sk-...
     OPENAI_MODEL=gpt-5-nano
     ```

   - Pastikan file `.env` memiliki hak akses terbatas (`chmod 600 .env`).

9. **Verifikasi instalasi**

   ```bash
   python -m playwright --version
   python -c "import psutil, rich; print('Playwright siap!')"
   ```

   Kedua perintah di atas memastikan modul inti dapat diimpor tanpa galat.

10. **(Opsional) Siapkan layanan systemd untuk menjalankan bot otomatis**

    Buat file `/etc/systemd/system/repai.service` dengan isi berikut:

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

    Kemudian aktifkan:

    ```bash
    sudo systemctl daemon-reload
    sudo systemctl enable --now repai.service
    ```

    Periksa log layanan dengan `journalctl -u repai.service -f`.

## Persiapan

1. **Dependensi Python**

   Instal paket yang diperlukan:

   ```bash
   pip install openai playwright psutil pyfiglet rich
   ```

   Jalankan juga `playwright install` untuk menyiapkan browser Chromium.

2. **Konfigurasi**

   Sunting `bot_config.json` sesuai kebutuhan. Jika `ai_enabled=true`,
   bot akan meminta `OPENAI_API_KEY` saat pertama kali dijalankan bila
   belum tersedia dan menyimpannya ke file `.env`. Nama model dapat
   diatur melalui `OPENAI_MODEL` atau gunakan bawaan `gpt-5-nano`.

## Menjalankan Bot

```bash
# menjalankan bot; bila kunci belum ada, program akan memintanya
python twt.py

# atau langsung lewat variabel lingkungan
OPENAI_API_KEY=sk-xxx python twt.py
```

Bot akan membuka jendela browser dan mulai memindai tweet. Hentikan dengan
`Ctrl+C`.

## Lisensi

Proyek ini dirilis tanpa lisensi khusus. Gunakan dengan risiko sendiri.

