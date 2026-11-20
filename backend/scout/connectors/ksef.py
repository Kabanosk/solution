"""KSeF (Krajowy System e-Faktur) connector, test environment (TE).

Everything here talks to the real KSeF API 2.0 at `api-test.ksef.mf.gov.pl`. TE accepts self-signed certificates,
so Scout authenticates as any test NIP with a freshly generated "company seal" certificate (XAdES signature over
the auth challenge). TE data is fictitious and shared between integrators; never send real data there.

    client = KsefClient()
    token = client.login(nip, "Pizzeria Amalfi")                       # XAdES with a self-signed seal
    ksef_numbers = client.send(token_of_supplier, [fa3_xml(...)])     # an e-invoice issued by a supplier
    metas = client.received(token, since)                             # invoices addressed to our NIP
    doc = parse_fa3(client.download(token, metas[0]["ksefNumber"]))

Flow per KSeF docs (github.com/CIRFMF/ksef-api): /auth/challenge -> /auth/xades-signature -> poll /auth/{ref}
-> /auth/token/redeem; send = /sessions/online (AES key wrapped with the MF RSA key) -> encrypted invoices ->
close; read = /invoices/query/metadata (Subject2 = we are the buyer) -> /invoices/ksef/{ksefNumber}.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from xml.sax.saxutils import escape

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.x509.oid import NameOID
from lxml import etree

TEST_URL = "https://api-test.ksef.mf.gov.pl/v2"
FA_NS = "http://crd.gov.pl/wzor/2025/06/25/13775/"
C14N_11 = "http://www.w3.org/2006/12/xml-c14n11"
CENT = Decimal("0.01")
EUR_PLN = Decimal("4.2500")  # fixed demo rate for the VAT-in-PLN field (P_14_1W) of EUR invoices


class KsefError(RuntimeError):
    pass


def random_nip(rng: random.Random | None = None) -> str:
    """A NIP with a valid check digit (TE wants random NIPs, never real ones)."""
    rng = rng or random.Random()
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    while True:
        digits = [rng.randint(1, 9)] + [rng.randint(0, 9) for _ in range(8)]
        check = sum(a * b for a, b in zip(digits, weights)) % 11
        if check != 10:
            return "".join(map(str, digits)) + str(check)


def seal_certificate(nip: str, name: str) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Self-signed equivalent of a qualified company seal: organizationIdentifier VATPL-<NIP> (TE only)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, name[:64]),
        x509.NameAttribute(x509.ObjectIdentifier("2.5.4.97"), f"VATPL-{nip}"),
        x509.NameAttribute(NameOID.COMMON_NAME, name[:64]),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),
    ])
    now = dt.datetime.now(dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(hours=1))
            .not_valid_after(now + dt.timedelta(days=30)).sign(key, hashes.SHA256()))
    return key, cert


def sign_auth_request(challenge: str, nip: str, key: rsa.RSAPrivateKey, cert: x509.Certificate) -> bytes:
    """AuthTokenRequest signed XAdES-BES, enveloped (canonicalization C14N 1.1: KSeF recomputes the digests)."""
    from signxml import DigestAlgorithm
    from signxml.xades import XAdESSigner

    xml = (f'<AuthTokenRequest xmlns="http://ksef.mf.gov.pl/auth/token/2.0"><Challenge>{escape(challenge)}</Challenge>'
           f"<ContextIdentifier><Nip>{nip}</Nip></ContextIdentifier>"
           "<SubjectIdentifierType>certificateSubject</SubjectIdentifierType></AuthTokenRequest>")
    signer = XAdESSigner(signature_algorithm="rsa-sha256", digest_algorithm=DigestAlgorithm.SHA256, c14n_algorithm=C14N_11)
    signed = signer.sign(
        etree.fromstring(xml.encode()),
        key=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        cert=[cert.public_bytes(serialization.Encoding.PEM).decode()],
    )
    return etree.tostring(signed, xml_declaration=True, encoding="utf-8")


def _sha_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def _money(v: Decimal) -> str:
    return str(Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP))


# -- FA(3) ------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Party:
    nip: str
    name: str
    street: str
    city: str


@dataclass(frozen=True)
class Line:
    description: str
    quantity: Decimal
    unit_net: Decimal
    vat_rate: int = 23


def fa3_xml(number: str, issue_date: dt.date, seller: Party, buyer: Party, lines: list[Line], *,
            currency: str = "EUR", due_date: dt.date | None = None) -> bytes:
    """A minimal valid FA(3) invoice (one VAT rate, 23/8/5 % lines summed by rate)."""
    rows, by_rate = [], {}
    for i, ln in enumerate(lines, 1):
        net = (ln.quantity * ln.unit_net).quantize(CENT, rounding=ROUND_HALF_UP)
        by_rate[ln.vat_rate] = by_rate.get(ln.vat_rate, Decimal(0)) + net
        rows.append(f"<FaWiersz><NrWierszaFa>{i}</NrWierszaFa><P_7>{escape(ln.description)}</P_7><P_8A>szt</P_8A>"
                    f"<P_8B>{ln.quantity.normalize():f}</P_8B><P_9A>{_money(ln.unit_net)}</P_9A>"
                    f"<P_11>{_money(net)}</P_11><P_12>{ln.vat_rate}</P_12></FaWiersz>")
    slots = {23: ("P_13_1", "P_14_1"), 8: ("P_13_2", "P_14_2"), 5: ("P_13_3", "P_14_3")}
    sums, gross = [], Decimal(0)
    for rate in (23, 8, 5):
        if rate in by_rate:
            net = by_rate[rate]
            vat = (net * rate / 100).quantize(CENT, rounding=ROUND_HALF_UP)
            gross += net + vat
            p13, p14 = slots[rate]
            sums.append(f"<{p13}>{_money(net)}</{p13}><{p14}>{_money(vat)}</{p14}>")
            if currency != "PLN":
                sums.append(f"<{p14}W>{_money(vat * EUR_PLN)}</{p14}W>")
    pay = (f"<Platnosc><TerminPlatnosci><Termin>{due_date.isoformat()}</Termin></TerminPlatnosci>"
           "<FormaPlatnosci>6</FormaPlatnosci></Platnosc>") if due_date else ""
    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def party(tag: str, p: Party, extra: str = "") -> str:
        return (f"<{tag}><DaneIdentyfikacyjne><NIP>{p.nip}</NIP><Nazwa>{escape(p.name)}</Nazwa></DaneIdentyfikacyjne>"
                f"<Adres><KodKraju>PL</KodKraju><AdresL1>{escape(p.street)}</AdresL1><AdresL2>{escape(p.city)}</AdresL2>"
                f"</Adres>{extra}</{tag}>")

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Faktura xmlns:etd="http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2022/01/05/eD/DefinicjeTypy/" '
        f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns="{FA_NS}">'
        '<Naglowek><KodFormularza kodSystemowy="FA (3)" wersjaSchemy="1-0E">FA</KodFormularza>'
        f"<WariantFormularza>3</WariantFormularza><DataWytworzeniaFa>{now}</DataWytworzeniaFa>"
        "<SystemInfo>Scout demo supplier</SystemInfo></Naglowek>"
        + party("Podmiot1", seller) + party("Podmiot2", buyer, "<JST>2</JST><GV>2</GV>")
        + f"<Fa><KodWaluty>{currency}</KodWaluty><P_1>{issue_date.isoformat()}</P_1><P_2>{escape(number)}</P_2>"
        + "".join(sums) + f"<P_15>{_money(gross)}</P_15>"
        "<Adnotacje><P_16>2</P_16><P_17>2</P_17><P_18>2</P_18><P_18A>2</P_18A><Zwolnienie><P_19N>1</P_19N></Zwolnienie>"
        "<NoweSrodkiTransportu><P_22N>1</P_22N></NoweSrodkiTransportu><P_23>2</P_23><PMarzy><P_PMarzyN>1</P_PMarzyN>"
        "</PMarzy></Adnotacje><RodzajFaktury>VAT</RodzajFaktury>"
        + "".join(rows) + pay + "</Fa></Faktura>"
    ).encode()


def _vat(net: Decimal, rate: int) -> Decimal:
    return (net * rate / 100).quantize(CENT, rounding=ROUND_HALF_UP)


def _exact_net(gross: Decimal, rate: int) -> Decimal | None:
    net = (gross / (1 + Decimal(rate) / 100)).quantize(CENT, rounding=ROUND_HALF_UP)
    for delta in (Decimal(0), CENT, -CENT, 2 * CENT, -2 * CENT):
        n = net + delta
        if n > 0 and n + _vat(n, rate) == gross:
            return n
    return None


def lines_for_gross(gross: Decimal, description: str, vat_rate: int = 23) -> list[Line]:
    """Lines whose net + VAT (summed per rate, as FA(3) does) is exactly `gross`. Not every gross is reachable
    with one rate (cent rounding), so try the other Polish rates, then split into two lines at two rates."""
    gross = Decimal(gross).quantize(CENT)
    rates = [vat_rate] + [r for r in (23, 8, 5) if r != vat_rate]
    for r in rates:
        if (n := _exact_net(gross, r)) is not None:
            return [Line(description, Decimal(1), n, r)]
    second = rates[1]
    for cents in range(1, 100):  # a small second line ("packaging and delivery") absorbs the rounding
        n2 = Decimal(cents) / 100
        rest = gross - n2 - _vat(n2, second)
        if (n1 := _exact_net(rest, vat_rate)) is not None:
            return [Line(description, Decimal(1), n1, vat_rate), Line("Packaging and delivery", Decimal(1), n2, second)]
    raise ValueError(f"cannot express {gross} as FA(3) lines")


def parse_fa3(xml: bytes) -> dict[str, Any]:
    """The fields Scout files: number, seller, buyer NIP, issue date, gross total, currency, lines (gross)."""
    root = etree.fromstring(xml)
    ns = {"f": root.nsmap.get(None, FA_NS)}

    def text(path: str, node: Any = root) -> str | None:
        found = node.find(path, ns)
        return found.text.strip() if found is not None and found.text else None

    lines = []
    for row in root.findall("f:Fa/f:FaWiersz", ns):
        net = Decimal(text("f:P_11", row) or "0")
        rate = text("f:P_12", row)
        vat = (net * Decimal(rate) / 100).quantize(CENT, rounding=ROUND_HALF_UP) if rate and rate.isdigit() else Decimal(0)
        qty = Decimal(text("f:P_8B", row) or "1")
        lines.append({"description": text("f:P_7", row) or "", "quantity": qty,
                      "unit_price": ((net + vat) / qty).quantize(Decimal("0.0001")) if qty else net + vat,
                      "amount": net + vat})
    return {
        "number": text("f:Fa/f:P_2"),
        "supplier": text("f:Podmiot1/f:DaneIdentyfikacyjne/f:Nazwa"),
        "seller_nip": text("f:Podmiot1/f:DaneIdentyfikacyjne/f:NIP"),
        "buyer_nip": text("f:Podmiot2/f:DaneIdentyfikacyjne/f:NIP"),
        "issue_date": dt.date.fromisoformat(text("f:Fa/f:P_1") or "1970-01-01"),
        "due_date": dt.date.fromisoformat(due) if (due := text("f:Fa/f:Platnosc/f:TerminPlatnosci/f:Termin")) else None,
        "total": Decimal(text("f:Fa/f:P_15") or "0"),
        "currency": text("f:Fa/f:KodWaluty") or "PLN",
        "lines": lines,
    }


# -- HTTP client ------------------------------------------------------------------------------------------------
class KsefClient:
    def __init__(self, base_url: str = TEST_URL, *, http: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = 30.0):
        self.base_url = base_url
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout,
                                          headers={"X-Error-Format": "problem-details"})
        self._sleep = sleep
        self._mf_key: Any = None

    def _call(self, method: str, path: str, token: str | None = None, **kw: Any) -> httpx.Response:
        headers = kw.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = self._http.request(method, path, headers=headers, **kw)
        if r.status_code >= 400:
            raise KsefError(f"KSeF {method} {path} -> {r.status_code}: {r.text[:400]}")
        return r

    def login(self, nip: str, name: str) -> str:
        """Access token for the context of `nip` (XAdES with a fresh self-signed seal; TE only)."""
        key, cert = seal_certificate(nip, name)
        challenge = self._call("POST", "/auth/challenge").json()["challenge"]
        body = sign_auth_request(challenge, nip, key, cert)
        started = self._call("POST", "/auth/xades-signature", content=body,
                             headers={"Content-Type": "application/xml"}).json()
        auth_token = started["authenticationToken"]["token"]
        status: dict[str, Any] = {}
        for _ in range(40):
            status = self._call("GET", f"/auth/{started['referenceNumber']}", auth_token).json()["status"]
            if status.get("code") != 100:
                break
            self._sleep(0.5)
        if status.get("code") != 200:
            raise KsefError(f"KSeF authentication failed: {status}")
        return self._call("POST", "/auth/token/redeem", auth_token).json()["accessToken"]["token"]

    def _symmetric_key_cert(self) -> Any:
        if self._mf_key is None:
            for item in self._call("GET", "/security/public-key-certificates").json():
                if "SymmetricKeyEncryption" in item.get("usage", []):
                    self._mf_key = x509.load_der_x509_certificate(base64.b64decode(item["certificate"])).public_key()
                    break
            else:
                raise KsefError("KSeF published no SymmetricKeyEncryption key")
        return self._mf_key

    def send(self, token: str, invoices: list[bytes], *, wait_seconds: float = 45.0) -> list[dict[str, Any]]:
        """Issue invoices in one interactive session. Returns per invoice {ok, ksefNumber | error}."""
        key, iv = os.urandom(32), os.urandom(16)
        wrapped = self._symmetric_key_cert().encrypt(
            key, asym_padding.OAEP(mgf=asym_padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        session = self._call("POST", "/sessions/online", token, json={
            "formCode": {"systemCode": "FA (3)", "schemaVersion": "1-0E", "value": "FA"},
            "encryption": {"encryptedSymmetricKey": base64.b64encode(wrapped).decode(),
                           "initializationVector": base64.b64encode(iv).decode()}}).json()["referenceNumber"]
        refs = []
        for xml in invoices:
            padder = padding.PKCS7(128).padder()
            enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
            ct = enc.update(padder.update(xml) + padder.finalize()) + enc.finalize()
            refs.append(self._call("POST", f"/sessions/online/{session}/invoices", token, json={
                "invoiceHash": _sha_b64(xml), "invoiceSize": len(xml), "encryptedInvoiceHash": _sha_b64(ct),
                "encryptedInvoiceSize": len(ct), "encryptedInvoiceContent": base64.b64encode(ct).decode()}
            ).json()["referenceNumber"])
        results: dict[str, dict[str, Any]] = {}
        deadline = time.monotonic() + wait_seconds
        while len(results) < len(refs) and time.monotonic() < deadline:
            for ref in refs:
                if ref in results:
                    continue
                st = self._call("GET", f"/sessions/{session}/invoices/{ref}", token).json()
                code = (st.get("status") or {}).get("code")
                if code == 200:
                    results[ref] = {"ok": True, "ksefNumber": st.get("ksefNumber")}
                elif code not in (100, 150, None):
                    results[ref] = {"ok": False, "error": st.get("status")}
            if len(results) < len(refs):
                self._sleep(1.0)
        try:
            self._call("POST", f"/sessions/online/{session}/close", token)
        except KsefError:
            pass  # the session closes itself after 12 h; the invoices are already in
        return [results.get(ref, {"ok": False, "error": "timeout"}) for ref in refs]

    def received(self, token: str, since: dt.datetime, *, invoice_number: str | None = None,
                 page_size: int = 100) -> list[dict[str, Any]]:
        """Metadata of invoices where we are the buyer (Subject2), stored in KSeF since `since`."""
        body: dict[str, Any] = {"subjectType": "Subject2",
                                "dateRange": {"dateType": "PermanentStorage", "from": since.isoformat()}}
        if invoice_number:
            body["invoiceNumber"] = invoice_number
        out, offset = [], 0
        while True:
            page = self._call("POST", "/invoices/query/metadata", token, json=body,
                              params={"pageSize": page_size, "pageOffset": offset, "sortOrder": "Asc"}).json()
            out += page.get("invoices", [])
            if not page.get("hasMore"):
                return out
            offset += 1

    def download(self, token: str, ksef_number: str) -> bytes:
        return self._call("GET", f"/invoices/ksef/{ksef_number}", token).content


# -- Scout connector ---------------------------------------------------------------------------------------------
class KsefLookup:
    """Live lookup used by the invoicing loop: is the invoice for this payment in KSeF (addressed to our NIP)?"""

    name = "ksef"
    label = "KSeF"

    def __init__(self, nip: str, company: str, audit: Any, *, base_url: str = TEST_URL,
                 client_factory: Callable[[str], KsefClient] = KsefClient, sleep: Callable[[float], None] = time.sleep):
        self.nip, self.company, self._audit = nip, company, audit
        self._base_url, self._factory, self._sleep = base_url, client_factory, sleep

    def find(self, number: str, *, task_id: str | None = None, days: int = 60,
             attempts: int = 4) -> list[tuple[dict[str, Any], str]]:
        """[(parsed FA(3), KSeF number)] for invoices with this number. Retries briefly: KSeF indexes new
        invoices within seconds."""
        client = self._factory(self._base_url)
        token = client.login(self.nip, self.company)
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
        metas: list[dict[str, Any]] = []
        for i in range(attempts):
            metas = client.received(token, since, invoice_number=number)
            if metas or i == attempts - 1:
                break
            self._sleep(2.0)
        if self._audit is not None:
            self._audit.record("agent", "search", "invoicing:ksef", f"KSeF number={number} nip={self.nip}",
                               task_id=task_id)
        return [(parse_fa3(client.download(token, m["ksefNumber"])), m["ksefNumber"]) for m in metas]
