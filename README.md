# DMARC-aware Email Collector

A small Streamlit app that:

1. Accepts domains via `.txt` / `.csv` upload or pasted text.
2. Checks DMARC policy for each domain.
3. Keeps only domains with `p=none`, `p=quarantine`, or no DMARC record.
4. Crawls homepage + common pages (`/contact`, `/about`, `/support`).
5. Extracts public email addresses and deduplicates them.
6. Shows results in a table and provides CSV download.

## How to run

### 1) Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2) Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3) Start the app

```bash
streamlit run app.py
```

After launching, open the local URL shown by Streamlit (typically `http://localhost:8501`).

## How to use

1. Upload a domain list (`.txt` or `.csv`) and/or paste domains in the text area.
2. (Optional) tune **Parallel workers** for faster crawling.
3. Click **Run collection**.
4. Review the table: `Domain | DMARC Policy | Extracted Emails`.
5. Click **Download extracted emails CSV** to export results.

## Input formats

- `.txt`: one domain per line (or comma/space separated).
- `.csv`: domains in one or more columns (all cells are scanned).

Examples:

```text
example.com
subdomain.example.org
```

```csv
example.com
acme.org
```

## Run tests

```bash
python -m pytest -q
```
