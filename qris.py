# -*- coding: utf-8 -*-
"""Modul QRIS dinamis — konversi QRIS statis ke dinamis sesuai EMVCo.

Murni offline, tanpa dependensi. Mengikuti kaidah yang sama dengan
implementasi gateway terbuka yang sudah teruji:
- parse payload sebagai TLV (tag 2 digit, panjang 2 digit, nilai),
- ubah tag 01 (Point of Initiation Method) dari '11' (statis) ke '12' (dinamis),
- sisipkan/ganti tag 54 (nominal transaksi, rupiah penuh tanpa desimal),
- buang CRC lama (tag 63), hitung ulang CRC16-CCITT (0xFFFF, poly 0x1021).
"""


def crc16_ccitt(data: str) -> str:
    """CRC16-CCITT (False) atas string ASCII, hasil 4 hex kapital."""
    crc = 0xFFFF
    for ch in data:
        crc ^= ord(ch) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return format(crc, '04X')


def parse_tlv(payload: str):
    """Parse payload EMVCo menjadi list [(tag, value), ...]."""
    tags = []
    i = 0
    n = len(payload)
    while i + 4 <= n:
        tag = payload[i:i + 2]
        try:
            length = int(payload[i + 2:i + 4])
        except ValueError:
            raise ValueError('panjang TLV tidak valid pada posisi %d' % i)
        value = payload[i + 4:i + 4 + length]
        if len(value) != length:
            raise ValueError('nilai TLV terpotong pada tag %s' % tag)
        tags.append((tag, value))
        i += 4 + length
    if i != n:
        raise ValueError('payload menyisakan %d karakter tak terparse' % (n - i))
    return tags


def build_tlv(tags) -> str:
    out = []
    for tag, value in tags:
        out.append('%s%02d%s' % (tag, len(value), value))
    return ''.join(out)


def get_tag(payload: str, wanted: str):
    for tag, value in parse_tlv(payload):
        if tag == wanted:
            return value
    return None


def verify_crc(payload: str) -> bool:
    if len(payload) < 8 or payload[-8:-4] != '6304':
        return False
    return crc16_ccitt(payload[:-4]) == payload[-4:].upper()


def inject_amount(static_payload: str, amount_rupiah: int) -> str:
    """Ubah QRIS statis menjadi dinamis dengan nominal terkunci."""
    if amount_rupiah <= 0:
        raise ValueError('nominal harus positif')
    payload = static_payload.strip()
    tags = parse_tlv(payload)
    # Buang CRC lama; dihitung ulang di akhir.
    tags = [(t, v) for (t, v) in tags if t != '63']
    amount = str(int(amount_rupiah))
    out = []
    has54 = False
    for tag, value in tags:
        if tag == '01':
            out.append(('01', '12'))
        elif tag == '54':
            out.append(('54', amount))
            has54 = True
        elif tag == '58' and not has54:
            out.append(('54', amount))
            has54 = True
            out.append((tag, value))
        else:
            out.append((tag, value))
    if not has54:
        out.append(('54', amount))
    body = build_tlv(out) + '6304'
    return body + crc16_ccitt(body)


def merchant_name(static_payload: str):
    return get_tag(static_payload, '59')
