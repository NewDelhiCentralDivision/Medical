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

import streamlit as st
import pandas as pd

from matcher import load_master, match_line_item, flag_duplicates, get_rate, extract_cghs_code, TIER_KEYS
from letter_templates import build_sanction_memo, build_ns_checklist
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
# Sidebar: API key + rate list + city/tier
# ---------------------------------------------------------------------------
st.sidebar.title("Setup")
st.sidebar.subheader("Reading the documents")
provider = st.sidebar.selectbox(
    "How should the claim documents be read?",
    [
        "Manual entry only - no OCR, no key needed",
        "Local OCR (offline, free - Tesseract)",
        "Anthropic (Claude)",
        "Google (Gemini)",
    ],
    index=0,
    help="The first two options need no API key at all and never send your documents anywhere."
)

def _get_secret(name):
    """Check st.secrets first (works for local secrets.toml and Streamlit Cloud secrets), then env vars."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, "")

api_key = ""
model_name = ""

if provider == "Manual entry only - no OCR, no key needed":
    st.sidebar.caption(
        "No documents get read automatically. Skip straight to section 2 below and type in the "
        "claimant details and each billed item yourself - the rate check, flagging, and letter "
        "generation all work exactly the same either way."
    )
elif provider == "Local OCR (offline, free - Tesseract)":
    st.sidebar.caption(
        "Runs entirely on this machine, sends nothing anywhere, and needs no key. **However, plain "
        "OCR sometimes misreads digits in amounts** (a real ₹1,000 has been misread as ₹600 in "
        "testing) - so it only shows you the raw text to read alongside the original scan. It will "
        "not auto-fill amounts into the claim table for you; you still type those in yourself, just "
        "with the raw text as a handy reference."
    )
    st.sidebar.caption("Requires the `tesseract` program installed on this machine (see README).")
elif provider == "Anthropic (Claude)":
    default_key = _get_secret("ANTHROPIC_API_KEY")
    api_key = st.sidebar.text_input(
        "Anthropic API key",
        type="password",
        help="Get one at console.anthropic.com. Pre-filled automatically if set in secrets.toml or as an environment variable, so you shouldn't need to type it each time.",
        value=default_key,
    )
    model_name = st.sidebar.text_input("Model", value="claude-sonnet-5",
                                        help="Current model as of mid-2026. Anthropic's docs.claude.com "
                                             "has the up to date list if this ever changes.")
else:
    default_key = _get_secret("GEMINI_API_KEY") or _get_secret("GOOGLE_API_KEY")
    api_key = st.sidebar.text_input(
        "Gemini API key",
        type="password",
        help="Get one free at aistudio.google.com/apikey - use the AI Studio key, not a Google Cloud Console key. Pre-filled automatically if set in secrets.toml or as an environment variable.",
        value=default_key,
    )
    model_name = st.sidebar.text_input(
        "Model",
        value="gemini-flash-latest",
        help="Google retires specific model versions frequently (e.g. gemini-2.5-flash was restricted to "
             "existing users only in mid-2026). 'gemini-flash-latest' is a rolling alias Google keeps "
             "pointed at their current recommended fast model, so it should keep working without you "
             "needing to update this field. If it ever 404s, check "
             "https://ai.google.dev/gemini-api/docs/models for the current model name."
    )

# Strip whitespace and stray quote characters - a very common cause of "API key not valid"
# errors is accidentally copying a leading/trailing space or a quote mark along with the key.
api_key = api_key.strip().strip('"').strip("'").strip() if api_key else api_key

if provider in ("Anthropic (Claude)", "Google (Gemini)") and not api_key:
    st.sidebar.caption(
        "No key entered yet. To avoid typing it every time, create a file `.streamlit/secrets.toml` "
        "next to app.py with a line like `ANTHROPIC_API_KEY = \"sk-ant-...\"` (or `GEMINI_API_KEY = \"...\"`) "
        "and it will load automatically."
    )

if provider in ("Anthropic (Claude)", "Google (Gemini)") and api_key:
    if st.sidebar.button("✅ Test this key"):
        try:
            if provider == "Anthropic (Claude)":
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)
                client.messages.create(model=model_name, max_tokens=10,
                                        messages=[{"role": "user", "content": "Say OK"}])
            else:
                from google import genai
                client = genai.Client(api_key=api_key)
                client.models.generate_content(model=model_name, contents=["Say OK"])
            st.sidebar.success("Key works.")
        except Exception as e:
            st.sidebar.error(f"Key check failed: {e}")

st.sidebar.markdown("---")
st.sidebar.subheader("CGHS Rate List")
st.sidebar.caption(
    "Bundled: all three city tiers (X/Y/Z), effective 13.10.2025, 1,998 codes each. "
    "Upload a replacement JSON/Excel below if CGHS issues a revision."
)
custom_master = st.sidebar.file_uploader("Replace rate list (optional)", type=["xlsx", "json"])

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

st.sidebar.markdown("---")
st.sidebar.subheader("City / Tier")
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
st.caption("New Delhi Central Division — decision-support tool. Every match and amount must be reviewed by the processing officer before a case is sanctioned.")

if "extracted" not in st.session_state:
    st.session_state.extracted = None
if "line_items" not in st.session_state:
    st.session_state.line_items = None

# ---------------------------------------------------------------------------
# Step 1: Upload claim documents
# ---------------------------------------------------------------------------
st.header("1. Upload claim documents")

if provider == "Manual entry only - no OCR, no key needed":
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
    button_label = ("📝 Read text (offline, no auto-fill)" if provider == "Local OCR (offline, free - Tesseract)"
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
  "nabh_status": string or null,             // "NABH" or "Non-NABH" if stated/visible, else null
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

if provider == "Local OCR (offline, free - Tesseract)" and run_ocr:
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

if provider in ("Anthropic (Claude)", "Google (Gemini)") and run_ocr:
    if not api_key:
        st.error(f"Please enter your {provider} API key in the sidebar first.")
    else:
        with st.spinner(f"Reading documents with {provider}..."):
            try:
                images = []
                for f in uploaded_files:
                    images.extend(file_to_pil_images(f))

                if provider == "Anthropic (Claude)":
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
                        f"**{provider} rejected the API key itself** (not a claim-reading problem). "
                        f"Common causes:\n\n"
                        + ("- Copied a Google **Cloud Console** API key rather than an **AI Studio** key from "
                           "[aistudio.google.com/apikey](https://aistudio.google.com/apikey)\n"
                           if provider == "Google (Gemini)" else
                           "- Copied a key from the wrong place - it should start with `sk-ant-` from "
                           "[console.anthropic.com](https://console.anthropic.com)\n")
                        + "- A stray space, newline, or quote mark got copied along with the key\n"
                        + "- The key was since deleted/regenerated in your account, so this copy is stale\n\n"
                        f"Raw error: `{err_text}`"
                    )
                elif "NOT_FOUND" in err_text or "404" in err_text or "no longer available" in err_text.lower():
                    st.error(
                        f"**The model name `{model_name}` isn't available anymore** - {provider.split()[0]} "
                        "retires specific model versions on a rolling basis, sometimes with little notice.\n\n"
                        + ("Try changing the Model field in the sidebar to `gemini-flash-latest` (a rolling "
                           "alias that should always point to a current working model), or check "
                           "[ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models) "
                           "for the current model list."
                           if provider == "Google (Gemini)" else
                           "Try `claude-sonnet-5` or check "
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
    nabh_status = st.selectbox("Facility accreditation", ["NABH", "Non-NABH"],
                                index=0 if (ex.get("nabh_status") or "NABH") == "NABH" else 1)
with col5:
    submission_date = st.text_input("Claim submission date", value=ex.get("submission_date") or "")
with col6:
    treatment_type = st.text_input("Treatment type", value=ex.get("treatment_type") or "TEST/INVESTIGATION")

# ---------------------------------------------------------------------------
# Step 3: Line-item matching against CGHS rates
# ---------------------------------------------------------------------------
st.header("3. Item-wise CGHS rate check")

items_raw = st.session_state.line_items or []
if not items_raw:
    st.info("No line items extracted yet. Run extraction above, or add rows manually in the table below.")
    items_raw = [{"description": "", "code": "", "claimed_amount": 0}]

st.caption(f"Rate lookup: **{tier_label}**, **{nabh_status}** (change in the sidebar if needed)")

matched = [match_line_item(it, BY_CODE, DESCRIPTIONS, tier_label=tier_label, accreditation=nabh_status) for it in items_raw]
matched = flag_duplicates(matched)

for it in matched:
    it['cghs_rate_used'] = it.get('cghs_rate')

df = pd.DataFrame([{
    'Description (as billed)': it.get('description', ''),
    'Billed code': it.get('code', ''),
    'Matched CGHS code': it.get('matched_code') or '',
    'Matched CGHS description': it.get('matched_description') or '',
    'Claimed (₹)': it.get('claimed_amount') or 0,
    f'CGHS rate - {tier_label}, {nabh_status} (₹)': it.get('cghs_rate_used'),
    'Admissible (₹)': it.get('admissible_amount'),
    'Flag': it.get('flag'),
} for it in matched])

st.write("Review every row. If a match looks wrong, correct the **Matched CGHS code** column directly and the rate/flag will update on the next save.")
edited_df = st.data_editor(
    df,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "Flag": st.column_config.TextColumn(disabled=True),
    }
)

st.caption(
    "OK = within CGHS rate · OVERCHARGED = claimed above CGHS rate (capped at CGHS rate below) · "
    "POSSIBLE DUPLICATE = same CGHS code billed more than once, check for double-counted sub-tests · "
    "NO MATCH = could not find this item in the rate list, verify the code/description manually."
)

# Re-derive final admissible totals from (possibly edited) table, re-matching any manually corrected codes
final_items = []
for _, row in edited_df.iterrows():
    code = extract_cghs_code(str(row.get('Matched CGHS code') or ''), valid_codes=BY_CODE)
    claimed = row.get('Claimed (₹)') or 0
    if code and code in BY_CODE:
        rate = get_rate(BY_CODE[code], tier_label, nabh_status)
        admissible = min(claimed, rate) if rate is not None else None
        desc = BY_CODE[code]['description']
    else:
        rate = None
        admissible = None
        desc = row.get('Description (as billed)')
    final_items.append({
        'description': row.get('Description (as billed)'),
        'code': code,
        'claimed_amount': claimed,
        'cghs_rate': rate,
        'admissible_amount': admissible,
        'matched_description': desc,
    })

total_claimed = sum(it['claimed_amount'] or 0 for it in final_items)
total_admissible = sum(it['admissible_amount'] or 0 for it in final_items if it['admissible_amount'] is not None)
unresolved = [it for it in final_items if it['admissible_amount'] is None]

m1, m2, m3 = st.columns(3)
m1.metric("Total claimed", f"₹{total_claimed:,.0f}")
m2.metric("Total admissible (as per CGHS rates)", f"₹{total_admissible:,.0f}")
diff = total_claimed - total_admissible
m3.metric("Difference", f"₹{diff:,.0f}", delta=f"-₹{diff:,.0f}" if diff > 0 else None, delta_color="inverse")

if unresolved:
    st.warning(f"{len(unresolved)} row(s) have no matched CGHS code and are excluded from the admissible total above. Resolve these before sanctioning.")

st.markdown("---")

# ---------------------------------------------------------------------------
# Step 4: Generate documents
# ---------------------------------------------------------------------------
st.header("4. Generate Sanction Memo & NS/Checklist")

col_a, col_b = st.columns(2)
with col_a:
    memo_no = st.text_input("Memo No.", value=f"L-2/NDHO/MR-XXX/{datetime.date.today().year}-{str(datetime.date.today().year+1)[-2:]}")
with col_b:
    memo_date = st.text_input("Memo date", value=datetime.date.today().strftime("%d.%m.%Y"))

use_admissible = st.checkbox("Sanction the CGHS-checked admissible amount (recommended)", value=True,
                              help="Uncheck only if you intend to sanction the full claimed amount regardless of the rate check above.")
final_amount = total_admissible if use_admissible else total_claimed

st.write(f"**Amount to be sanctioned: ₹{final_amount:,.0f}** ({rupees_in_words(final_amount)})")

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
        'hco_type': 'Yes',
        'total_admissible': int(total_admissible),
        'order_remark': 'in order' if abs(total_claimed - total_admissible) < 1 else
                         f'partially admissible (Rs {total_claimed - total_admissible:,.0f}/- in excess of CGHS rates has been disallowed - see item-wise sheet)',
        'recommended': 'Yes',
    }
    ns_items = [{
        'sl_no': i + 1,
        'particulars': it['matched_description'] or it['description'],
        'code': it['code'],
        'claimed': it['claimed_amount'],
        'admissible': it['admissible_amount'] if it['admissible_amount'] is not None else 'MANUAL REVIEW',
    } for i, it in enumerate(final_items)]

    os.makedirs('/tmp/cghs_out', exist_ok=True)
    sanction_path = '/tmp/cghs_out/Sanction_Memo.docx'
    ns_path = '/tmp/cghs_out/NS_Checklist.docx'
    build_sanction_memo(case, sanction_path)
    build_ns_checklist(case, ns_items, ns_path)

    st.success("Documents generated.")
    dl1, dl2 = st.columns(2)
    with dl1:
        with open(sanction_path, 'rb') as f:
            st.download_button("⬇️ Download Sanction Memo (.docx)", f, file_name=f"Sanction_Memo_{emp_id}.docx")
    with dl2:
        with open(ns_path, 'rb') as f:
            st.download_button("⬇️ Download NS/Checklist (.docx)", f, file_name=f"NS_Checklist_{emp_id}.docx")
