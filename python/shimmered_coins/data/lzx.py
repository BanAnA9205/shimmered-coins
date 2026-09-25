"""LZX decoder for XNA-compressed ``.xnb`` files.

Python port of FNA's ``LzxDecoder.cs`` (itself a C# port of libmspack's ``lzxd.c``;
Copyright 2003-2004 Stuart Caie, Copyright 2011 Ali Scissons; dual MSPL/LGPL license).
This is the compression Terraria's ``Content/Images`` textures use: tModLoader/FNA reads
them with the very same algorithm. ``decompress_xnb_stream`` also ports the block framing
from FNA's ``ContentManager.GetContentReaderFromXnb``.
"""

from __future__ import annotations

import struct

MIN_MATCH = 2
NUM_CHARS = 256
NUM_PRIMARY_LENGTHS = 7
NUM_SECONDARY_LENGTHS = 249

PRETREE_MAXSYMBOLS = 20
PRETREE_TABLEBITS = 6
MAINTREE_MAXSYMBOLS = NUM_CHARS + 50 * 8
MAINTREE_TABLEBITS = 12
LENGTH_MAXSYMBOLS = NUM_SECONDARY_LENGTHS + 1
LENGTH_TABLEBITS = 12
ALIGNED_MAXSYMBOLS = 8
ALIGNED_TABLEBITS = 7

VERBATIM, ALIGNED, UNCOMPRESSED = 1, 2, 3


def _make_tables() -> tuple[list[int], list[int]]:
    extra_bits = [0] * 52
    j = 0
    for i in range(0, 51, 2):
        extra_bits[i] = extra_bits[i + 1] = j
        if i != 0 and j < 17:
            j += 1
    position_base = [0] * 51
    j = 0
    for i in range(51):
        position_base[i] = j
        j += 1 << extra_bits[i]
    return extra_bits, position_base


EXTRA_BITS, POSITION_BASE = _make_tables()


class _BitBuffer:
    """Big-endian bit reader over a byte buffer with FNA's read-two-bytes behaviour."""

    __slots__ = ("buf", "pos", "end", "buffer", "bitsleft")

    def __init__(self, data: bytes, pos: int, end: int):
        self.buf = data
        self.pos = pos
        self.end = end
        self.buffer = 0
        self.bitsleft = 0

    def init(self) -> None:
        self.buffer = 0
        self.bitsleft = 0

    def ensure_bits(self, bits: int) -> None:
        while self.bitsleft < bits:
            lo = self.buf[self.pos] if self.pos < len(self.buf) else 0xFF
            hi = self.buf[self.pos + 1] if self.pos + 1 < len(self.buf) else 0xFF
            self.pos += 2
            self.buffer |= ((hi << 8) | lo) << (16 - self.bitsleft)
            self.buffer &= 0xFFFFFFFF
            self.bitsleft += 16

    def peek_bits(self, bits: int) -> int:
        return (self.buffer >> (32 - bits)) & ((1 << bits) - 1)

    def remove_bits(self, bits: int) -> None:
        self.buffer = (self.buffer << bits) & 0xFFFFFFFF
        self.bitsleft -= bits

    def read_bits(self, bits: int) -> int:
        if bits <= 0:
            return 0
        self.ensure_bits(bits)
        ret = self.peek_bits(bits)
        self.remove_bits(bits)
        return ret


class LzxDecoder:
    """One decoder instance per file (block state persists across frames)."""

    def __init__(self, window: int = 16):
        if not 15 <= window <= 21:
            raise ValueError(f"window {window} out of range")
        self.window_size = 1 << window
        self.window = bytearray([0xDC]) * self.window_size
        posn_slots = 42 if window == 20 else 50 if window == 21 else window << 1
        self.main_elements = NUM_CHARS + (posn_slots << 3)
        self.R0 = self.R1 = self.R2 = 1
        self.header_read = 0
        self.frames_read = 0
        self.block_remaining = 0
        self.block_type = 0
        self.block_length = 0
        self.intel_filesize = 0
        self.intel_curpos = 0
        self.intel_started = 0
        self.window_posn = 0
        self.PRETREE_table = [0] * ((1 << PRETREE_TABLEBITS) + (PRETREE_MAXSYMBOLS << 1))
        self.PRETREE_len = bytearray(PRETREE_MAXSYMBOLS + 64)
        self.MAINTREE_table = [0] * ((1 << MAINTREE_TABLEBITS) + (MAINTREE_MAXSYMBOLS << 1))
        self.MAINTREE_len = bytearray(MAINTREE_MAXSYMBOLS + 64)
        self.LENGTH_table = [0] * ((1 << LENGTH_TABLEBITS) + (LENGTH_MAXSYMBOLS << 1))
        self.LENGTH_len = bytearray(LENGTH_MAXSYMBOLS + 64)
        self.ALIGNED_table = [0] * ((1 << ALIGNED_TABLEBITS) + (ALIGNED_MAXSYMBOLS << 1))
        self.ALIGNED_len = bytearray(ALIGNED_MAXSYMBOLS + 64)

    def decompress(self, data: bytes, pos: int, in_len: int, out: bytearray, out_len: int) -> int:
        """Decode one block of `in_len` bytes into `out` (appending `out_len` bytes)."""
        bitbuf = _BitBuffer(data, pos, pos + in_len)
        window = self.window
        window_size = self.window_size
        startpos = pos
        R0, R1, R2 = self.R0, self.R1, self.R2
        window_posn = self.window_posn

        togo = out_len
        if self.header_read == 0:
            if bitbuf.read_bits(1):
                i = bitbuf.read_bits(16)
                j = bitbuf.read_bits(16)
                self.intel_filesize = (i << 16) | j
            self.header_read = 1

        while togo > 0:
            if self.block_remaining == 0:
                if self.block_type == UNCOMPRESSED:
                    if self.block_length & 1:
                        bitbuf.pos += 1  # realign bitstream to word
                    bitbuf.init()
                self.block_type = bitbuf.read_bits(3)
                i = bitbuf.read_bits(16)
                j = bitbuf.read_bits(8)
                self.block_remaining = self.block_length = (i << 8) | j

                if self.block_type == ALIGNED:
                    for i in range(8):
                        self.ALIGNED_len[i] = bitbuf.read_bits(3)
                    self._make_decode_table(
                        ALIGNED_MAXSYMBOLS, ALIGNED_TABLEBITS, self.ALIGNED_len, self.ALIGNED_table
                    )
                    # rest of aligned header is same as verbatim
                    self._read_lengths(self.MAINTREE_len, 0, 256, bitbuf)
                    self._read_lengths(self.MAINTREE_len, 256, self.main_elements, bitbuf)
                    self._make_decode_table(
                        MAINTREE_MAXSYMBOLS,
                        MAINTREE_TABLEBITS,
                        self.MAINTREE_len,
                        self.MAINTREE_table,
                    )
                    if self.MAINTREE_len[0xE8]:
                        self.intel_started = 1
                    self._read_lengths(self.LENGTH_len, 0, NUM_SECONDARY_LENGTHS, bitbuf)
                    self._make_decode_table(
                        LENGTH_MAXSYMBOLS, LENGTH_TABLEBITS, self.LENGTH_len, self.LENGTH_table
                    )
                elif self.block_type == VERBATIM:
                    self._read_lengths(self.MAINTREE_len, 0, 256, bitbuf)
                    self._read_lengths(self.MAINTREE_len, 256, self.main_elements, bitbuf)
                    self._make_decode_table(
                        MAINTREE_MAXSYMBOLS,
                        MAINTREE_TABLEBITS,
                        self.MAINTREE_len,
                        self.MAINTREE_table,
                    )
                    if self.MAINTREE_len[0xE8]:
                        self.intel_started = 1
                    self._read_lengths(self.LENGTH_len, 0, NUM_SECONDARY_LENGTHS, bitbuf)
                    self._make_decode_table(
                        LENGTH_MAXSYMBOLS, LENGTH_TABLEBITS, self.LENGTH_len, self.LENGTH_table
                    )
                elif self.block_type == UNCOMPRESSED:
                    self.intel_started = 1
                    bitbuf.ensure_bits(16)
                    if bitbuf.bitsleft > 16:
                        bitbuf.pos -= 2  # and align the bitstream
                    r = struct.unpack_from("<III", data, bitbuf.pos)
                    bitbuf.pos += 12
                    R0, R1, R2 = r
                else:
                    raise ValueError("lzx: invalid block type")

            if bitbuf.pos > startpos + in_len:
                if bitbuf.pos > startpos + in_len + 2 or bitbuf.bitsleft < 16:
                    raise ValueError("lzx: input exhausted")

            while self.block_remaining > 0 and togo > 0:
                this_run = min(self.block_remaining, togo)
                togo -= this_run
                self.block_remaining -= this_run
                window_posn &= window_size - 1
                if window_posn + this_run > window_size:
                    raise ValueError("lzx: run wraps the window")

                if self.block_type == UNCOMPRESSED:
                    if bitbuf.pos + this_run > startpos + in_len + 2:
                        raise ValueError("lzx: uncompressed block overruns input")
                    window[window_posn : window_posn + this_run] = data[
                        bitbuf.pos : bitbuf.pos + this_run
                    ]
                    bitbuf.pos += this_run
                    window_posn += this_run
                    continue

                while this_run > 0:
                    main_element = self._read_huff_sym(
                        self.MAINTREE_table,
                        self.MAINTREE_len,
                        MAINTREE_MAXSYMBOLS,
                        MAINTREE_TABLEBITS,
                        bitbuf,
                    )
                    if main_element < NUM_CHARS:
                        window[window_posn] = main_element
                        window_posn += 1
                        this_run -= 1
                        continue
                    main_element -= NUM_CHARS
                    match_length = main_element & NUM_PRIMARY_LENGTHS
                    if match_length == NUM_PRIMARY_LENGTHS:
                        match_length += self._read_huff_sym(
                            self.LENGTH_table,
                            self.LENGTH_len,
                            LENGTH_MAXSYMBOLS,
                            LENGTH_TABLEBITS,
                            bitbuf,
                        )
                    match_length += MIN_MATCH
                    match_offset = main_element >> 3

                    if match_offset > 2:
                        if self.block_type == ALIGNED:
                            extra = EXTRA_BITS[match_offset]
                            match_offset = POSITION_BASE[match_offset] - 2
                            if extra > 3:
                                extra -= 3
                                match_offset += bitbuf.read_bits(extra) << 3
                                match_offset += self._read_huff_sym(
                                    self.ALIGNED_table,
                                    self.ALIGNED_len,
                                    ALIGNED_MAXSYMBOLS,
                                    ALIGNED_TABLEBITS,
                                    bitbuf,
                                )
                            elif extra == 3:
                                match_offset += self._read_huff_sym(
                                    self.ALIGNED_table,
                                    self.ALIGNED_len,
                                    ALIGNED_MAXSYMBOLS,
                                    ALIGNED_TABLEBITS,
                                    bitbuf,
                                )
                            elif extra > 0:
                                match_offset += bitbuf.read_bits(extra)
                            else:
                                match_offset = 1
                        else:  # VERBATIM
                            if match_offset != 3:
                                extra = EXTRA_BITS[match_offset]
                                match_offset = (
                                    POSITION_BASE[match_offset] - 2 + bitbuf.read_bits(extra)
                                )
                            else:
                                match_offset = 1
                        R2, R1, R0 = R1, R0, match_offset
                    elif match_offset == 0:
                        match_offset = R0
                    elif match_offset == 1:
                        match_offset, R1, R0 = R1, R0, R1
                    else:
                        match_offset, R2, R0 = R2, R0, R2

                    rundest = window_posn
                    this_run -= match_length
                    if window_posn >= match_offset:
                        runsrc = rundest - match_offset
                    else:
                        runsrc = rundest + window_size - match_offset
                        copy_length = match_offset - window_posn
                        if copy_length < match_length:
                            match_length -= copy_length
                            window[rundest : rundest + copy_length] = window[
                                runsrc : runsrc + copy_length
                            ]
                            window_posn += copy_length
                            rundest += copy_length
                            runsrc = 0
                    if match_offset >= match_length:
                        window[rundest : rundest + match_length] = window[
                            runsrc : runsrc + match_length
                        ]
                    else:
                        # LZ77 semantics: the match may reference bytes it is producing
                        for k in range(match_length):
                            window[rundest + k] = window[runsrc + k]
                    window_posn += match_length

        self.window_posn = window_posn
        self.R0, self.R1, self.R2 = R0, R1, R2
        start_window_pos = window_posn or window_size
        out += window[start_window_pos - out_len : start_window_pos]
        self.frames_read += 1
        self.intel_curpos += out_len
        return 0

    def _read_huff_sym(self, table, lengths, nsyms, nbits, bitbuf) -> int:
        bitbuf.ensure_bits(16)
        i = table[bitbuf.peek_bits(nbits)]
        if i >= nsyms:
            j = 1 << (32 - nbits)
            while True:
                j >>= 1
                i = (i << 1) | (1 if bitbuf.buffer & j else 0)
                if j == 0:
                    raise ValueError("lzx: huffman decode failed")
                i = table[i]
                if i < nsyms:
                    break
        bitbuf.remove_bits(lengths[i])
        return i

    def _make_decode_table(self, nsyms: int, nbits: int, length, table) -> None:
        bit_num = 1
        pos = 0
        table_mask = 1 << nbits
        bit_mask = table_mask >> 1
        next_symbol = bit_mask

        while bit_num <= nbits:
            for sym in range(nsyms):
                if length[sym] == bit_num:
                    leaf = pos
                    pos += bit_mask
                    if pos > table_mask:
                        raise ValueError("lzx: table overrun")
                    table[leaf : leaf + bit_mask] = [sym] * bit_mask
            bit_mask >>= 1
            bit_num += 1

        if pos != table_mask:
            for sym in range(pos, table_mask):
                table[sym] = 0
            pos <<= 16
            table_mask <<= 16
            bit_mask = 1 << 15
            while bit_num <= 16:
                for sym in range(nsyms):
                    if length[sym] == bit_num:
                        leaf = pos >> 16
                        for fill in range(bit_num - nbits):
                            if table[leaf] == 0:
                                table[next_symbol << 1] = 0
                                table[(next_symbol << 1) + 1] = 0
                                table[leaf] = next_symbol
                                next_symbol += 1
                            leaf = table[leaf] << 1
                            if (pos >> (15 - fill)) & 1:
                                leaf += 1
                        table[leaf] = sym
                        pos += bit_mask
                        if pos > table_mask:
                            raise ValueError("lzx: table overrun")
                bit_mask >>= 1
                bit_num += 1

        if pos == table_mask:
            return
        for sym in range(nsyms):
            if length[sym]:
                raise ValueError("lzx: invalid decode table")

    def _read_lengths(self, lens: bytearray, first: int, last: int, bitbuf) -> None:
        for x in range(20):
            self.PRETREE_len[x] = bitbuf.read_bits(4)
        self._make_decode_table(
            PRETREE_MAXSYMBOLS, PRETREE_TABLEBITS, self.PRETREE_len, self.PRETREE_table
        )
        x = first
        while x < last:
            z = self._read_huff_sym(
                self.PRETREE_table, self.PRETREE_len, PRETREE_MAXSYMBOLS, PRETREE_TABLEBITS, bitbuf
            )
            if z == 17:
                y = bitbuf.read_bits(4) + 4
                while y:
                    lens[x] = 0
                    x += 1
                    y -= 1
            elif z == 18:
                y = bitbuf.read_bits(5) + 20
                while y:
                    lens[x] = 0
                    x += 1
                    y -= 1
            elif z == 19:
                y = bitbuf.read_bits(1) + 4
                z = self._read_huff_sym(
                    self.PRETREE_table,
                    self.PRETREE_len,
                    PRETREE_MAXSYMBOLS,
                    PRETREE_TABLEBITS,
                    bitbuf,
                )
                z = lens[x] - z
                if z < 0:
                    z += 17
                while y:
                    lens[x] = z
                    x += 1
                    y -= 1
            else:
                z = lens[x] - z
                if z < 0:
                    z += 17
                lens[x] = z
                x += 1


def decompress_xnb_stream(
    data: bytes, pos: int, compressed_size: int, decompressed_size: int
) -> bytes:
    """FNA's XNB block framing: big-endian u16 block sizes, 32 KB frames, 0xFF marker."""
    dec = LzxDecoder(16)
    out = bytearray()
    p = pos
    end = pos + compressed_size
    while p < end:
        hi, lo = data[p], data[p + 1]
        block_size = (hi << 8) | lo
        frame_size = 0x8000
        if hi == 0xFF:
            # custom frame size: [FF][hi][lo][block hi][block lo]
            frame_size = (lo << 8) | data[p + 2]
            block_size = (data[p + 3] << 8) | data[p + 4]
            p += 5
        else:
            p += 2
        if block_size == 0 or frame_size == 0:
            break
        dec.decompress(data, p, block_size, out, frame_size)
        p += block_size
    if len(out) != decompressed_size:
        raise ValueError(f"lzx: expected {decompressed_size} bytes, got {len(out)}")
    return bytes(out)


__all__ = ["LzxDecoder", "decompress_xnb_stream"]
