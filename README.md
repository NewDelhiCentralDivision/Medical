# CGHS Claim Checker & Sanction Letter Generator

Upload a scanned/photographed CGHS medical reimbursement claim (claim form,
bill, referral). The app reads it with an AI vision model, checks every
billed test/treatment against the official CGHS rate list — **for all
CGHS city tiers, not just Delhi** — flags anything billed above the CGHS
rate or double-billed, and generates the Sanction Memo + NS/Checklist note
used by this division — only the claimant's name, ID, and figures change
each time.

**This is a decision-support tool, not an auto-approver.** Every match,
rate, and total is shown for the processing officer to review and correct
before a case is sanctioned. Treat every output as a draft.

---

## What's included

| File | Purpose |
|---|---|
| `app.py` | The Streamlit app (UI, OCR call, matching, document generation) |
| `matcher.py` | Matches claimed line items to CGHS codes (exact code match first, fuzzy text match as fallback) |
| `letter_templates.py` | Generates the Sanction Memo and NS/Checklist `.docx` files from your existing wording |
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
3. Every row shows: what was claimed, the matched CGHS code and rate, the admissible amount (the lower of the two), and a flag:
   - **OK** — claimed amount is within the CGHS rate
   - **OVERCHARGED** — claimed above the CGHS rate (capped automatically for the total)
   - **POSSIBLE DUPLICATE** — the same CGHS code appears more than once in the bill (labs sometimes split one test into several internal line items — worth a second look so it isn't double-counted)
   - **NO MATCH** — couldn't confidently match this row; needs your judgement
4. **Every row is editable** in the app — if a match looks wrong, correct the CGHS code directly in the table.
5. Name and Employee ID (and every other field) can always be typed in manually if OCR misses them or gets them wrong.

## Important limitations to know about

- **City tier selection is a dropdown you set, not auto-detected from the claim.** The sidebar has a "beneficiary's / treatment city" field that suggests Tier I / II / III based on a starter list of well-known cities (see `TIER1_CITIES` / `TIER2_CITIES` in `app.py`) — this list is not exhaustive, so **always confirm the tier is correct for the actual city** before sanctioning, especially for less common Y-tier cities. The official, exhaustive Y-city list is in Annexure I-B of the source CGHS rate memo if you need to check a specific city.
- **Ward entitlement:** the bundled rates are the semi-private-ward base rates. Per the CGHS rate memo, General Ward is 5% less and Private Ward is 5% more — this isn't automated yet, since the memo also says investigations/consultations/radiotherapy are uniform regardless of ward. If you regularly process indoor/surgical claims (where ward entitlement changes the rate), this is worth adding next.
- **Bundled/discounted lab bills:** some diagnostic labs quote a package rate lower than the sum of individual listed prices (as in the sample case used to build this). The app checks each *line item* against its CGHS rate; it doesn't try to reconcile a lab's internal discounting logic. Always sanity-check the total against the actual amount paid on the receipt.
- **OCR quality depends on scan/photo clarity.** Handwritten forms are read reasonably well by the underlying model, but always verify the extracted fields, especially amounts, before sanctioning.
- **This tool never auto-approves anything.** It surfaces the correct rate next to whatever was claimed; the sanctioning decision remains with the processing officer, as it must.

## Extending the CGHS rate list

If CGHS issues a revised rate memo:
- The Excel format expected matches `CGHS_Rates_All_Tiers.xlsx`: `CGHS Code | Description | Classification | Tier I (X City) Non-NABH/NABH/Super Speciality | Tier II (Y City) ... | Tier III (Z City) ...`
- Upload it via the "Replace rate list" option in the sidebar, or replace `data/cghs_all_tiers.json` directly and redeploy.
