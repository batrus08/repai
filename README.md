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

