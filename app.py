import os, io, base64
from datetime import datetime
from flask import Flask, jsonify, request, send_file, abort, url_for
from flask_cors import CORS
import pg8000
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader

# ------------------------------------------------------------------------------
# Flask + CORS Setup
# ------------------------------------------------------------------------------
app = Flask(__name__)

app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB uploads
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

CORS(app, resources={r"/api/*": {"origins": "http://localhost:3001"}})


# ------------------------------------------------------------------------------
# Database (pg8000) Setup
# ------------------------------------------------------------------------------
DB_CONFIG = {
    "user":     "postgres",
    "password": "Luke4:3!Path",
    "host":     "localhost",
    "port":     5432,
    "database": "postgres"
}

def get_conn_cursor():
    conn = pg8000.connect(**DB_CONFIG)
    cur = conn.cursor()
    return conn, cur

def close_conn_cursor(conn, cur):
    cur.close()
    conn.close()

# ------------------------------------------------------------------------------
# Helper: Return JSON error
# ------------------------------------------------------------------------------
def error_response(msg, code=400):
    return jsonify({"error": msg}), code

# ------------------------------------------------------------------------------
# 1) UPLOAD CONTRACT (PDF) → /api/upload_contract
# ------------------------------------------------------------------------------
@app.route('/api/upload_contract', methods=['POST'])
def api_upload_contract():
    """
    Expects form-data: "contract_pdf" = File (must be .pdf)
    Inserts into contract_templates (deactivates old active).
    """
    if 'contract_pdf' not in request.files:
        return error_response("Missing 'contract_pdf' file.", 400)
    file = request.files['contract_pdf']
    filename = file.filename.lower()
    if not filename.endswith('.pdf'):
        return error_response("Only PDF files are allowed.", 400)

    pdf_bytes = file.read()

    conn, cur = get_conn_cursor()
    # Deactivate existing
    cur.execute("UPDATE contract_templates SET is_active = FALSE WHERE is_active = TRUE;")
    # Insert new
    cur.execute("""
        INSERT INTO contract_templates (pdf_data, filename, is_active, note)
        VALUES (%s, %s, TRUE, %s)
        RETURNING id;
    """, (pdf_bytes, filename, f"Uploaded at {datetime.now().isoformat()}"))
    new_id = cur.fetchone()[0]
    conn.commit()
    close_conn_cursor(conn, cur)

    return jsonify({"success": True, "template_id": new_id})

# ------------------------------------------------------------------------------
# 2) LIST CONTRACT VERSIONS → /api/list_contract_versions
# ------------------------------------------------------------------------------
@app.route('/api/list_contract_versions', methods=['GET'])
def api_list_contract_versions():
    """
    Returns JSON list of all rows in contract_templates:
    [ {id, filename, created_at (ISO), is_active}, ... ]
    """
    conn, cur = get_conn_cursor()
    cur.execute("""
        SELECT id, filename, created_at, is_active
          FROM contract_templates
         ORDER BY created_at DESC;
    """)
    rows = cur.fetchall()
    close_conn_cursor(conn, cur)

    versions = [
        {
            "id":         r[0],
            "filename":   r[1],
            "created_at": r[2].isoformat(),
            "is_active":  bool(r[3])
        } for r in rows
    ]
    return jsonify(versions)

# ------------------------------------------------------------------------------
# 3) ACTIVATE CONTRACT → /api/activate_contract (POST JSON: {"template_id":X})
# ------------------------------------------------------------------------------
@app.route('/api/activate_contract', methods=['POST'])
def api_activate_contract():
    data = request.get_json()
    if not data or 'template_id' not in data:
        return error_response("JSON must include 'template_id'.", 400)
    template_id = data['template_id']

    conn, cur = get_conn_cursor()
    # Check exists
    cur.execute("SELECT 1 FROM contract_templates WHERE id = %s;", (template_id,))
    if not cur.fetchone():
        close_conn_cursor(conn, cur)
        return error_response("template_id not found.", 404)

    # Deactivate all, activate this one
    cur.execute("UPDATE contract_templates SET is_active = FALSE WHERE is_active = TRUE;")
    cur.execute("UPDATE contract_templates SET is_active = TRUE WHERE id = %s;", (template_id,))
    conn.commit()
    close_conn_cursor(conn, cur)

    return jsonify({"success": True, "activated_id": template_id})

# ------------------------------------------------------------------------------
# 4) GET ACTIVE CONTRACT (metadata + PDF URL) → /api/active_contract
# ------------------------------------------------------------------------------
@app.route('/api/active_contract', methods=['GET'])
def api_active_contract():
    """
    Returns JSON:
       { "id":…, "filename":…, "pdf_url": "/api/download_template/<id>" }
    or 404 if none active.
    """
    conn, cur = get_conn_cursor()
    cur.execute("SELECT id, filename FROM contract_templates WHERE is_active = TRUE;")
    row = cur.fetchone()
    close_conn_cursor(conn, cur)
    if not row:
        return error_response("No active contract.", 404)
    tid, fname = row
    return jsonify({
        "id": tid,
        "filename": fname,
        "pdf_url": url_for('api_download_template', template_id=tid)
    })

# ------------------------------------------------------------------------------
# 5) DOWNLOAD TEMPLATE PDF → /api/download_template/<int:template_id>
# ------------------------------------------------------------------------------
@app.route('/api/download_template/<int:template_id>', methods=['GET'])
def api_download_template(template_id):
    conn, cur = get_conn_cursor()
    cur.execute("SELECT filename, pdf_data FROM contract_templates WHERE id = %s;", (template_id,))
    row = cur.fetchone()
    close_conn_cursor(conn, cur)
    if not row:
        return error_response("Template not found.", 404)
    filename, pdf_bytes = row
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=False,
        download_name=filename
    )

# ------------------------------------------------------------------------------
# 6) CREATE (FILL) PERSONALIZED → /api/create_personal (POST form-data or JSON)
# ------------------------------------------------------------------------------
@app.route('/api/create_personal', methods=['POST'])
def api_create_personal():
    data = request.get_json()
    name = data.get('name', '')
    address = data.get('address', '')

    # Step 1: Get active template from DB
    conn, cur = get_conn_cursor()
    cur.execute("SELECT id, pdf_data FROM contract_templates WHERE is_active = TRUE LIMIT 1;")
    tpl = cur.fetchone()
    if not tpl:
        close_conn_cursor(conn, cur)
        return jsonify({"error": "No active contract template found."}), 404

    template_id, template_pdf = tpl

    # Step 2: Read original PDF
    pdf_reader = PdfReader(io.BytesIO(template_pdf))
    pdf_writer = PdfWriter()
    for page in pdf_reader.pages:
        pdf_writer.add_page(page)

    # Step 3: Create overlay with user info
    overlay_stream = io.BytesIO()
    c = canvas.Canvas(overlay_stream, pagesize=letter)
    c.drawString(100, 700, f"Name: {name}")
    c.drawString(100, 680, f"Address: {address}")
    c.save()
    overlay_stream.seek(0)
    overlay_pdf = PdfReader(overlay_stream)

    # Step 4: Merge overlay onto first page
    pdf_writer.pages[0].merge_page(overlay_pdf.pages[0])

    # Step 5: Output final filled PDF
    final_stream = io.BytesIO()
    pdf_writer.write(final_stream)
    final_pdf_bytes = final_stream.getvalue()

    # Debug: Save local copy
    with open("debug_filled.pdf", "wb") as f:
        f.write(final_pdf_bytes)

    # Step 6: Save to DB
    cur.execute("""
        INSERT INTO personalized_contracts (employee_name, address, generated_pdf_data)
        VALUES (%s, %s, %s) RETURNING id;
    """, (name, address, final_pdf_bytes))
    new_id = cur.fetchone()[0]
    conn.commit()
    close_conn_cursor(conn, cur)

    return jsonify({ "id": new_id })

# ------------------------------------------------------------------------------
# 7) DOWNLOAD GENERATED (UNSIGNED) PDF → /api/download_personal/<int:personal_id>
# ------------------------------------------------------------------------------
@app.route('/api/download_personal/<int:personal_id>', methods=['GET'])
def api_download_personal(personal_id):
    conn, cur = get_conn_cursor()
    cur.execute("""
        SELECT generated_pdf_data
          FROM personalized_contracts
         WHERE id = %s;
    """, (personal_id,))
    row = cur.fetchone()
    close_conn_cursor(conn, cur)
    if not row:
        return error_response("Generated PDF not found.", 404)
    pdf_bytes = row[0]
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=False,
        download_name=f"contract_{personal_id}.pdf"
    )

# ------------------------------------------------------------------------------
# 8) SIGN PERSONALIZED → /api/sign_personal/<int:personal_id> (POST JSON {signature: "data:image/png;base64,..."})
# ------------------------------------------------------------------------------
@app.route('/api/sign_personal/<int:personal_id>', methods=['POST'])
def api_sign_personal(personal_id):
    data = request.get_json()
    if not data or 'signature' not in data:
        return jsonify({"error": "Missing signature"}), 400

    # Decode base64 image
    signature_data = base64.b64decode(data['signature'].split(',')[1])
    image = ImageReader(io.BytesIO(signature_data))

    # Fetch original PDF
    conn, cur = get_conn_cursor()
    cur.execute("SELECT generated_pdf_data FROM personalized_contracts WHERE id = %s;", (personal_id,))
    row = cur.fetchone()
    if not row or not row[0]:
        close_conn_cursor(conn, cur)
        return jsonify({"error": "No PDF found"}), 404

    original_pdf = row[0]
    pdf_reader = PdfReader(io.BytesIO(original_pdf))
    pdf_writer = PdfWriter()

    # Create overlay PDF with signature
    sig_overlay = io.BytesIO()
    c = canvas.Canvas(sig_overlay, pagesize=letter)
    c.drawImage(image, 100, 100, width=200, height=100)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.drawString(100, 90, f"Signed on {timestamp}")
    c.save()
    sig_overlay.seek(0)
    overlay_pdf = PdfReader(sig_overlay)

    # Merge onto last page
    for page in pdf_reader.pages:
        pdf_writer.add_page(page)
    pdf_writer.pages[-1].merge_page(overlay_pdf.pages[0])

    # Write new signed PDF
    output_stream = io.BytesIO()
    pdf_writer.write(output_stream)
    signed_pdf = output_stream.getvalue()

    # Save to DB
    cur.execute("""
        UPDATE personalized_contracts
        SET signed_pdf_data = %s, signature_timestamp = %s
        WHERE id = %s;
    """, (signed_pdf, timestamp, personal_id))
    conn.commit()
    close_conn_cursor(conn, cur)

    return jsonify({
        "success": True,
        "signed_url": url_for('api_download_signed', personal_id=personal_id)
    })


# ------------------------------------------------------------------------------
# 9) DOWNLOAD SIGNED PDF → /api/download_signed/<int:personal_id>
# ------------------------------------------------------------------------------
@app.route('/api/download_signed/<int:personal_id>', methods=['GET'])
def api_download_signed(personal_id):
    conn, cur = get_conn_cursor()
    cur.execute("""
        SELECT signed_pdf_data
          FROM personalized_contracts
         WHERE id = %s;
    """, (personal_id,))
    row = cur.fetchone()
    close_conn_cursor(conn, cur)
    if not row or not row[0]:
        return error_response("Signed PDF not found.", 404)
    signed_bytes = row[0]
    return send_file(
        io.BytesIO(signed_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"signed_contract_{personal_id}.pdf"
    )

# ------------------------------------------------------------------------------
# 10) LIST PERSONALIZED CONTRACTS → /api/list_personalized_contracts
# ------------------------------------------------------------------------------
@app.route('/api/list_personalized_contracts', methods=['GET'])
def api_list_personalized_contracts():
    conn, cur = get_conn_cursor()
    cur.execute("""
        SELECT id, template_id, employee_name, created_at,
               (signed_pdf_data IS NOT NULL) AS is_signed
          FROM personalized_contracts
         ORDER BY created_at DESC;
    """)
    rows = cur.fetchall()
    close_conn_cursor(conn, cur)

    data = [
        {
            "id":            r[0],
            "template_id":   r[1],
            "employee_name": r[2],
            "created_at":    r[3].isoformat(),
            "is_signed":     bool(r[4]),
            "pdf_url":       url_for('api_download_personal', personal_id=r[0]),
            "signed_url":    (url_for('api_download_signed', personal_id=r[0]) if r[4] else None)
        } for r in rows
    ]
    return jsonify(data)

# ------------------------------------------------------------------------------
# 404 for any other /api/... route
# ------------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

if __name__ == '__main__':
    # Create tables if they do not exist (optional; run once)
    conn, cur = get_conn_cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS contract_templates (
      id           SERIAL PRIMARY KEY,
      pdf_data     BYTEA      NOT NULL,
      filename     TEXT       NOT NULL,
      created_at   TIMESTAMP  NOT NULL DEFAULT NOW(),
      is_active    BOOLEAN    NOT NULL DEFAULT FALSE,
      note         TEXT
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS personalized_contracts (
      id                     SERIAL PRIMARY KEY,
      template_id            INTEGER    NOT NULL REFERENCES contract_templates(id),
      employee_name          TEXT       NOT NULL,
      employee_id_number     TEXT       NOT NULL,
      employee_cellphone     TEXT,
      employee_address       TEXT       NOT NULL,
      generated_pdf_data     BYTEA      NOT NULL,
      created_at             TIMESTAMP  NOT NULL DEFAULT NOW(),
      signed_pdf_data        BYTEA,
      signature_timestamp    TIMESTAMP
    );
    """)
    conn.commit()
    close_conn_cursor(conn, cur)

    app.run(debug=True)
