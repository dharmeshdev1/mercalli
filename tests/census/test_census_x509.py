import base64

import pytest

from mercalli.census.x509 import issuer_of

# Self-signed test certificates generated with LibreSSL. In a self-signed certificate the issuer
# equals the subject, which makes the expected values easy to state.
V3_UTF8 = base64.b64decode(
    "MIICrzCCAlWgAwIBAgIJAJg8xdMDTGBaMAoGCCqGSM49BAMCMEkxCzAJBgNVBAYTAlVTMR8wHQYDVQQKDBZaw7xyaWNoIFRydXN0"
    "IFNlcnZpY2VzMRkwFwYDVQQDDBBaVFMgSXNzdWluZyBDQSAxMB4XDTI2MTEwMzEzMzAwMVoXDTM2MTAzMTEzMzAwMVowSTELMAkG"
    "A1UEBhMCVVMxHzAdBgNVBAoMFlrDvHJpY2ggVHJ1c3QgU2VydmljZXMxGTAXBgNVBAMMEFpUUyBJc3N1aW5nIENBIDEwggFLMIIB"
    "AwYHKoZIzj0CATCB9wIBATAsBgcqhkjOPQEBAiEA/////wAAAAEAAAAAAAAAAAAAAAD///////////////8wWwQg/////wAAAAEA"
    "AAAAAAAAAAAAAAD///////////////wEIFrGNdiqOpPns+u9VXaYhrxlHQawzFOw9jvOPD4n0mBLAxUAxJ02CIbnBJNqZnjhE50m"
    "t4GffpAEQQRrF9Hy4SxCR/i85uVjpEDydwN9gS3rM6D0oTlF2JjClk/jQuL+Gn+bjufrSnwPnhYrzjNXazFezsu2QGg3v1H1AiEA"
    "/////wAAAAD//////////7zm+q2nF56E87nKwvxjJVECAQEDQgAExvCchzunbvmJm/xEddxR+FVRjC0p1Y0so3ePgg1kWRmzqEfD"
    "Q1anOlfZF74jvug1eu5+UDhez9NTIFMiD6ngG6MyMDAwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUuVf6Md9m2mzgoa0r+jHd"
    "8Xh1J4swCgYIKoZIzj0EAwIDSAAwRQIgQExRDGjnTGrViKvfu5xLzI3w+RwZ3IHwT2sQfmnLPNQCIQDm0J/Vmbao+FT62n9YdZUV"
    "v1jbWg9w3GyrrCrlDMfTXg=="
)
V1_WITH_OU = base64.b64decode(
    "MIIClTCCAjsCCQDFOVAN04DN3zAJBgcqhkjOPQQBMFkxCzAJBgNVBAYTAkJFMRkwFwYDVQQKDBBHbG9iYWxTaWduIG52LXNhMRIw"
    "EAYDVQQLDAlUZXN0IFVuaXQxGzAZBgNVBAMMEkdsb2JhbFNpZ24gVGVzdCBDQTAeFw0yNjExMDMxMzI5NDVaFw0zNjEwMzExMzI5"
    "NDVaMFkxCzAJBgNVBAYTAkJFMRkwFwYDVQQKDBBHbG9iYWxTaWduIG52LXNhMRIwEAYDVQQLDAlUZXN0IFVuaXQxGzAZBgNVBAMM"
    "Ekdsb2JhbFNpZ24gVGVzdCBDQTCCAUswggEDBgcqhkjOPQIBMIH3AgEBMCwGByqGSM49AQECIQD/////AAAAAQAAAAAAAAAAAAAA"
    "AP///////////////zBbBCD/////AAAAAQAAAAAAAAAAAAAAAP///////////////AQgWsY12Ko6k+ez671VdpiGvGUdBrDMU7D2"
    "O848PifSYEsDFQDEnTYIhucEk2pmeOETnSa3gZ9+kARBBGsX0fLhLEJH+Lzm5WOkQPJ3A32BLeszoPShOUXYmMKWT+NC4v4af5uO"
    "5+tKfA+eFivOM1drMV7Oy7ZAaDe/UfUCIQD/////AAAAAP//////////vOb6racXnoTzucrC/GMlUQIBAQNCAAS96uTHusqvgJkM"
    "Vbxi0vbMkSJzd3mSIysXD+TKYcVgawKOt3pzn5YPBNEehrQy/m9iCtc2YrSAvaXkClpC9NDbMAkGByqGSM49BAEDSQAwRgIhAOJw"
    "8WgcwJg636xhahGUcTrQZTe/h9jx2cKxBvFrBzWPAiEAqaxRgrFQPRm/hqUGZoYIpDdIQkzTr1pTpshe6/Y0/as="
)


def test_v3_certificate_skips_version_and_decodes_utf8() -> None:
    assert issuer_of(V3_UTF8) == {"C": "US", "O": "Zürich Trust Services", "CN": "ZTS Issuing CA 1"}


def test_v1_certificate_without_version_field() -> None:
    assert issuer_of(V1_WITH_OU) == {"C": "BE", "O": "GlobalSign nv-sa", "OU": "Test Unit", "CN": "GlobalSign Test CA"}


@pytest.mark.parametrize("der", [b"", b"\x30", V3_UTF8[:40], b"\x04\x03abc"])
def test_malformed_input_raises_value_error(der: bytes) -> None:
    with pytest.raises(ValueError, match=r"DER|certificate|Name"):
        issuer_of(der)
