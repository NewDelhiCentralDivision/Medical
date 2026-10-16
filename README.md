# CGHS Claim Checker & Sanction Letter Generator

Upload a scanned/photographed CGHS medical reimbursement claim (claim form,
bill, referral). The app reads it with an AI vision model, checks every
billed test/treatment against the official CGHS rate list — **for all
CGHS city tiers, not just Delhi** — flags anything billed above the CGHS
rate or double-billed, and generates the Sanction Memo + NS/Checklist note
used by the division — only the claimant's name, ID, and figures change
each time. It is set up for New Delhi Central Division, but the office name,
address, signatories, head of account and copy-to list are editable in step 4,
so any division (North / East / West / ...) can use it as is.

**This is a decision-support tool, not an auto-approver.** Every match,
rate, and total is shown for the processing officer to review and correct
before a case is sanctioned. Treat every output as a draft.

---

## What's included

| File | Purpose |
|---|---|
| `app.py` | The Streamlit app (UI, OCR call, matching, document generation) |
| `matcher.py` | Matches claimed line items to CGHS codes (exact code match first, fuzzy text match as fallback) |
| `bundles.py` | The billing rules: which CGHS codes are packages that already include other tests, and what must not be counted twice (see "How double counting is prevented") |
| `letter_templates.py` | Generates the Sanction Memo and NS/Checklist `.docx` files from your existing wording; the office details come from an editable profile (`DEFAULT_OFFICE`) |
| `tests/` | Automated checks for the billing rules and the whole app (run with `python -m pytest tests/ -q`) |
| `num2words_inr.py` | Converts rupee figures to words (Indian lakh/crore numbering) |
| `data/CGHS_Rates_All_Tiers.xlsx` | The full CGHS rate master, extracted from `CGHS_RATE_as_on_3_10_2025.pdf` - all 1,998 codes, with Tier I (X City) / Tier II (Y City) / Tier III (Z City) rates side by side, semi-private ward base rates |
| `data/cghs_all_tiers.json` | Same data as above, used internally by the app |

## Setup

### Using the tool (officers)

Officers never see or type an API key. In the sidebar they pick **how to read the documents**; if the key has been set up (below), the first choice is **Auto-read with AI (Gemini)** and the only thing to select is the **model** from the dropdown:

| Reading mode | Needs the key? | What it does |
|---|---|---|
| **Auto-read with AI (Gemini)** | Built in (set once by the administrator) | Reads the documents and auto-fills every field, including line items |
| **Auto-read with AI (Claude)** | Only shown if an `ANTHROPIC_API_KEY` is also set up | Same, with Claude |
| **Offline OCR - raw text only** | No | Runs Tesseract on your own machine and shows the raw text to read beside the scan - it does **not** auto-fill amounts (it can misread a digit, e.g. ₹1,000 as ₹600) |
| **Type everything manually** | No | Skip upload and type the details in; the rate check, flagging and letters work exactly the same |

If no key has been set up, the AI options simply do not appear and the tool still works in the other two modes.

**Models.** Gemini choices, checked against Google's model pages on 15 Oct 2026: `gemini-3.8-flash` (default, fast), `gemini-4-argon` (newest and most capable), `gemini-3.6-flash`, `gemini-3.5-flash-lite` (cheapest), `gemini-3.1-pro-preview` (preview) and `gemini-flash-latest` (rolling alias). The dropdown also has an *Other* entry for any newer model name. Google retires models regularly - the current list is at [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models).

### One-time key setup (administrator)

Get a free key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (an AI Studio key, not a Google Cloud Console key), then store it in **one** of these places - it is picked up automatically and never shown to users:

- **Streamlit Community Cloud:** app → Settings → Secrets, add `GEMINI_API_KEY = "your-gemini-key"`.
- **Local / office PC:** copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in the same line.
- **Environment variable:** set `GEMINI_API_KEY`.

> **Why the key is not written into `app.py`:** this GitHub repository is public. Google scans public repositories and automatically disables any key it finds, and anyone could also copy the key and spend your quota. Secrets keep the key inside the tool but out of the code. `.streamlit/secrets.toml` is in `.gitignore` so it cannot be pushed by mistake.

### Install and run

```bash
pip install -r requirements.txt
streamlit run app.py
```
It opens at `http://localhost:8501`. You also need two system programs:
- **poppler** (reads PDF pages): Windows - [binaries](https://github.com/oschwartz10612/poppler-windows/releases), add `bin` to PATH; Mac - `brew install poppler`; Linux - `sudo apt install poppler-utils`
- **tesseract** (only for Offline OCR): Windows - [UB-Mannheim build](https://github.com/UB-Mannheim/tesseract/wiki), add to PATH; Mac - `brew install tesseract`; Linux - `sudo apt install tesseract-ocr`

On Streamlit Community Cloud, `packages.txt` installs poppler automatically: push the repo, point a new app at `app.py` on [share.streamlit.io](https://share.streamlit.io), and add the secret above.

## How the item matching works

For each line item extracted from the claim:
1. If a CGHS code was read directly off the document, it's matched exactly.
2. Otherwise, the test/treatment description is fuzzy-matched against all 1,998 CGHS descriptions, and the closest one above a similarity threshold is used.
3. Each row is capped at its CGHS rate, then the billing rules (next section) decide what actually counts. The **Calculation sheet** under the table shows, for every row: the claimed amount, the CGHS rate, the amount **counted**, a result and a plain-English note:
   - **COUNTED** — payable (capped at the CGHS rate; a note says so when the bill was higher)
   - **INCLUDED IN PACKAGE** — already covered by a package that is also billed (e.g. Hb, TLC and Platelets inside a CBC); counted as ₹0
   - **REPEAT - COUNTED ONCE** — the same diagnostic code billed twice; counted once
   - **REVIEW - …** — still counted, but please confirm (see below)
   - **NO MATCH** — couldn't confidently match this row; needs your judgement
4. **Every row is editable** in the app — if a match looks wrong, correct the CGHS code directly in the table. A row you type in by hand is matched automatically from its description or code. The **Treatment** column lets you overrule the rules for a single row: *Auto* (default), *Count* (pay it anyway) or *Exclude* (don't pay it).
5. Name and Employee ID (and every other field) can always be typed in manually if OCR misses them or gets them wrong.

## How double counting is prevented

Some CGHS codes are packages that already include tests which also have their own code — for example Complete Haemogram/CBC (`LB012`) covers Haemoglobin, TLC, DLC, Platelet count, ESR and Peripheral smear; the Kidney Function Test (`LB123`) covers Creatinine, Urea, Uric acid, Sodium, Potassium and Urine R/E; the Lipid Profile (`LB125`) covers Cholesterol, Triglycerides and LDL. When a lab prints the package *and* its component tests, paying every line pays for the same test twice. `bundles.py` knows about these overlaps (54 packages with 97 overlaps — 22 laboratory panels plus USG whole abdomen, growth scan and the contrast CT/MRI scans) and checks the whole claim at once, whatever order the rows are in. A package inside a package (Anaemia profile → CBC → Hb) is followed all the way.

The rules come in two strengths:

- **Listed** — the CGHS rate list itself names the component in the package's description. The component is counted as ₹0 by default (*INCLUDED IN PACKAGE*).
- **Customary** — CGHS doesn't spell the components out (e.g. "Liver Function Test"), or it's a billing convention (a contrast scan normally covers its own plain scan). The row stays in the total and is marked *REVIEW* for you to decide.

Other checks: the same *diagnostic* code billed twice is counted once (repeated consultations or procedures are only flagged, since those can genuinely repeat); and two codes for the same test by different methods (CRP / CRP quantitative, ANA / ANA-IFA, …) are flagged *REVIEW - SAME TEST BILLED TWICE*. A package only covers other rows if it is actually being paid — a package billed at ₹0, or one you set to *Exclude*, covers nothing. Nothing is final: **Treatment → Count / Exclude** overrules any row, and the NS/Checklist's calculation sheet prints the reason next to every held-back row.

**Limits to know about:** the rules are not date-aware (the same test done again on a different date looks like a repeat — set it to *Count*), and the lists in `bundles.py` cover the common packages, not every code in the rate list. To add one, add a line to `_PACKAGES` in `bundles.py` and run the tests; they check that every code you use exists in the rate list.

## Using the tool for another division

In **step 4**, open **Office / division details** and change the division name, office line, address, place, city & PIN, sanctioning authority (sentence and signature wording), Memo No. prefix, Head of Account, copy-to list and the NS/Checklist signature titles. The page header, the Sanction Memo and the NS/Checklist all follow. Use **Save these details** to download a small `.json` file and **Load saved details** next time (the app does not remember anything between sessions); **Reset to defaults** restores New Delhi Central Division's wording. Also set the beneficiary's city in the sidebar so the right CGHS tier (X / Y / Z) is used. The memo number's financial year follows April–March automatically.

To change the defaults permanently for your deployment, edit `DEFAULT_OFFICE` at the top of `letter_templates.py`.

## Running the tests

```bash
pip install pytest
python -m pytest tests/ -q
```

## Important limitations to know about

- **City tier selection is a dropdown you set, not auto-detected from the claim.** The sidebar has a "beneficiary's / treatment city" field that suggests Tier I / II / III based on a starter list of well-known cities (see `TIER1_CITIES` / `TIER2_CITIES` in `app.py`) — this list is not exhaustive, so **always confirm the tier is correct for the actual city** before sanctioning, especially for less common Y-tier cities. The official, exhaustive Y-city list is in Annexure I-B of the source CGHS rate memo if you need to check a specific city.
- **Ward entitlement:** the bundled rates are the semi-private-ward base rates. Per the CGHS rate memo, General Ward is 5% less and Private Ward is 5% more — this isn't automated yet, since the memo also says investigations/consultations/radiotherapy are uniform regardless of ward. If you regularly process indoor/surgical claims (where ward entitlement changes the rate), this is worth adding next.
- **Bundled/discounted lab bills:** some diagnostic labs quote a package rate lower than the sum of individual listed prices (as in the sample case used to build this). The app checks each *line item* against its CGHS rate and holds back tests that a billed CGHS package already includes, but it doesn't try to reconcile a lab's internal discounting logic. Always sanity-check the total against the actual amount paid on the receipt.
- **OCR quality depends on scan/photo clarity.** Handwritten forms are read reasonably well by the underlying model, but always verify the extracted fields, especially amounts, before sanctioning.
- **This tool never auto-approves anything.** It surfaces the correct rate next to whatever was claimed; the sanctioning decision remains with the processing officer, as it must.

## Extending the CGHS rate list

If CGHS issues a revised rate memo:
- The Excel format expected matches `CGHS_Rates_All_Tiers.xlsx`: `CGHS Code | Description | Classification | Tier I (X City) Non-NABH/NABH/Super Speciality | Tier II (Y City) ... | Tier III (Z City) ...`
- Upload it via the "Replace rate list" option in the sidebar, or replace `data/cghs_all_tiers.json` directly and redeploy.
