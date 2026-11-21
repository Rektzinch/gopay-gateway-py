# -*- coding: utf-8 -*-
"""Uji offline modul qris — payload statis sintetis bergaya GoPay Merchant."""
import qris

acct = qris.build_tlv([
    ('00', 'ID.GOPAY.W'),
    ('01', '936009153029876543'),
    ('02', 'GOPAYMERCHANT01'),
    ('03', 'UME'),
])
body = qris.build_tlv([
    ('00', '01'),
    ('01', '11'),
    ('26', acct),
    ('52', '5414'),
    ('53', '360'),
    ('58', 'ID'),
    ('59', 'TOKO KOPI PIU'),
    ('60', 'JAKARTA PUSAT'),
    ('61', '10110'),
    ('62', qris.build_tlv([('07', 'A01')])),
]) + '6304'
static = body + qris.crc16_ccitt(body)

assert qris.verify_crc(static), 'CRC payload sintetis harus valid'
assert qris.get_tag(static, '01') == '11'
assert qris.get_tag(static, '59') == 'TOKO KOPI PIU'

dyn = qris.inject_amount(static, 10000)
assert qris.verify_crc(dyn), 'CRC hasil dinamis harus valid'
assert qris.get_tag(dyn, '01') == '12', 'tag 01 harus jadi dinamis'
assert qris.get_tag(dyn, '54') == '10000', 'tag 54 harus nominal'
assert qris.get_tag(dyn, '59') == 'TOKO KOPI PIU', 'nama merchant utuh'
assert qris.get_tag(dyn, '26') == qris.get_tag(static, '26'), 'akun merchant utuh'

dyn2 = qris.inject_amount(dyn, 27500)
assert qris.get_tag(dyn2, '54') == '27500', 'inject ulang mengganti nominal'
assert qris.verify_crc(dyn2)

# CRC16-CCITT vektor baku: "123456789" -> 0x29B1
assert qris.crc16_ccitt('123456789') == '29B1', 'vektor CRC baku gagal'

print('SEMUA UJI QRIS LULUS')
print('statis :', static)
print('dinamis:', dyn)
