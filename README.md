# Vendor Onboarding Verification (PS-2)

A working, deployable process that takes an Indian vendor's onboarding submission, runs it through a visible six-stage verification pipeline, and returns **APPROVED / PENDING / REJECTED** with the reasoning for every finding. For anything not approved, it drafts the message back to the vendor saying exactly what's needed, plus a separate internal note for procurement.

**Stack:** Python 3.11 · Streamlit (multipage) · SQLite · pydantic · rapidfuzz · Google Gemini (`google-genai`, optional) · pytest

---

## 1. Problem summary

Before a company can pay a vendor, someone has to verify the vendor is legitimate. In practice, procurement teams receive incomplete forms, wrong documents, bank accounts in names that don't match the company, and tax IDs that look valid on their own but contradict each other. The review is manual, the follow-up is manual, and the audit trail is someone's inbox. A bad onboarding can lead to **payment fraud** (money sent to the wrong account) or **compliance failures** (TDS / GST credit lost).

This app automates that review. It is deterministic and explainable, stores every run as an audit trail, and makes the follow-up specific.

## 2. Assumptions

| Assumption | Why |
|---|---|
| **India-only vendors** | Lets the rules be specific and real: GSTIN, PAN, IFSC, GST state codes, PIN zones. |
| **Documents are metadata, not files** | Each document is `{doc_type, uploaded, expiry_date}`. OCR is a separate problem (see *What I'd build next*). The decision logic is the same either way. |
| **Rule-based decisions; LLM only for communication** | Approve / reject must be reproducible and auditable. Gemini only *words* the email, sees only vendor-safe action items, and is replaced by a template if it's missing, fails or produces unsafe output. |
| **Vendor master is a local registry** | `data/existing_vendors.json` (8 fictional vendors) is seeded into SQLite and stands in for the ERP vendor master. |
| **WARN vs FAIL** | **WARN** = the vendor can fix it, or a human should look → PENDING. **FAIL** = an integrity, fraud or compliance signal → REJECTED. |
| **All stages always run** | Even after a FAIL, so the reviewer sees the full picture and the vendor gets one consolidated request, not several emails in a row. |
| **Light input normalisation** | IDs are upper-cased and spaces are removed. That's a formatting habit, not a vendor error. |

### Thresholds and why

| Threshold | Value | Reasoning |
|---|---|---|
| Bank-name match: PASS | **≥ 90** (rapidfuzz `token_sort_ratio` after normalisation) | After expanding *Pvt→Private, Ltd→Limited, &→and*, dropping *M/s* and punctuation, the same entity scores ~100. 90 leaves room for minor bank truncation. |
| Bank-name match: WARN | **70–89** | Plausibly the same entity (truncated or abbreviated by the bank). Ask for a bank letter instead of rejecting a real vendor. |
| Bank-name match: FAIL | **< 70** | A different party. Unrelated names score 20–40. This is the payment-diversion pattern. |
| Document expiry | `expiry_date < today` → WARN | An expired certificate proves nothing today. The vendor simply re-uploads it. |

## 3. Process map

```mermaid
flowchart LR
    A[Vendor submission] --> S1[1. Completeness<br/>fields + required docs]
    S1 --> S2[2. Format validation<br/>PAN · GSTIN + checksum · IFSC]
    S2 --> S3[3. Cross-field consistency<br/>GSTIN↔PAN · state · entity type]
    S3 --> S4[4. Bank account<br/>holder-name fuzzy match]
    S4 --> S5[5. Duplicate & fraud<br/>vs vendor master]
    S5 --> S6[6. Document validity<br/>not expired]
    S6 --> D{7. Decision}
    D -- no issues --> OK[APPROVED<br/>welcome email]
    D -- WARN only --> P[PENDING<br/>specific request email]
    D -- any FAIL --> R[REJECTED<br/>neutral email + internal note]
    P -. 8. vendor fixes & resubmits .-> S1
```

The same map is rendered on the app's home page (`st.graphviz_chart`).

### Stage reference

| # | Stage | Checks | Severity |
|---|---|---|---|
| 1 | Completeness | 13 required fields. Required docs per entity type: companies and LLPs need GST cert, PAN card, cancelled cheque and incorporation cert. Partnerships and proprietorships don't need the incorporation cert. | WARN |
| 2 | Format validation | PAN `^[A-Z]{5}[0-9]{4}[A-Z]$`. GSTIN `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$` **and the mod-36 checksum**. IFSC `^[A-Z]{4}0[A-Z0-9]{6}$`. Account number 9–18 digits. Email, Indian mobile, PIN code, known state. | WARN |
| 3 | Cross-field consistency | GSTIN[3–12] == PAN → **FAIL**. GSTIN state code == declared state → **FAIL**. PAN 4th char fits entity type (C company / F firm, LLP / P individual) → **FAIL**. PAN 5th char == legal-name initial → WARN. Legal-name suffix fits entity type → WARN. PIN-code zone fits state → WARN. | FAIL / WARN |
| 4 | Bank account verification | Normalise both names, then `token_sort_ratio`. For proprietorships, a match against the proprietor (contact name) is also accepted. Evidence shows raw vs normalised names and the score before and after normalisation. | PASS / WARN / FAIL |
| 5 | Duplicate & fraud check | Same GSTIN already onboarded → **FAIL**. Same bank account (number + bank) under a different PAN → **FAIL**. Same PAN with a new GSTIN → WARN (likely a new branch). | FAIL / WARN |
| 6 | Document validity | Uploaded docs not expired. Docs expiring within 30 days are noted in the evidence. | WARN |
| 7 | Decision | any FAIL → REJECTED. Otherwise any WARN → PENDING. Otherwise APPROVED. Plain-English summary, plus reasons tied to their stages. | — |
| 8 | Communication | PENDING: polite, specific, numbered request. REJECTED: neutral notice (no reasons) plus internal note with the real reasons and next steps. APPROVED: welcome email with a bank-change call-back warning. | — |

## 4. Edge cases and why they matter

| Sample | Outcome | What happens | Why it matters |
|---|---|---|---|
| `01_happy_path` | ✅ APPROVED | Clean Private Limited from Karnataka. | Baseline: no false positives on a good vendor. |
| `02_gstin_pan_mismatch` | ⛔ REJECTED at *Cross-field* | Every field is well-formed and the GSTIN checksum is valid, but the GSTIN embeds a **different PAN**. | The "looks fine on the surface" case. A GSTIN belongs to one legal entity. A borrowed GSTIN means invoices carry someone else's tax identity, so input tax credit is at risk, and it's a classic impersonation signal. Only cross-referencing catches it. |
| `03_bank_name_close_match` | ✅ APPROVED | Bank holder `M/s ACME PRECISION TOOLS PVT. LTD.` vs legal name `Acme Precision Tools Private Limited`. Raw similarity **26**, after normalisation **100**. | Shows that the fraud control doesn't punish real vendors for cosmetic differences. Without normalisation this would be a false rejection. |
| `04_bank_name_mismatch` | ⛔ REJECTED at *Bank* | An LLP whose account is held by an unrelated individual (score 25). | Account substitution is the most common B2B payment fraud. The vendor email is neutral; the internal note says *do not pay, call back on an independently sourced number*. |
| `05_duplicate_bank_account` | ⛔ REJECTED at *Duplicate* | A new vendor whose bank account already belongs to existing vendor V-1006 (a different PAN). | Two "different" vendors paying into one account is a shell-vendor or collusion pattern. It's invisible when you look at the submission alone. |
| `06_missing_expired_doc` | ⏳ PENDING | A proprietorship (account in the owner's name, which is accepted) with **no cancelled cheque** and an **expired provisional GST certificate**. | Shows the fixable path. The vendor gets one email listing both items exactly. **Resubmit** loads the data back so you can fix it live and watch it become APPROVED. |

The tests also cover: malformed IDs → PENDING (not rejected); same PAN + new GSTIN → WARN; a GSTIN already in the vendor master → REJECTED; LLM leaking or omitting content → template fallback.

**Communication guardrails.** Rejection emails never reveal which control fired, so a fraudster learns nothing about how to tune the next attempt. The real reason goes only into the internal note. Gemini never sees the internal reasons. Its output is checked for banned words (e.g. *fraud, mismatch, duplicate*) and must mention every requested item, otherwise the template is used.

## 5. Running it

### Locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

No API key is needed: emails fall back to the deterministic template. To enable Gemini drafting, either:

```bash
# environment variable
export GEMINI_API_KEY=...            # PowerShell: $env:GEMINI_API_KEY="..."
# or copy .streamlit/secrets.toml.example -> .streamlit/secrets.toml and fill it in
```

Optional: `GEMINI_MODEL` (default `gemini-2.5-flash`). `VENDOR_DB_PATH` overrides the SQLite location (default `data/app.db`, created and seeded on first run).

### Tests

```bash
pytest -q
```

52 tests across three files:

- `tests/test_validators.py`: regexes, plus the GSTIN checksum verified against the published example `27AAPFU0939F1ZV`.
- `tests/test_scenarios.py`: every sample's expected status and failing stage, resubmit-to-approved, no leakage in rejection emails.
- `tests/test_db_llm.py` and `tests/test_ui_smoke.py`: persistence round-trip, LLM fallback and guardrails, and a headless Streamlit `AppTest` of every page plus the resubmit flow.

### Regenerating sample data

```bash
python scripts/generate_samples.py
```

All GSTINs are produced by `build_gstin()` using the checksum function, never hand-typed. All names are fictional.

### Deploying to Streamlit Community Cloud

1. Push this folder to a GitHub repository (`data/app.db` and `.streamlit/secrets.toml` are git-ignored).
2. On [share.streamlit.io](https://share.streamlit.io) → **Create app** → pick the repo and branch, and set **Main file path** to `app.py`.
3. Under **Advanced settings**, choose **Python 3.11**, and optionally paste `GEMINI_API_KEY = "..."` into **Secrets**.
4. Deploy. The SQLite DB is created and seeded on first start. Cloud storage is ephemeral, so use **Reset demo data** in the sidebar for a clean demo.

## 6. Demo script (≈5 minutes)

1. **Sidebar → Reset demo data.** Keep **Demo pace** on (0.5 s per stage).
2. **Home:** read the one-paragraph explanation and walk the process map.
3. **New Submission → "1 · Happy path" → Run.** Six green stages appear one by one → APPROVED → welcome email.
4. **"2 · GSTIN / PAN mismatch":** point out that format validation passes, including the checksum. Cross-field catches it → REJECTED. Show the neutral vendor email vs the internal note.
5. **"3 · Bank name: cosmetic difference":** open stage 4. Raw score 26 → normalised 100 → APPROVED.
6. **"4 · Bank account in a stranger's name":** score 25 → REJECTED, with the internal note's call-back instruction.
7. **"5 · Bank account shared with another vendor":** every stage passes except Duplicate & fraud, which names V-1006.
8. **"6 · Missing and expired documents"** → PENDING, with an email asking for exactly two items. Click **Fix & resubmit**, then in **📎 Documents** tick *Cancelled cheque → Uploaded* and untick *GST certificate → Has validity date*. Run → **APPROVED**, linked to the earlier run.
9. **Dashboard:** KPIs, status breakdown, "where submissions are stopped", filter the table, select a run → **Run Detail** → **Replay live**.

## 7. Project structure

```
app.py                      # router (st.navigation), shared sidebar, landing page + process map
pages/1_New_Submission.py   # sample buttons, editable form, live run view, resubmit
pages/2_Dashboard.py        # KPIs, charts, filterable run history
pages/3_Run_Detail.py       # drill-down + animated replay of a stored run
core/schemas.py             # pydantic: VendorSubmission, StageResult, Decision, RunResult, ...
core/validators.py          # PAN / GSTIN / IFSC regexes, GSTIN mod-36 checksum, state codes, PIN zones
core/name_match.py          # name normalisation + rapidfuzz matching
core/pipeline.py            # the six ordered stages (each a documented rule) + live iterator
core/decision.py            # stage results -> status, summary, reasons
core/llm.py                 # Gemini email drafting, guardrails, template fallback, internal note
core/db.py                  # SQLite: runs (audit trail) + existing_vendors (seeded)
core/orchestrator.py        # one-call process_submission() for tests / scripts
core/samples.py             # sample + registry loaders
ui/                         # Streamlit form state, rendering, sidebar
data/samples/*.json         # 6 demo scenarios
data/existing_vendors.json  # 8-vendor seed registry
scripts/generate_samples.py # generates samples + registry with computed GSTIN checksums
tests/                      # pytest suite
```

## 8. What I'd build next

- **Real document intake:** OCR / layout extraction (e.g. Document AI, or Gemini vision) to read the GSTIN, PAN and holder name *from* the uploaded certificates and cheque, then cross-check them against the typed form. This catches "right number, wrong document".
- **Authoritative verification APIs:** a GST portal taxpayer search (is the GSTIN active, what is its legal name, what is its filing status), PAN verification, and **penny-drop bank verification**, which returns the beneficiary name the bank has on record and replaces our name-matching proxy with ground truth.
- **Vendor self-service portal:** vendors upload documents and fix flagged fields themselves through a magic link from the PENDING email. Verification re-runs automatically.
- **Approval routing and SLAs:** WARN cases go to a reviewer queue. High-risk FAILs go to finance or audit, with four-eyes approval to override, every override recorded, and reminder emails for stale PENDING cases.
- **Risk scoring and monitoring:** move from binary rules to a weighted risk score for prioritisation (keeping hard-stop rules). Periodically re-verify active vendors, watching for GST cancellation, expiring documents and bank-detail change requests.
- **ERP integration:** write approved vendors to the vendor master (SAP / Oracle / Tally), so the duplicate check runs against live data.
