# Repai

Bot sederhana untuk memindai pencarian di X (Twitter) dan membalas secara otomatis.
`twt.py` menjalankan browser Playwright, menerapkan filter kata kunci, serta
opsional memanfaatkan model *zero-shot* dari Hugging Face untuk memilah tweet
yang layak dibalas.

## Fitur

- Memindai tweet terbaru sesuai kata kunci pencarian.
- Prefilter kata positif/negatif yang didefinisikan pada `bot_config.json`.
- Dukungan klasifikasi *zero-shot* melalui Hugging Face (`HF_API_TOKEN`).
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

   Sunting `bot_config.json` sesuai kebutuhan. Jika ingin mengaktifkan
   klasifikasi AI, buat variabel lingkungan `HF_API_TOKEN` yang berisi token
   API Hugging Face.

## Menjalankan Bot

```bash
python twt.py
```

Bot akan membuka jendela browser dan mulai memindai tweet. Hentikan dengan
`Ctrl+C`.

## Lisensi

Proyek ini dirilis tanpa lisensi khusus. Gunakan dengan risiko sendiri.

