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

The app has four ways to read a claim - pick one in the sidebar:

| Option | Needs a key? | What it does |
|---|---|---|
| **Manual entry only** | No | Skip document upload entirely, type everything into the form yourself |
| **Local OCR (offline, free)** | No | Runs Tesseract on your own machine, shows raw extracted text to read alongside the original - does **not** auto-fill amounts (tested: it can misread a digit, e.g. turning ₹1,000 into ₹600) |
| **Anthropic (Claude)** | Yes | Reads the documents and auto-fills every field, including line items |
| **Google (Gemini)** | Yes | Same as above; Gemini has a free tier |

**Choosing a model.** For both cloud options the sidebar has a Model dropdown (and an *Other* entry for typing any model name that is newer than this list). Gemini choices, checked against Google's model pages on 15 Oct 2026: `gemini-3.8-flash` (default, fast), `gemini-4-argon` (newest and most capable, released 13 Oct 2026), `gemini-3.6-flash`, `gemini-3.5-flash-lite` (cheapest), `gemini-3.1-pro-preview` (preview) and `gemini-flash-latest` (a rolling alias). Google retires models regularly; if one ever stops working, pick another or use *Other*. The current list is at [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models). Claude choices: `claude-sonnet-5-5` (default), `claude-haiku-5-5`, `claude-opus-5-5`, `claude-fable-5-1`.

If you don't want to deal with any API key at all, pick **Manual entry only** and just type in the claimant details and each billed item directly - the rate check, flagging, and letter generation work exactly the same regardless of how the data got into the form. **Local OCR** is a middle ground: it saves you re-typing whole documents by hand by giving you the raw text to copy from, without trusting a machine to read money amounts unsupervised.

If you do want auto-fill and are willing to use a cloud AI model:

1. **Get an API key:**
   - Claude: sign up at [console.anthropic.com](https://console.anthropic.com), create a key.
   - Gemini: sign up at [aistudio.google.com/apikey](https://aistudio.google.com/apikey), create a free key.

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   You'll also need two system programs installed (not just the Python packages):
   - **poppler** (for reading PDF pages):
     - Windows: download poppler binaries, add the `bin` folder to your PATH ([guide](https://github.com/oschwartz10612/poppler-windows/releases))
     - Mac: `brew install poppler`
     - Linux: `sudo apt install poppler-utils`
   - **tesseract** (only needed if you use the Local OCR option):
     - Windows: install from the [UB-Mannheim build](https://github.com/UB-Mannheim/tesseract/wiki), add it to your PATH
     - Mac: `brew install tesseract`
     - Linux: `sudo apt install tesseract-ocr`

3. **Save your key so you don't have to retype it every time you run the app locally.** Create a file at `.streamlit/secrets.toml` (a hidden folder, right next to `app.py` - copy `.streamlit/secrets.toml.example` and rename it) with one of:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
   or
   ```toml
   GEMINI_API_KEY = "your-gemini-key"
   ```
   The app checks this file automatically and pre-fills the sidebar field. (This file is already in `.gitignore` so it won't accidentally get pushed to GitHub.) If you skip this step, you can still just paste the key into the sidebar each session — it's never saved anywhere by the app itself.

4. **Run it:**
   ```bash
   streamlit run app.py
   ```
   It opens in your browser at `http://localhost:8501`. Pick your reading option at the top of the sidebar, upload the claim documents (if applicable), and go.

## Deploying to Streamlit Community Cloud (like PostBuddy)

1. Push this folder to a GitHub repo (the included `packages.txt` tells Streamlit Cloud to install `poppler-utils` automatically — you don't need to do anything extra for that).
2. On [share.streamlit.io](https://share.streamlit.io), point a new app at the repo, main file `app.py`.
3. Instead of typing your API key into the sidebar every time, add it as a **Streamlit secret**: in the app's settings → Secrets, add whichever provider you're using:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
   and/or
   ```toml
   GEMINI_API_KEY = "your-gemini-key"
   ```
   The app reads these automatically if the sidebar field is left blank — same mechanism as the local `secrets.toml` file above.

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
