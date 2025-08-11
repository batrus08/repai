# Repai

Bot sederhana untuk memindai pencarian di X (Twitter) dan membalas secara otomatis.
`twt.py` menjalankan browser Playwright, menerapkan filter kata kunci, serta
opsional memanfaatkan model *zero-shot* dari Hugging Face untuk memilah tweet
yang layak dibalas.

## Fitur

- Memindai tweet terbaru sesuai kata kunci pencarian.
- Prefilter kata positif/negatif yang didefinisikan pada `bot_config.json`.
- Dukungan klasifikasi *zero-shot* melalui Hugging Face (token dibaca dari `tokens.json` atau variabel lingkungan `HF_API_TOKEN`).
- Menampilkan statistik proses dan penggunaan sistem.
- Penanganan CAPTCHA secara manual.

## Persiapan

1. **Dependensi Python**

   Instal paket yang diperlukan:

   ```bash
   pip install requests playwright psutil pyfiglet rich
   ```

   Jalankan juga `playwright install` untuk menyiapkan browser Chromium.

2. **Konfigurasi**

   Sunting `bot_config.json` sesuai kebutuhan. Untuk mengaktifkan
   klasifikasi AI, jalankan bot lalu masukkan token Hugging Face ketika
   diminta. Token akan tersimpan otomatis ke `tokens.json`. Sebagai
   alternatif, Anda bisa menyiapkan variabel lingkungan `HF_API_TOKEN`
   sebelum menjalankan bot.

## Menjalankan Bot

```bash
# menjalankan dan memasukkan token saat diminta
python twt.py

# atau langsung lewat variabel lingkungan
HF_API_TOKEN=hf_xxx python twt.py
```

Bot akan membuka jendela browser dan mulai memindai tweet. Hentikan dengan
`Ctrl+C`.

## Lisensi

Proyek ini dirilis tanpa lisensi khusus. Gunakan dengan risiko sendiri.

