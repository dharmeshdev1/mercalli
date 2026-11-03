"""Just enough DER to read who issued a certificate.

The standard library only parses a peer certificate after verifying it, and the census wants the
issuer of every certificate, including expired or mismatched ones, so the issuer is read straight
from the DER bytes. Nothing here makes a trust decision.
"""

from __future__ import annotations

_SEQUENCE = 0x30
_VERSION = 0xA0  # [0] EXPLICIT, present on v2 and v3 certificates
_ATTRIBUTES = {
    bytes((0x55, 0x04, 0x03)): "CN",
    bytes((0x55, 0x04, 0x06)): "C",
    bytes((0x55, 0x04, 0x0A)): "O",
    bytes((0x55, 0x04, 0x0B)): "OU",
}
_BMP_STRING = 0x1E
_UNIVERSAL_STRING = 0x1C
_T61_STRING = 0x14


def _tlv(data: bytes, pos: int) -> tuple[int, int, int]:
    """Read the TLV at `pos` and return (tag, start of value, end of value)."""
    if pos + 2 > len(data):
        raise ValueError("truncated DER")
    tag, length = data[pos], data[pos + 1]
    pos += 2
    if length & 0x80:
        size = length & 0x7F
        if size == 0 or size > 4:
            raise ValueError("unsupported DER length")
        length = int.from_bytes(data[pos : pos + size], "big")
        pos += size
    end = pos + length
    if end > len(data):
        raise ValueError("truncated DER")
    return tag, pos, end


def _text(tag: int, raw: bytes) -> str:
    if tag == _BMP_STRING:
        return raw.decode("utf-16-be", errors="replace")
    if tag == _UNIVERSAL_STRING:
        return raw.decode("utf-32-be", errors="replace")
    if tag == _T61_STRING:
        return raw.decode("latin-1")
    return raw.decode("utf-8", errors="replace")


def issuer_of(der: bytes) -> dict[str, str]:
    """Return the issuer's CN, O, OU and C attributes from a DER encoded X.509 certificate."""
    tag, cert, _ = _tlv(der, 0)
    if tag != _SEQUENCE:
        raise ValueError("not a certificate")
    tag, pos, _ = _tlv(der, cert)  # TBSCertificate
    if tag != _SEQUENCE:
        raise ValueError("not a certificate")
    tag, _, end = _tlv(der, pos)
    if tag == _VERSION:
        pos = end
    _, _, pos = _tlv(der, pos)  # serialNumber
    _, _, pos = _tlv(der, pos)  # signature algorithm
    tag, rdn, name_end = _tlv(der, pos)
    if tag != _SEQUENCE:
        raise ValueError("issuer is not a Name")
    out: dict[str, str] = {}
    while rdn < name_end:
        _, attr, set_end = _tlv(der, rdn)
        while attr < set_end:
            _, oid_pos, attr_end = _tlv(der, attr)
            _, oid_start, oid_end = _tlv(der, oid_pos)
            value_tag, value_start, value_end = _tlv(der, oid_end)
            key = _ATTRIBUTES.get(der[oid_start:oid_end])
            if key is not None and key not in out:
                out[key] = _text(value_tag, der[value_start:value_end]).strip()
            attr = attr_end
        rdn = set_end
    return out
