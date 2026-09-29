
from Crypto.Cipher import AES as _AES
from Crypto.Util.Padding import pad, unpad

def aes_ecb_encrypt(text: bytes, key: bytes) -> str:
    pad_len = 16 - len(text) % 16
    text = text + bytearray([pad_len] * pad_len)
    cipher = _AES.new(key, _AES.MODE_ECB)
    from binascii import hexlify
    return hexlify(cipher.encrypt(text)).decode().upper()

def aes_cbc_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = _AES.new(key, _AES.MODE_CBC, iv)
    return cipher.encrypt(pad(data, _AES.block_size))

def aes_cbc_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = _AES.new(key, _AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(data), _AES.block_size)

ENCRYPT = 1
DECRYPT = 0

sbox = (
    (14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7, 0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8,
     4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0, 15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13),
    (15,1,8,14,6,11,3,4,9,7,2,13,12,0,5,10, 3,13,4,7,15,2,8,15,12,0,1,10,6,9,11,5,
     0,14,7,11,10,4,13,1,5,8,12,6,9,3,2,15, 13,8,10,1,3,15,4,2,11,6,7,12,0,5,14,9),
    (10,0,9,14,6,3,15,5,1,13,12,7,11,4,2,8, 13,7,0,9,3,4,6,10,2,8,5,14,12,11,15,1,
     13,6,4,9,8,15,3,0,11,1,2,12,5,10,14,7, 1,10,13,0,6,9,8,7,4,15,14,3,11,5,2,12),
    (7,13,14,3,0,6,9,10,1,2,8,5,11,12,4,15, 13,8,11,5,6,15,0,3,4,7,2,12,1,10,14,9,
     10,6,9,0,12,11,7,13,15,1,3,14,5,2,8,4, 3,15,0,6,10,10,13,8,9,4,5,11,12,7,2,14),
    (2,12,4,1,7,10,11,6,8,5,3,15,13,0,14,9, 14,11,2,12,4,7,13,1,5,0,15,10,3,9,8,6,
     4,2,1,11,10,13,7,8,15,9,12,5,6,3,0,14, 11,8,12,7,1,14,2,13,6,15,0,9,10,4,5,3),
    (12,1,10,15,9,2,6,8,0,13,3,4,14,7,5,11, 10,15,4,2,7,12,9,5,6,1,13,14,0,11,3,8,
     9,14,15,5,2,8,12,3,7,0,4,10,1,13,11,6, 4,3,2,12,9,5,15,10,11,14,1,7,6,0,8,13),
    (4,11,2,14,15,0,8,13,3,12,9,7,5,10,6,1, 13,0,11,7,4,9,1,10,14,3,5,12,2,15,8,6,
     1,4,11,13,12,3,7,14,10,15,6,8,0,5,9,2, 6,11,13,8,1,4,10,7,9,5,0,15,14,2,3,12),
    (13,2,8,4,6,15,11,1,10,9,3,14,5,0,12,7, 1,15,13,8,10,3,7,4,12,5,6,11,0,14,9,2,
     7,11,4,1,9,12,14,2,0,6,10,13,15,3,5,8, 2,1,14,7,4,10,8,13,15,12,9,0,3,5,6,11),
)

def _bitnum(a, b, c):
    return ((a[(b // 32) * 4 + 3 - (b % 32) // 8] >> (7 - b % 8)) & 1) << c

def _bitnum_intr(a, b, c):
    return ((a >> (31 - b)) & 1) << c

def _bitnum_intl(a, b, c):
    return ((a << b) & 0x80000000) >> c

def _sbox_bit(a):
    return (a & 32) | ((a & 31) >> 1) | ((a & 1) << 4)

def _initial_permutation(input_data):
    return (
        (_bitnum(input_data,57,31)|_bitnum(input_data,49,30)|_bitnum(input_data,41,29)|_bitnum(input_data,33,28)|
         _bitnum(input_data,25,27)|_bitnum(input_data,17,26)|_bitnum(input_data,9,25)|_bitnum(input_data,1,24)|
         _bitnum(input_data,59,23)|_bitnum(input_data,51,22)|_bitnum(input_data,43,21)|_bitnum(input_data,35,20)|
         _bitnum(input_data,27,19)|_bitnum(input_data,19,18)|_bitnum(input_data,11,17)|_bitnum(input_data,3,16)|
         _bitnum(input_data,61,15)|_bitnum(input_data,53,14)|_bitnum(input_data,45,13)|_bitnum(input_data,37,12)|
         _bitnum(input_data,29,11)|_bitnum(input_data,21,10)|_bitnum(input_data,13,9)|_bitnum(input_data,5,8)|
         _bitnum(input_data,63,7)|_bitnum(input_data,55,6)|_bitnum(input_data,47,5)|_bitnum(input_data,39,4)|
         _bitnum(input_data,31,3)|_bitnum(input_data,23,2)|_bitnum(input_data,15,1)|_bitnum(input_data,7,0)),
        (_bitnum(input_data,56,31)|_bitnum(input_data,48,30)|_bitnum(input_data,40,29)|_bitnum(input_data,32,28)|
         _bitnum(input_data,24,27)|_bitnum(input_data,16,26)|_bitnum(input_data,8,25)|_bitnum(input_data,0,24)|
         _bitnum(input_data,58,23)|_bitnum(input_data,50,22)|_bitnum(input_data,42,21)|_bitnum(input_data,34,20)|
         _bitnum(input_data,26,19)|_bitnum(input_data,18,18)|_bitnum(input_data,10,17)|_bitnum(input_data,2,16)|
         _bitnum(input_data,60,15)|_bitnum(input_data,52,14)|_bitnum(input_data,44,13)|_bitnum(input_data,36,12)|
         _bitnum(input_data,28,11)|_bitnum(input_data,20,10)|_bitnum(input_data,12,9)|_bitnum(input_data,4,8)|
         _bitnum(input_data,62,7)|_bitnum(input_data,54,6)|_bitnum(input_data,46,5)|_bitnum(input_data,38,4)|
         _bitnum(input_data,30,3)|_bitnum(input_data,22,2)|_bitnum(input_data,14,1)|_bitnum(input_data,6,0)),
    )

def _inverse_permutation(s0, s1):
    data = bytearray(8)
    data[3]=(_bitnum_intr(s1,7,7)|_bitnum_intr(s0,7,6)|_bitnum_intr(s1,15,5)|_bitnum_intr(s0,15,4)|_bitnum_intr(s1,23,3)|_bitnum_intr(s0,23,2)|_bitnum_intr(s1,31,1)|_bitnum_intr(s0,31,0))
    data[2]=(_bitnum_intr(s1,6,7)|_bitnum_intr(s0,6,6)|_bitnum_intr(s1,14,5)|_bitnum_intr(s0,14,4)|_bitnum_intr(s1,22,3)|_bitnum_intr(s0,22,2)|_bitnum_intr(s1,30,1)|_bitnum_intr(s0,30,0))
    data[1]=(_bitnum_intr(s1,5,7)|_bitnum_intr(s0,5,6)|_bitnum_intr(s1,13,5)|_bitnum_intr(s0,13,4)|_bitnum_intr(s1,21,3)|_bitnum_intr(s0,21,2)|_bitnum_intr(s1,29,1)|_bitnum_intr(s0,29,0))
    data[0]=(_bitnum_intr(s1,4,7)|_bitnum_intr(s0,4,6)|_bitnum_intr(s1,12,5)|_bitnum_intr(s0,12,4)|_bitnum_intr(s1,20,3)|_bitnum_intr(s0,20,2)|_bitnum_intr(s1,28,1)|_bitnum_intr(s0,28,0))
    data[7]=(_bitnum_intr(s1,3,7)|_bitnum_intr(s0,3,6)|_bitnum_intr(s1,11,5)|_bitnum_intr(s0,11,4)|_bitnum_intr(s1,19,3)|_bitnum_intr(s0,19,2)|_bitnum_intr(s1,27,1)|_bitnum_intr(s0,27,0))
    data[6]=(_bitnum_intr(s1,2,7)|_bitnum_intr(s0,2,6)|_bitnum_intr(s1,10,5)|_bitnum_intr(s0,10,4)|_bitnum_intr(s1,18,3)|_bitnum_intr(s0,18,2)|_bitnum_intr(s1,26,1)|_bitnum_intr(s0,26,0))
    data[5]=(_bitnum_intr(s1,1,7)|_bitnum_intr(s0,1,6)|_bitnum_intr(s1,9,5)|_bitnum_intr(s0,9,4)|_bitnum_intr(s1,17,3)|_bitnum_intr(s0,17,2)|_bitnum_intr(s1,25,1)|_bitnum_intr(s0,25,0))
    data[4]=(_bitnum_intr(s1,0,7)|_bitnum_intr(s0,0,6)|_bitnum_intr(s1,8,5)|_bitnum_intr(s0,8,4)|_bitnum_intr(s1,16,3)|_bitnum_intr(s0,16,2)|_bitnum_intr(s1,24,1)|_bitnum_intr(s0,24,0))
    return data

def _f(state, key):
    t1=(_bitnum_intl(state,31,0)|((state&0xF0000000)>>1)|_bitnum_intl(state,4,5)|_bitnum_intl(state,3,6)|((state&0x0F000000)>>3)|_bitnum_intl(state,8,11)|_bitnum_intl(state,7,12)|((state&0x00F00000)>>5)|_bitnum_intl(state,12,17)|_bitnum_intl(state,11,18)|((state&0x000F0000)>>7)|_bitnum_intl(state,16,23))
    t2=(_bitnum_intl(state,15,0)|((state&0x0000F000)<<15)|_bitnum_intl(state,20,5)|_bitnum_intl(state,19,6)|((state&0x00000F00)<<13)|_bitnum_intl(state,24,11)|_bitnum_intl(state,23,12)|((state&0x000000F0)<<11)|_bitnum_intl(state,28,17)|_bitnum_intl(state,27,18)|((state&0x0000000F)<<9)|_bitnum_intl(state,0,23))
    _lrgstate=[(t1>>24)&0xFF,(t1>>16)&0xFF,(t1>>8)&0xFF,(t2>>24)&0xFF,(t2>>16)&0xFF,(t2>>8)&0xFF]
    lrgstate=[_lrgstate[i]^key[i] for i in range(6)]
    st=((sbox[0][_sbox_bit(lrgstate[0]>>2)]<<28)|(sbox[1][_sbox_bit(((lrgstate[0]&3)<<4)|(lrgstate[1]>>4))]<<24)|
        (sbox[2][_sbox_bit(((lrgstate[1]&15)<<2)|(lrgstate[2]>>6))]<<20)|(sbox[3][_sbox_bit(lrgstate[2]&63)]<<16)|
        (sbox[4][_sbox_bit(lrgstate[3]>>2)]<<12)|(sbox[5][_sbox_bit(((lrgstate[3]&3)<<4)|(lrgstate[4]>>4))]<<8)|
        (sbox[6][_sbox_bit(((lrgstate[4]&15)<<2)|(lrgstate[5]>>6))]<<4)|sbox[7][_sbox_bit(lrgstate[5]&63)])
    return (_bitnum_intl(st,15,0)|_bitnum_intl(st,6,1)|_bitnum_intl(st,19,2)|_bitnum_intl(st,20,3)|_bitnum_intl(st,28,4)|
            _bitnum_intl(st,11,5)|_bitnum_intl(st,27,6)|_bitnum_intl(st,16,7)|_bitnum_intl(st,0,8)|_bitnum_intl(st,14,9)|
            _bitnum_intl(st,22,10)|_bitnum_intl(st,25,11)|_bitnum_intl(st,4,12)|_bitnum_intl(st,17,13)|_bitnum_intl(st,30,14)|
            _bitnum_intl(st,9,15)|_bitnum_intl(st,1,16)|_bitnum_intl(st,7,17)|_bitnum_intl(st,23,18)|_bitnum_intl(st,13,19)|
            _bitnum_intl(st,31,20)|_bitnum_intl(st,26,21)|_bitnum_intl(st,2,22)|_bitnum_intl(st,8,23)|_bitnum_intl(st,18,24)|
            _bitnum_intl(st,12,25)|_bitnum_intl(st,29,26)|_bitnum_intl(st,5,27)|_bitnum_intl(st,21,28)|_bitnum_intl(st,10,29)|
            _bitnum_intl(st,3,30)|_bitnum_intl(st,24,31))

def _des_crypt(input_data, key):
    s0, s1 = _initial_permutation(input_data)
    for idx in range(15):
        prev = s1
        s1 = _f(s1, key[idx]) ^ s0
        s0 = prev
    s0 = _f(s1, key[15]) ^ s0
    return _inverse_permutation(s0, s1)

def _key_schedule(key, mode):
    schedule = [[0]*6 for _ in range(16)]
    shifts = (1,1,2,2,2,2,2,2,1,2,2,2,2,2,2,1)
    pc1_c=(56,48,40,32,24,16,8,0,57,49,41,33,25,17,9,1,58,50,42,34,26,18,10,2,59,51,43,35)
    pc1_d=(62,54,46,38,30,22,14,6,61,53,45,37,29,21,13,5,60,52,44,36,28,20,12,4,27,19,11,3)
    pc2=(13,16,10,23,0,4,2,27,14,5,20,9,22,18,11,3,25,7,15,6,26,19,12,1,40,51,30,36,46,54,29,39,50,44,32,47,43,48,38,55,33,52,45,41,49,35,28,31)
    c = sum(_bitnum(key, pc1_c[i], 31-i) for i in range(28))
    d = sum(_bitnum(key, pc1_d[i], 31-i) for i in range(28))
    for i in range(16):
        c = ((c << shifts[i]) | (c >> (28-shifts[i]))) & 0xFFFFFFF0
        d = ((d << shifts[i]) | (d >> (28-shifts[i]))) & 0xFFFFFFF0
        gen = 15-i if mode == DECRYPT else i
        for j in range(6): schedule[gen][j] = 0
        for j in range(24): schedule[gen][j//8] |= _bitnum_intr(c, pc2[j], 7-(j%8))
        for j in range(24,48): schedule[gen][j//8] |= _bitnum_intr(d, pc2[j]-27, 7-(j%8))
    return schedule

def tripledes_key_setup(key, mode):
    if mode == ENCRYPT:
        return [_key_schedule(key[0:], ENCRYPT), _key_schedule(key[8:], DECRYPT), _key_schedule(key[16:], ENCRYPT)]
    return [_key_schedule(key[16:], DECRYPT), _key_schedule(key[8:], ENCRYPT), _key_schedule(key[0:], DECRYPT)]

def tripledes_crypt(data, key):
    for i in range(3):
        data = _des_crypt(data, key[i])
    return data
