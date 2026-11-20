# KSeF integration (test environment)

Scout connects to **KSeF**, the Polish national e-invoice system, through the public API 2.0 test environment
(`https://api-test.ksef.mf.gov.pl/v2`). Nothing is mocked on the KSeF side: invoices are really issued, stored,
numbered and downloaded by KSeF. TE data is fictitious and shared between integrators, so Scout uses random test NIPs.

## One click: Connect KSeF

Dashboard → Connections → **KSeF** card → **Connect KSeF** (`POST /v1/connections/ksef:connect`, write token).
The API starts a `ksef_connect` worker job and returns 202; the card polls the `ksef` row of `connections`
(`config.phase`, `config.steps`) until the run is `done` or `error`.

The run (`backend/scout/ksef_sync.py`):

1. Registers the company under a random valid test NIP (stored in `config.nip`, reused afterwards).
2. Signs in with **XAdES**: the auth challenge is signed with a freshly generated self-signed "company seal"
   certificate (`organizationIdentifier = VATPL-<NIP>`). TE accepts self-signed seals; production would use a
   qualified seal or a KSeF certificate instead, nothing else changes.
3. Test environment only: plays the suppliers. Each supplier gets its own test NIP and seal and issues FA(3)
   e-invoices addressed to the pizzeria: one for every payment that has an invoice number but no invoice
   (except the live demo cases), five already-filed invoices, and three new unpaid invoices.
4. Queries KSeF for invoices where the pizzeria is the buyer (`Subject2`), downloads each FA(3) XML and files it:
   - matches a payment without an invoice (same number and amount) → linked, the payment becomes documented;
   - already on file → marked `source = 'ksef'`;
   - no payment yet → stored as a new payable (with its due date).
5. Sets the connection to `connected` and writes the summary and the invoice list (with KSeF numbers) to the row.

Second click = "Sync again": nothing is issued twice, new invoices are pulled.

## Scout checks KSeF for missing invoices

Once connected, the invoicing loop asks KSeF right after its own invoicing system, before anyone's laptop or
mailbox (`_rung_ksef` in `backend/scout/loops/invoicing.py`): query by invoice number, download, compare the
amount, file it (AUTO). The task log shows the KSeF number and the seller's NIP.

Demo case `ksef` (`POST /v1/demo/play/ksef`, `SCOUT_DEMO=1`): Hurtownia Mazur issues invoice HMZ-20417
(€1,509.84) in KSeF right now, then the payment arrives at the bank mock. Scout does not have it on file, finds it
in KSeF and files it. Needs a connected KSeF row (409 `ksef_not_connected` otherwise).

## Protocol notes

* Auth: `/auth/challenge` → `/auth/xades-signature` (XAdES-BES enveloped, RSA-SHA256, C14N 1.1) → poll
  `/auth/{ref}` → `/auth/token/redeem`. Exclusive C14N produced digest mismatches on SignedProperties; C14N 1.1 works.
* Sending: `/sessions/online` with an AES-256 key wrapped by the MF `SymmetricKeyEncryption` RSA key (OAEP-SHA256),
  invoices AES-256-CBC/PKCS#7 with SHA-256 hashes of the plain and encrypted file, then poll each invoice status.
* FA(3) in EUR carries the VAT amount in PLN (`P_14_xW`, fixed demo rate 4.25). Not every gross amount can be one
  line at one VAT rate (cent rounding), so `lines_for_gross` tries other rates or adds a small second line.
* Settings: `KSEF_API_URL` (default: the test environment).
* Tests: `backend/tests/scout/test_ksef.py` with a fake KSeF; `KSEF_LIVE=1` adds a round trip against TE.
