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
- Sistem log terstruktur (JSONL) yang merekam setiap kejadian bot di `logs/events.jsonl` dan log kesalahan terdedikasi pada `logs/error.log`.
- Menyimpan ID tweet yang sudah dibalas ke `replied_ids.json` sehingga tidak diulang ketika bot dijalankan kembali.
- Melewati otomatis tweet yang tidak dapat dibalas (misalnya karena balasan ditutup).
- Penanganan CAPTCHA secara manual.
- Sistem log terpusat ke file `logs/bot.log` lengkap dengan rotasi otomatis agar memudahkan investigasi masalah.

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

   Bagian `logging` mengendalikan perilaku log sistem:

  ```json
  "logging": {
    "level": "INFO",
    "file": "logs/bot.log",
    "event_file": "logs/events.jsonl",
    "error_file": "logs/error.log",
    "max_bytes": 1048576,
    "backup_count": 3
  }
  ```

   - `level`: `DEBUG`, `INFO`, `WARNING`, dll.
   - `file`: lokasi file log utama. Folder akan dibuat otomatis.
   - `event_file`: file JSONL yang mencatat setiap event (keputusan, siklus, toggle tombol, dsb.).
   - `error_file`: log khusus untuk level WARNING ke atas sehingga investigasi error lebih mudah.
   - `max_bytes` dan `backup_count`: mengaktifkan rotasi sehingga log lama tersimpan.

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

