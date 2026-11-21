# gopay-gateway-py

Gateway pembayaran GoPay Merchant self-hosted, ditulis sendiri dalam Python
(stdlib murni + `segno` opsional untuk gambar QR). Menggantikan kebutuhan
memakai repo gateway pihak ketiga yang sebagian kodenya terobfuskasi —
di sini **seluruh** kode yang menyentuh sesi GoBiz bisa dibaca.

## Cara kerja

1. `login_cli.py` — login GoID sekali saja: nomor HP GoBiz → OTP SMS/WA →
   access_token + refresh_token tersimpan di `data/session.json` (0600).
   Token berlaku ±24 jam dan di-refresh otomatis (refresh token berputar,
   selalu disimpan ulang). Tidak ada kata sandi yang disimpan.
2. `gateway.py` — layanan HTTP di 127.0.0.1:8890:
   - `POST /create-qris` (API key) — ubah QRIS statis menjadi dinamis
     bernominal terkunci (EMVCo: tag 01→12, tag 54, CRC16 dihitung ulang).
   - `GET /qr/<id>.png` — gambar QR lokal (tidak memakai layanan QR
     pihak ketiga).
   - `GET /api/qr-status/<id>` — cek status; saat dipanggil, gateway
     menarik mutasi dari API merchant-analytics Gojek dan mencocokkan
     nominal + waktu, dengan sistem klaim per trx_id agar satu transaksi
     tidak bisa mengkreditkan dua pembayaran.
   - `POST /check-payment`, `GET /transactions`, `GET /token-status`
     (semua API key).
3. Disiplin polling: tidak ada polling latar. Mutasi hanya ditarik saat
   ada pembayaran yang sedang ditunggu statusnya.

## Protokol GoBiz (sumber terbuka yang terbaca)

- Auth: `POST api.gobiz.co.id/goid/login/request` {client_id:
  "go-biz-web-new", phone_number (nasional polos), country_code: "62"};
  `POST /goid/token` grant_type "otp" / "refresh_token".
- Profil merchant: `GET api.gobiz.co.id/goresto/v5/public/users/config`.
- Mutasi: `GET api.gojekapi.com/merchant-analytics/v2/merchants/transactions`
  — `gross_amount` dalam **sen** (dibagi 100 → rupiah).
- Referensi pembanding: QrisMerchantID (AlfinAI, Python, terbuka) dan
  GOJEK_API_DOCS (azmi2409) — keduanya terdokumentasi dari tangkapan
  portal resmi; implementasi di sini ditulis ulang, bukan menyalin.

## Uji

`python3 test_qris.py` — uji offline modul QRIS (vektor CRC baku 0x29B1,
konversi statis→dinamis, integritas tag merchant).

## Catatan jujur

- Ini otomasi sesi GoBiz **tidak resmi** (bukan API publik GoTo). Risiko
  pembatasan akun ada bila polling agresif — karena itu polling hanya
  on-demand. Keputusan memakai jalur ini ada di pemilik merchant.
- Sesi bisa dicabut GoBiz sewaktu-waktu; bila refresh gagal, jalankan
  `login_cli.py` lagi.
