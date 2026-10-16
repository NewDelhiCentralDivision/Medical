"""
CGHS Claim Checker & Sanction Letter Generator
------------------------------------------------
Upload a scanned/photographed CGHS medical reimbursement claim (form, bill,
referral). This tool reads it with an AI vision model, matches every claimed
test/treatment against the official CGHS rate list, flags any amount billed
above the CGHS rate, and generates the Sanction Memo + NS/Checklist note
used by this division -- with only the claimant details changing each time.

Run:  streamlit run app.py
"""
import os
import io
import json
import base64
import datetime
import tempfile

import streamlit as st
import pandas as pd

from matcher import match_line_item, get_rate, extract_cghs_code, TIER_KEYS
from letter_templates import build_sanction_memo, build_ns_checklist, DEFAULT_OFFICE
from bundles import (
    apply_billing_rules, summarise, validate_rules, OVERRIDE_CHOICES, OVERRIDE_AUTO,
    COUNTED, INCLUDED, REPEAT, FORCED_EXCLUDE, UNMATCHED,
)
from num2words_inr import rupees_in_words

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
MASTER_JSON = os.path.join(DATA_DIR, 'cghs_all_tiers.json')

# A starter list of well-known CGHS Tier I (X) and Tier II (Y) cities so the
# dropdown can suggest the right tier - anything not in these lists defaults
# to Tier III (Z), which covers "all other" CGHS-covered cities.
TIER1_CITIES = ["Delhi", "Mumbai", "Kolkata", "Chennai", "Hyderabad", "Bengaluru", "Ahmedabad", "Pune"]
TIER2_CITIES = [
    "Chandigarh", "Jaipur", "Lucknow", "Kanpur", "Nagpur", "Kochi", "Guwahati", "Patna",
    "Surat", "Faridabad", "Gurgaon", "Srinagar", "Jammu", "Indore", "Bhopal", "Coimbatore",
    "Ludhiana", "Amritsar", "Meerut", "Agra", "Varanasi", "Noida", "Vadodara", "Rajkot",
]

st.set_page_config(page_title="CGHS Claim Checker", page_icon="🩺", layout="wide")

# ---------------------------------------------------------------------------
# Office / division details (edited in step 4, saved in session state so the page
# header and the generated documents always agree). Every field has a key
# "office_<field>"; copy_to is kept as one-line-per-entry text.
# ---------------------------------------------------------------------------
def _office_default_text(field):
    v = DEFAULT_OFFICE[field]
    return "\n".join(v) if isinstance(v, list) else v

for _f in DEFAULT_OFFICE:
    st.session_state.setdefault(f"office_{_f}", _office_default_text(_f))


def current_office_profile():
    """The office settings as currently typed into step 4."""
    prof = {f: st.session_state.get(f"office_{f}", _office_default_text(f)) for f in DEFAULT_OFFICE}
    prof["copy_to"] = [ln.strip() for ln in str(prof["copy_to"]).splitlines() if ln.strip()]
    return prof


def fiscal_year_label(today=None):
    """Indian financial year, April-March: 15 Oct 2026 -> '2026-27', 10 Feb 2027 -> '2026-27'."""
    today = today or datetime.date.today()
    start = today.year if today.month >= 4 else today.year - 1
    return f"{start}-{str(start + 1)[-2:]}"

# ---------------------------------------------------------------------------
# Sidebar: API key + rate list + city/tier
# ---------------------------------------------------------------------------
st.sidebar.title("Setup")

def _get_secret(name):
    """Check st.secrets first (works for local secrets.toml and Streamlit Cloud secrets), then env vars."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.environ.get(name, "")


def _clean_key(k):
    """Strip whitespace/quotes - a stray space or quote copied with a key is a common cause of 'key not valid'."""
    return (k or "").strip().strip('"').strip("'").strip()


# The API keys are set ONCE by whoever deploys the tool (Streamlit secrets, a local
# .streamlit/secrets.toml, or an environment variable). Officers never see or type a key.
# They are deliberately NOT written into the source code: this repository is public, and
# Google/Anthropic automatically disable any key they find in a public repo.
GEMINI_KEY = _clean_key(_get_secret("GEMINI_API_KEY") or _get_secret("GOOGLE_API_KEY"))
CLAUDE_KEY = _clean_key(_get_secret("ANTHROPIC_API_KEY"))

P_GEMINI = "Google (Gemini)"
P_CLAUDE = "Anthropic (Claude)"
P_OCR = "Local OCR (offline, free - Tesseract)"
P_MANUAL = "Manual entry only - no OCR, no key needed"
PROVIDER_LABELS = {
    P_GEMINI: "🤖 Auto-read with AI (Gemini)",
    P_CLAUDE: "🤖 Auto-read with AI (Claude)",
    P_OCR: "📝 Offline OCR - raw text only",
    P_MANUAL: "⌨️ Type everything manually",
}
provider_options = ([P_GEMINI] if GEMINI_KEY else []) + ([P_CLAUDE] if CLAUDE_KEY else []) + [P_OCR, P_MANUAL]

st.sidebar.subheader("1 · How to read the documents")
provider = st.sidebar.radio(
    "Reading mode", provider_options, index=0, format_func=lambda p: PROVIDER_LABELS[p],
    label_visibility="collapsed",
    help="AI reading needs the tool's built-in key. Offline OCR and manual typing need no key and send nothing anywhere.",
)

api_key = ""
model_name = ""

# Model lists for the dropdowns. IDs are the exact API model names. The "Other" entry
# at the end of every list lets you type a name that isn't listed yet, so a new
# release never needs a code change.
CLAUDE_MODELS = {
    "claude-sonnet-5-5": "claude-sonnet-5-5 - balanced (recommended)",
    "claude-haiku-5-5": "claude-haiku-5-5 - fastest / cheapest",
    "claude-opus-5-5": "claude-opus-5-5 - stronger",
    "claude-fable-5-1": "claude-fable-5-1 - top tier",
}
GEMINI_MODELS = {
    "gemini-3.8-flash": "gemini-3.8-flash - fast (recommended)",
    "gemini-4-argon": "gemini-4-argon - newest, most capable (Oct 2026)",
    "gemini-3.6-flash": "gemini-3.6-flash",
    "gemini-3.5-flash-lite": "gemini-3.5-flash-lite - cheapest",
    "gemini-3.1-pro-preview": "gemini-3.1-pro-preview (preview)",
    "gemini-flash-latest": "gemini-flash-latest - rolling alias",
}
OTHER_MODEL = "Other (type a model name)..."


def model_picker(label, models, default, key, help_text):
    """Sidebar dropdown of known model IDs, with an 'Other' escape hatch for anything newer."""
    options = list(models) + [OTHER_MODEL]
    choice = st.sidebar.selectbox(
        label, options, index=options.index(default), key=key, help=help_text,
        format_func=lambda m: models.get(m, m),
    )
    if choice == OTHER_MODEL:
        return st.sidebar.text_input("Model name", value="", key=f"{key}_custom").strip()
    return choice


if provider == P_MANUAL:
    st.sidebar.caption("Nothing is read automatically - type the details in sections 2 and 3. All checks work the same.")
elif provider == P_OCR:
    st.sidebar.caption(
        "Runs on this machine and sends nothing anywhere. Plain OCR can misread digits in amounts, so it "
        "only shows the raw text beside the scan - you type the confirmed values yourself. "
        "Needs the `tesseract` program installed (see README)."
    )
elif provider == P_CLAUDE:
    api_key = CLAUDE_KEY
    model_name = model_picker(
        "Model", CLAUDE_MODELS, "claude-sonnet-5-5", key="claude_model",
        help_text="claude-sonnet-5-5 is a good balance of accuracy and cost for reading scanned bills. "
                  "Pick 'Other' to type any model name; docs.claude.com has the up to date list."
    )
else:
    api_key = GEMINI_KEY
    model_name = model_picker(
        "Model", GEMINI_MODELS, "gemini-3.8-flash", key="gemini_model",
        help_text="List checked against Google's model pages on 15 Oct 2026. gemini-3.8-flash is the "
                  "recommended fast model; gemini-4-argon is the newest and most capable; "
                  "gemini-3.5-flash-lite is the cheapest. 'gemini-flash-latest' is a rolling alias. "
                  "If a model ever stops working, pick another, or choose 'Other' and type a name from "
                  "https://ai.google.dev/gemini-api/docs/models"
    )

if not GEMINI_KEY and not CLAUDE_KEY:
    st.sidebar.caption(
        "ℹ️ AI reading is switched off because no API key has been set up for this tool yet. "
        "The administrator can enable it once - see the README (\"One-time key setup\")."
    )

if api_key:
    if st.sidebar.button("Check AI connection"):
        try:
            if not model_name:
                raise ValueError("choose a model first (or type a model name under 'Other')")
            if provider == P_CLAUDE:
                import anthropic
                # Keep the client in a variable: if it is only a temporary, Python can discard it
                # mid-call and the request fails with "client has been closed".
                client = anthropic.Anthropic(api_key=api_key)
                client.messages.create(
                    model=model_name, max_tokens=10, messages=[{"role": "user", "content": "Say OK"}])
            else:
                from google import genai
                client = genai.Client(api_key=api_key)
                client.models.generate_content(model=model_name, contents=["Say OK"])
            st.sidebar.success("AI connection works.")
        except Exception as e:
            st.sidebar.error(f"Connection check failed: {e}")

st.sidebar.markdown("---")
with st.sidebar.expander("⚙️ Advanced: replace CGHS rate list"):
    st.caption(
        "Bundled: all three city tiers (X/Y/Z), effective 13.10.2025, 1,998 codes each. "
        "Upload a replacement JSON/Excel only if CGHS issues a revision."
    )
    custom_master = st.file_uploader("Replace rate list (optional)", type=["xlsx", "json"])

@st.cache_data
def get_master(_uploaded_bytes, _uploaded_name):
    if _uploaded_bytes is not None:
        if _uploaded_name.endswith('.json'):
            data = json.loads(_uploaded_bytes)
        else:
            df = pd.read_excel(io.BytesIO(_uploaded_bytes))
            # Expect the multi-tier column layout from CGHS_Rates_All_Tiers.xlsx
            data = []
            for _, row in df.iterrows():
                data.append({
                    'code': row['CGHS Code'],
                    'description': row['Description'],
                    'classification': row.get('Classification', ''),
                    'tiers': {
                        'tier1_x_city': {
                            'rate_non_nabh': row.get('Tier I (X City) Non-NABH'),
                            'rate_nabh': row.get('Tier I (X City) NABH'),
                            'rate_super_speciality': row.get('Tier I (X City) Super Speciality'),
                        },
                        'tier2_y_city': {
                            'rate_non_nabh': row.get('Tier II (Y City) Non-NABH'),
                            'rate_nabh': row.get('Tier II (Y City) NABH'),
                            'rate_super_speciality': row.get('Tier II (Y City) Super Speciality'),
                        },
                        'tier3_z_city': {
                            'rate_non_nabh': row.get('Tier III (Z City) Non-NABH'),
                            'rate_nabh': row.get('Tier III (Z City) NABH'),
                            'rate_super_speciality': row.get('Tier III (Z City) Super Speciality'),
                        },
                    }
                })
        by_code = {r['code']: r for r in data}
        descriptions = {r['code']: r['description'] for r in data}
        return data, by_code, descriptions

    if not os.path.exists(MASTER_JSON):
        # Show exactly what IS present so the missing-file cause is obvious, instead of a bare crash.
        app_dir = os.path.dirname(__file__)
        try:
            app_dir_listing = os.listdir(app_dir)
        except Exception as e:
            app_dir_listing = [f"(could not list {app_dir}: {e})"]
        try:
            data_dir_listing = os.listdir(DATA_DIR) if os.path.isdir(DATA_DIR) else None
        except Exception as e:
            data_dir_listing = [f"(could not list {DATA_DIR}: {e})"]

        st.error(
            "**Can't find the bundled CGHS rate file** at:\n\n"
            f"`{MASTER_JSON}`\n\n"
            "This means the `data/` folder wasn't pushed to your GitHub repo (or ended up in the "
            "wrong place), so Streamlit Cloud has nothing to load.\n\n"
            f"**Contents of the app folder** (`{app_dir}`):\n"
            f"{app_dir_listing}\n\n"
            f"**Contents of the expected data folder** (`{DATA_DIR}`):\n"
            f"{data_dir_listing if data_dir_listing is not None else 'this folder does not exist at all'}\n\n"
            "**To fix:** in your GitHub repo, confirm there is a `data/` folder sitting right next to "
            "`app.py`, containing `cghs_all_tiers.json` (and `CGHS_Rates_All_Tiers.xlsx`). If it's "
            "missing, re-upload that folder to GitHub (drag the whole `data` folder into the repo in "
            "the GitHub web UI, or use `git add data/ && git commit && git push` from the command line), "
            "then reboot the app from 'Manage app'.\n\n"
            "As a temporary workaround, you can also use the **'Replace rate list'** uploader in the "
            "sidebar to upload `CGHS_Rates_All_Tiers.xlsx` directly for this session."
        )
        st.stop()

    with open(MASTER_JSON) as f:
        data = json.load(f)
    by_code = {r['code']: r for r in data}
    descriptions = {r['code']: r['description'] for r in data}
    return data, by_code, descriptions

uploaded_bytes = custom_master.read() if custom_master else None
uploaded_name = custom_master.name if custom_master else None
MASTER_DATA, BY_CODE, DESCRIPTIONS = get_master(uploaded_bytes, uploaded_name)
st.sidebar.success(f"{len(MASTER_DATA)} CGHS codes loaded (all 3 tiers)")
_rule_problems = validate_rules(BY_CODE)
if _rule_problems:
    # Only happens with a replacement rate list that renumbers or drops codes the
    # package rules rely on - the rules for those codes then can't be trusted.
    st.sidebar.warning(
        f"{len(_rule_problems)} package rule(s) refer to codes missing from this rate list, so the "
        "'already included in package' check may miss overlaps. First one: " + _rule_problems[0]
    )

st.sidebar.markdown("---")
st.sidebar.subheader("2 · City / tier")
city_input = st.sidebar.text_input("Beneficiary's / treatment city", value="Delhi")

def suggest_tier(city):
    c = city.strip().lower()
    if any(c == t.lower() for t in TIER1_CITIES):
        return "Tier I (X City)"
    if any(c == t.lower() for t in TIER2_CITIES):
        return "Tier II (Y City)"
    return "Tier III (Z City)"

suggested = suggest_tier(city_input)
tier_label = st.sidebar.selectbox(
    "CGHS city tier for rate lookup",
    list(TIER_KEYS.keys()),
    index=list(TIER_KEYS.keys()).index(suggested),
    help="Auto-suggested from the city typed above - override if needed. "
         "Tier I = X-city (e.g. Delhi, Mumbai), Tier II = Y-city, Tier III = Z-city (all other CGHS-covered cities)."
)
if tier_label != suggested:
    st.sidebar.caption(f"Note: '{city_input}' would normally suggest {suggested}.")

st.title("🩺 CGHS Claim Checker & Sanction Letter Generator")
_division_name = (st.session_state.get("office_division") or "").strip()
st.caption(f"{_division_name + ' — ' if _division_name else ''}decision-support tool. "
           "Every match and amount must be reviewed by the processing officer before a case is sanctioned.")

st.markdown(
    "**How to use:** &nbsp;① pick how to read the documents (sidebar) &nbsp;→&nbsp; ② upload the bills and click "
    "*Extract* &nbsp;→&nbsp; ③ check the claimant and item tables &nbsp;→&nbsp; ④ download the memo and checklist."
)

if "extracted" not in st.session_state:
    st.session_state.extracted = None
if "line_items" not in st.session_state:
    st.session_state.line_items = None

# ---------------------------------------------------------------------------
# Step 1: Upload claim documents
# ---------------------------------------------------------------------------
st.header("1. Upload claim documents")

if provider == P_MANUAL:
    st.info(
        "You've chosen manual entry - no documents need to be uploaded here. "
        "Skip down to **2. Claimant details** and **3. Item-wise CGHS rate check** and type "
        "everything in directly from the papers in front of you."
    )
    uploaded_files = None
    run_ocr = False
else:
    st.write("Upload the claim form, bill(s)/receipt, and referral if available. Photos, scans, or PDFs are all fine.")
    uploaded_files = st.file_uploader(
        "Claim documents",
        type=["png", "jpg", "jpeg", "pdf"],
        accept_multiple_files=True
    )
    button_label = ("📝 Read text (offline, no auto-fill)" if provider == P_OCR
                    else "🔍 Extract claim details")
    run_ocr = st.button(button_label, type="primary", disabled=not uploaded_files)

def file_to_pil_images(f):
    """Convert an uploaded file (image or PDF) into a list of PIL Image objects."""
    from PIL import Image
    raw = f.read()
    images = []
    if f.type == "application/pdf" or f.name.lower().endswith(".pdf"):
        from pdf2image import convert_from_bytes
        images.extend(convert_from_bytes(raw, dpi=200))
    else:
        images.append(Image.open(io.BytesIO(raw)))
    return images

EXTRACTION_PROMPT = """You are reading scanned/photographed CGHS medical reimbursement claim documents
(claim form, bill/receipt, doctor's referral, CGHS card). Extract the following as strict JSON, with
no commentary before or after:

{
  "claimant_name": string or null,
  "employee_id": string or null,
  "designation": string or null,
  "office": string or null,
  "pin": string or null,
  "cghs_beneficiary_id": string or null,
  "cghs_card_validity": string or null,
  "patient_relation": string or null,        // e.g. "SELF", "SPOUSE", "SON" etc.
  "hospital_or_lab_name": string or null,
  "nabh_status": string or null,             // "NABH", "Non-NABH", or "Super Speciality" if stated/visible, else null
  "hco_type": string or null,                // e.g. "CGHS empanelled" / "Private" / "Govt"
  "submission_date": string or null,         // DD.MM.YYYY as printed
  "treatment_type": string or null,          // e.g. "OPD", "Indoor", "TEST/INVESTIGATION", "Emergency"
  "total_claimed_amount": number or null,
  "line_items": [
    {
      "description": string,          // the test/treatment name exactly as printed
      "code": string or null,         // any lab/CGHS code printed alongside it, if visible
      "claimed_amount": number
    }
  ]
}

Read every page provided. If a field is not present anywhere, use null. For line_items, include every
distinct billed test/treatment/procedure row you can find, with its exact price as printed - including
rows priced at 0. Do not invent values. Output ONLY the JSON object."""

def _clean_json_text(raw_text):
    raw_text = raw_text.strip().strip("`")
    if raw_text.lower().startswith("json"):
        raw_text = raw_text[4:].strip()
    return raw_text

def extract_with_anthropic(images, api_key, model_name):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    content_blocks = []
    for im in images:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        content_blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}})
    content_blocks.append({"type": "text", "text": EXTRACTION_PROMPT})

    resp = client.messages.create(
        model=model_name,
        max_tokens=4000,
        messages=[{"role": "user", "content": content_blocks}]
    )
    raw_text = "".join(b.text for b in resp.content if b.type == "text")
    return json.loads(_clean_json_text(raw_text))

def extract_with_gemini(images, api_key, model_name):
    from google import genai
    client = genai.Client(api_key=api_key)
    contents = list(images) + [EXTRACTION_PROMPT]
    resp = client.models.generate_content(model=model_name, contents=contents)
    return json.loads(_clean_json_text(resp.text))

def run_local_ocr(images):
    """Offline OCR via Tesseract. Returns raw text per image - intentionally NOT
    parsed into structured fields, since digit misreads in amounts are a real risk
    (tested: a real Rs.1000 was misread as Rs.600). This is a reading aid only."""
    import pytesseract
    return [pytesseract.image_to_string(im) for im in images]

if provider == P_OCR and run_ocr:
    try:
        images = []
        for f in uploaded_files:
            images.extend(file_to_pil_images(f))
        with st.spinner("Running offline OCR..."):
            texts = run_local_ocr(images)
        st.warning(
            "This is raw, unverified text straight off the page - it **will** contain misread "
            "characters, especially in numbers. Read it side by side with the original document "
            "and type the confirmed values into the sections below yourself; nothing here is "
            "auto-filled."
        )
        for i, t in enumerate(texts):
            with st.expander(f"Page {i + 1} - raw OCR text"):
                st.text(t)
    except Exception as e:
        st.error(f"Local OCR failed: {e}")
        st.info(
            "This usually means the `tesseract` program itself isn't installed on this machine "
            "(the Python package alone isn't enough) - see the README for the one-line install "
            "command for your OS."
        )

if provider in (P_CLAUDE, P_GEMINI) and run_ocr:
    if not api_key:
        st.error("AI reading is not set up on this installation (no API key configured). Choose another reading mode in the sidebar.")
    else:
        with st.spinner(f"Reading documents with {provider}..."):
            try:
                images = []
                for f in uploaded_files:
                    images.extend(file_to_pil_images(f))

                if provider == P_CLAUDE:
                    extracted = extract_with_anthropic(images, api_key, model_name)
                else:
                    extracted = extract_with_gemini(images, api_key, model_name)

                st.session_state.extracted = extracted
                st.session_state.line_items = extracted.get("line_items", [])
                st.success("Extraction complete. Review and correct the details below.")
            except Exception as e:
                err_text = str(e)
                if "API_KEY_INVALID" in err_text or "API key not valid" in err_text or "invalid x-api-key" in err_text.lower():
                    st.error(
                        "**The tool's built-in API key was rejected.** Nothing is wrong with your claim documents - "
                        "please tell the administrator. They need to replace the key in the app's secrets "
                        "(a Google AI Studio key from aistudio.google.com/apikey, with no spaces or quotes). "
                        "Meanwhile, switch the reading mode in the sidebar to manual typing and carry on.\n\n"
                        f"Raw error: `{err_text}`"
                    )
                elif "NOT_FOUND" in err_text or "404" in err_text or "no longer available" in err_text.lower():
                    st.error(
                        f"**The model name `{model_name}` isn't available anymore** - {provider.split()[0]} "
                        "retires specific model versions on a rolling basis, sometimes with little notice.\n\n"
                        + ("Pick another model in the sidebar - `gemini-3.8-flash`, or `gemini-flash-latest` "
                           "(a rolling alias that should always point to a current working model) - or check "
                           "[ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models) "
                           "for the current model list."
                           if provider == P_GEMINI else
                           "Pick another model in the sidebar (e.g. `claude-sonnet-5-5`) or check "
                           "[docs.claude.com](https://docs.claude.com) for the current model list.")
                        + f"\n\nRaw error: `{err_text}`"
                    )
                else:
                    st.error(f"Extraction failed: {e}")
                st.info("You can still fill in the details manually below.")
                st.session_state.extracted = {}
                st.session_state.line_items = []

# ---------------------------------------------------------------------------
# Step 2: Review / correct claimant details (manual override always available)
# ---------------------------------------------------------------------------
st.header("2. Claimant details")
st.caption("If OCR could not read the name or employee ID, fill them in here manually - everything on this page is editable.")

ex = st.session_state.extracted or {}
col1, col2, col3 = st.columns(3)
with col1:
    name = st.text_input("Claimant name", value=ex.get("claimant_name") or "")
    designation = st.text_input("Designation", value=ex.get("designation") or "")
    office = st.text_input("Office", value=ex.get("office") or "")
with col2:
    emp_id = st.text_input("Employee ID", value=ex.get("employee_id") or "")
    pin = st.text_input("PIN", value=ex.get("pin") or "")
    patient_relation = st.text_input("Patient relation to official", value=ex.get("patient_relation") or "SELF")
with col3:
    hospital = st.text_input("Hospital / Diagnostic Centre", value=ex.get("hospital_or_lab_name") or "")
    cghs_id = st.text_input("CGHS Beneficiary ID", value=ex.get("cghs_beneficiary_id") or "")
    cghs_validity = st.text_input("CGHS card validity", value=ex.get("cghs_card_validity") or "")

col4, col5, col6 = st.columns(3)
with col4:
    _accred_options = ["NABH", "Non-NABH", "Super Speciality"]
    _ex_accred = ex.get("nabh_status") or "NABH"
    nabh_status = st.selectbox("Facility accreditation", _accred_options,
                                index=_accred_options.index(_ex_accred) if _ex_accred in _accred_options else 0,
                                help="Super Speciality rates apply to CGHS-recognised super-speciality "
                                     "hospitals/centres - check the empanelment status on the hospital's "
                                     "CGHS listing if unsure.")
with col5:
    submission_date = st.text_input("Claim submission date", value=ex.get("submission_date") or "")
with col6:
    treatment_type = st.text_input("Treatment type", value=ex.get("treatment_type") or "TEST/INVESTIGATION")

# ---------------------------------------------------------------------------
# Step 3: Line-item matching against CGHS rates
# ---------------------------------------------------------------------------
st.header("3. Item-wise CGHS rate check")


def _num(x):
    """Cell value -> a clean number (blank / NaN -> 0; 300.0 -> 300)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0
    if pd.isna(v):
        return 0
    return int(v) if v == int(v) else round(v, 2)


def _txt(x):
    """Cell value -> stripped text (None / NaN -> '')."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    return str(x).strip()


items_raw = st.session_state.line_items or []
if not items_raw:
    st.info("No line items extracted yet. Run extraction above, or add rows manually in the table below.")
    items_raw = [{"description": "", "code": "", "claimed_amount": 0}]

st.caption(f"Rate lookup: **{tier_label}**, **{nabh_status}** (change in the sidebar if needed)")

matched = [match_line_item(it, BY_CODE, DESCRIPTIONS, tier_label=tier_label, accreditation=nabh_status) for it in items_raw]

df = pd.DataFrame([{
    'Description (as billed)': it.get('description', ''),
    'Billed code': it.get('code', '') or '',
    'Matched CGHS code': it.get('matched_code') or '',
    'Claimed (₹)': it.get('claimed_amount') or 0,
    'Treatment': OVERRIDE_AUTO,
    'Match check': it.get('flag') or '',
} for it in matched])

st.write(
    "Review every row. If a match looks wrong, correct the **Matched CGHS code** - the calculation sheet "
    "underneath updates straight away. A row you type in by hand is matched automatically from its "
    "description or code. Leave **Treatment** on *Auto* unless you need to overrule the billing rules for one row."
)
edited_df = st.data_editor(
    df,
    num_rows="dynamic",
    width="stretch",
    column_config={
        "Match check": st.column_config.TextColumn(
            disabled=True,
            help="How confidently this row was matched to a CGHS code when it was first read. "
                 "The final result for every row is in the calculation sheet below."),
        "Treatment": st.column_config.SelectboxColumn(
            options=OVERRIDE_CHOICES, default=OVERRIDE_AUTO, required=True,
            help="Auto = apply the billing rules. Count = pay this row even if it looks like a repeat or part "
                 "of a package. Exclude = do not pay this row."),
        "Claimed (₹)": st.column_config.NumberColumn(min_value=0, format="%.0f"),
    },
)

# Work out the final rows from the (possibly edited) table: re-match anything typed by hand,
# cap each row at its CGHS rate, then let the billing rules decide what really counts.
rows_final = []
for _, row in edited_df.iterrows():
    desc_billed = _txt(row.get('Description (as billed)'))
    billed_code = _txt(row.get('Billed code'))
    claimed = _num(row.get('Claimed (₹)'))
    code = extract_cghs_code(_txt(row.get('Matched CGHS code')), valid_codes=BY_CODE)
    if not code and (desc_billed or billed_code):
        auto = match_line_item({'description': desc_billed, 'code': billed_code, 'claimed_amount': claimed},
                               BY_CODE, DESCRIPTIONS, tier_label=tier_label, accreditation=nabh_status)
        code = auto.get('matched_code')
    if not (desc_billed or billed_code or code or claimed):
        continue  # an empty spare row
    rate = get_rate(BY_CODE[code], tier_label, nabh_status) if code else None
    rows_final.append({
        'description': desc_billed,
        'billed_code': billed_code,
        'code': code,
        'claimed_amount': claimed,
        'cghs_rate': rate,
        'admissible_amount': (min(claimed, rate) if rate is not None else None),
        'matched_description': BY_CODE[code]['description'] if code else desc_billed,
        'override': _txt(row.get('Treatment')) or OVERRIDE_AUTO,
    })

final_items = apply_billing_rules(rows_final, BY_CODE)


def _result_note(r):
    note = r['rule_note']
    if r['cghs_rate'] is not None and r['claimed_amount'] > r['cghs_rate']:
        capped = f"Billed ₹{r['claimed_amount']:,} is above the CGHS rate ₹{r['cghs_rate']:,} - restricted to the rate."
        note = f"{note} {capped}".strip()
    return note


sheet = pd.DataFrame([{
    'Sl.': i + 1,
    'Particulars': r['matched_description'],
    'CGHS code': r['code'] or '-',
    'Claimed (₹)': r['claimed_amount'],
    'CGHS rate (₹)': r['cghs_rate'],
    'Counted (₹)': r['counted_amount'],
    'Result': r['rule_status'],
    'Note': _result_note(r),
} for i, r in enumerate(final_items)])


def _row_colour(row):
    status = row['Result']
    if status in (INCLUDED, REPEAT, FORCED_EXCLUDE):
        bg = 'rgba(220, 53, 69, 0.18)'       # held back
    elif status == UNMATCHED or str(status).startswith('REVIEW'):
        bg = 'rgba(255, 193, 7, 0.22)'       # needs the officer's attention
    else:
        bg = ''
    return [f'background-color: {bg}' if bg else '' for _ in row]


st.subheader("Calculation sheet")
if len(sheet):
    st.dataframe(sheet.style.apply(_row_colour, axis=1), width="stretch", hide_index=True)
else:
    st.caption("Nothing to calculate yet - add at least one billed item above.")

st.caption(
    "COUNTED = payable (capped at the CGHS rate) · INCLUDED IN PACKAGE = already covered by a package that is also "
    "billed (e.g. Hb, TLC and Platelets inside a CBC), counted as ₹0 · REPEAT = the same diagnostic code billed twice, "
    "counted once · REVIEW = counted for now, but please confirm · NO MATCH = not found in the rate list, verify the "
    "code or description manually."
)

total_claimed = sum(r['claimed_amount'] for r in final_items)
total_admissible = sum(r['counted_amount'] for r in final_items if r['counted_amount'] is not None)
unresolved = [r for r in final_items if r['counted_amount'] is None]
summary = summarise(final_items)

m1, m2, m3 = st.columns(3)
m1.metric("Total claimed", f"₹{total_claimed:,.0f}")
m2.metric("Total admissible (as per CGHS rates)", f"₹{total_admissible:,.0f}")
diff = total_claimed - total_admissible
m3.metric("Difference", f"₹{diff:,.0f}", delta=f"-₹{diff:,.0f}" if diff > 0 else None, delta_color="inverse")

if summary['excluded']:
    st.info(
        f"🧮 {summary['excluded']} row(s) are already covered by another billed test or package, or are repeats, "
        f"so they are not paid twice (₹{summary['held_back_amount']:,.0f} held back). The reason is in the **Note** "
        "column. Disagree? Set that row's **Treatment** to *Count*."
    )
if summary['review']:
    st.warning(
        f"🔎 {summary['review']} row(s) are marked REVIEW. They are still counted, but please confirm each one - "
        "set **Treatment** to *Exclude* to drop a row."
    )
if unresolved:
    st.warning(f"{len(unresolved)} row(s) have no matched CGHS rate and are excluded from the admissible total above. "
               "Resolve these before sanctioning.")

st.markdown("---")

# ---------------------------------------------------------------------------
# Step 4: Generate documents
# ---------------------------------------------------------------------------
st.header("4. Generate Sanction Memo & NS/Checklist")


def _apply_office_profile(data):
    """Copy a dict of office settings into the step-4 widgets' session state."""
    for f in DEFAULT_OFFICE:
        if f in data:
            v = data[f]
            st.session_state[f"office_{f}"] = "\n".join(str(x) for x in v) if isinstance(v, list) else str(v)


def _load_office_profile():
    up = st.session_state.get("office_profile_upload")
    if up is None:
        return
    try:
        data = json.loads(up.getvalue().decode("utf-8"))
        if not isinstance(data, dict) or not any(k in DEFAULT_OFFICE for k in data):
            raise ValueError("this doesn't look like a saved office-details file")
        _apply_office_profile(data)
        st.session_state["office_msg"] = ("ok", f"Loaded office details for {data.get('division') or 'the file'}.")
    except Exception as e:
        st.session_state["office_msg"] = ("err", f"Couldn't read that file: {e}")


def _reset_office_profile():
    _apply_office_profile(DEFAULT_OFFICE)
    st.session_state["office_msg"] = ("ok", f"Reset to {DEFAULT_OFFICE['division']} defaults.")


office_now = current_office_profile()
st.caption(f"Documents will be issued for: **{office_now['division'] or '(division name not set)'}**. "
           "Using this tool for another division? Edit the office details below.")

with st.expander("🏢 Office / division details - edit these to use the tool for another division", expanded=False):
    st.caption(
        "Everything here is printed on the Sanction Memo and the NS/Checklist (letterhead, sanction sentence, "
        "signature block, copy-to list, memo number). The defaults are this division's original wording."
    )
    _msg = st.session_state.pop("office_msg", None)
    if _msg:
        (st.success if _msg[0] == "ok" else st.error)(_msg[1])

    o1, o2, o3 = st.columns(3)
    o1.text_input("Department", key="office_dept_name")
    o2.text_input("Office line (printed above the division name)", key="office_office_line",
                  help='For example: "O/o The Senior Superintendent of Post Offices,"')
    o3.text_input("Division / office name", key="office_division",
                  help="For example: North Division, East Division, West Division ...")
    o4, o5, o6 = st.columns(3)
    o4.text_input("Office address", key="office_address",
                  help="Printed under the division name. Leave blank to leave the line out.")
    o5.text_input("Place (for 'Dated at ... the')", key="office_place")
    o6.text_input("City & PIN (sanction sentence and signature block)", key="office_city_pin")
    o7, o8, o9 = st.columns(3)
    o7.text_input("Sanctioning authority - signature block", key="office_sanctioning_authority")
    o8.text_input("Sanctioning authority - in the sanction sentence", key="office_sanctioning_authority_long")
    o9.text_input("Memo No. prefix", key="office_memo_no_prefix",
                  help="The financial year is added automatically, e.g. <prefix>/2026-27")
    st.text_input("Head of Account (HOA)", key="office_hoa",
                  help="Leave blank to leave the 'expenditure will be met from ...' line out.")
    st.text_area("Copy-to list - one line per entry (the claimant is always No. 1)", key="office_copy_to", height=110)
    o10, o11 = st.columns(2)
    o10.text_input("NS/Checklist - prepared by", key="office_prepared_by")
    o11.text_input("NS/Checklist - checked / approved by", key="office_checked_by")

    s1, s2, s3 = st.columns(3)
    with s1:
        st.download_button("💾 Save these details (.json)",
                           data=json.dumps(current_office_profile(), indent=2, ensure_ascii=False),
                           file_name="office_details.json", mime="application/json",
                           help="Keep this file and use 'Load saved details' next time - the app does not "
                                "remember anything between sessions.")
    with s2:
        st.file_uploader("Load saved details", type=["json"], key="office_profile_upload",
                         on_change=_load_office_profile)
    with s3:
        st.button("↩️ Reset to defaults", on_click=_reset_office_profile)

office_now = current_office_profile()

_fy = fiscal_year_label()
col_a, col_b, col_c = st.columns(3)
with col_a:
    memo_no = st.text_input(
        "Memo No.",
        value=f"{office_now['memo_no_prefix']}/{_fy}" if office_now['memo_no_prefix'].strip() else _fy,
    )
with col_b:
    memo_date = st.text_input("Memo date", value=datetime.date.today().strftime("%d.%m.%Y"))
with col_c:
    calc_sheet_sl_no = st.text_input(
        "Calculation sheet Sl. No.", value="05",
        help="The serial number, within this case file, where the item-wise calculation sheet "
             "(appended as the last page of the NS/Checklist doc) will sit - matches the "
             "'Calculation sheet is placed at Sl. No.' column in the summary table."
    )

use_admissible = st.checkbox("Sanction the CGHS-checked admissible amount (recommended)", value=True,
                              help="Uncheck only if you intend to sanction the full claimed amount regardless of the rate check above.")
final_amount = total_admissible if use_admissible else total_claimed

st.write(f"**Amount to be sanctioned: ₹{final_amount:,.0f}** ({rupees_in_words(final_amount)})")

if unresolved or summary['review']:
    st.caption("⚠️ Some rows above still need a decision (REVIEW / NO MATCH). You can generate the documents now, "
               "but the amounts will change if you resolve them afterwards - generate again if so.")


def _remark(r):
    """Short reason printed in the Remarks column of the Calculation Sheet page."""
    if r['rule_status'] == INCLUDED:
        return f"Included in {r.get('covered_by') or 'a package billed'} - not payable separately"
    if r['rule_status'] == REPEAT:
        return "Same test billed more than once - counted once"
    if r['rule_status'] == FORCED_EXCLUDE:
        return "Not admitted"
    if r['cghs_rate'] is not None and r['claimed_amount'] > r['cghs_rate']:
        return f"Restricted to CGHS rate Rs {r['cghs_rate']}/-"
    return ""


def _order_remark():
    if abs(total_claimed - total_admissible) < 1:
        return 'in order'
    reasons = []
    if any(r['cghs_rate'] is not None and r['claimed_amount'] > r['cghs_rate'] for r in final_items):
        reasons.append('billed above CGHS rates')
    if any(r['rule_status'] in (INCLUDED, REPEAT) for r in final_items):
        reasons.append('tests already included in a package billed / repeated')
    if any(r['rule_status'] == FORCED_EXCLUDE for r in final_items):
        reasons.append('not admitted on verification')
    why = f" - {', '.join(reasons)}" if reasons else ""
    return f'partially admissible (Rs {total_claimed - total_admissible:,.0f}/- disallowed{why} - see calculation sheet)'


if st.button("📄 Generate documents", type="primary", disabled=(not name or not emp_id)):
    case = {
        'memo_no': memo_no,
        'memo_date': memo_date,
        'amount': int(final_amount),
        'name': name,
        'designation': designation,
        'office': office,
        'pin': pin,
        'emp_id': emp_id,
        'hospital': hospital,
        'patient_relation': patient_relation,
        'patient_relation_display': patient_relation.upper(),
        'cghs_card_no': cghs_id,
        'cghs_validity': cghs_validity,
        'submission_date': submission_date,
        'treatment_type': treatment_type,
        'nabh_status': nabh_status,
        'tier_label': tier_label,
        'hco_type': 'Yes',
        'total_admissible': int(total_admissible),
        'total_claimed': int(total_claimed),
        'calc_sheet_sl_no': calc_sheet_sl_no,
        'order_remark': _order_remark(),
        'recommended': 'Yes',
        'office_profile': office_now,
    }
    ns_items = [{
        'sl_no': i + 1,
        'particulars': r['matched_description'] or r['description'],
        'code': r['code'],
        'claimed': r['claimed_amount'],
        'admissible': r['counted_amount'] if r['counted_amount'] is not None else 'MANUAL REVIEW',
        'remark': _remark(r),
    } for i, r in enumerate(final_items)]

    out_dir = os.path.join(tempfile.gettempdir(), 'cghs_out')
    os.makedirs(out_dir, exist_ok=True)
    sanction_path = os.path.join(out_dir, 'Sanction_Memo.docx')
    ns_path = os.path.join(out_dir, 'NS_Checklist.docx')
    build_sanction_memo(case, sanction_path)
    build_ns_checklist(case, ns_items, ns_path)

    # Keep the finished files in the session so both download buttons stay on screen
    # (clicking one download button reruns the page, which would otherwise make the other vanish).
    with open(sanction_path, 'rb') as f1, open(ns_path, 'rb') as f2:
        st.session_state.generated_docs = {'memo': f1.read(), 'ns': f2.read(), 'emp_id': emp_id, 'name': name}

docs = st.session_state.get('generated_docs')
if docs:
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(docs['emp_id']))
    st.success(f"Documents generated for {docs['name']} ({docs['emp_id']}).")
    st.caption("These files don't update by themselves - click **Generate documents** again after changing anything above.")
    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button("⬇️ Download Sanction Memo (.docx)", docs['memo'], file_name=f"Sanction_Memo_{safe_id}.docx")
    with dl2:
        st.download_button("⬇️ Download NS/Checklist (.docx)", docs['ns'], file_name=f"NS_Checklist_{safe_id}.docx")
