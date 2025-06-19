import os, io, base64
from datetime import datetime
from flask import Flask, jsonify, request, send_file, url_for
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

# ✔️ Enable CORS on all /api/* routes
CORS(app, resources={r"/api/*": {"origins": "*"}})

app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB uploads
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ------------------------------------------------------------------------------
# Database Connection Helpers (stubbed for this test)
# ------------------------------------------------------------------------------
DB_CONFIG = {
    "user":     "postgres",
    "password": "yourpassword",
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
# A SIMPLE TEST ENDPOINT TO VERIFY CORS
# ------------------------------------------------------------------------------
@app.route('/api/test_cors', methods=['GET'])
def api_test_cors():
    return jsonify({"message": "CORS is enabled for /api/test_cors"})

# ------------------------------------------------------------------------------
# AN EXAMPLE “ACTIVE CONTRACT” ENDPOINT (same as before)
# ------------------------------------------------------------------------------
@app.route('/api/active_contract', methods=['GET'])
def api_active_contract():
    # In a real app, you’d fetch from the database.
    # Here, just return a dummy JSON payload.
    return jsonify({
        "id": 1,
        "filename": "example.pdf",
        "pdf_url": url_for('api_download_template', template_id=1, _external=False)
    })

@app.route('/api/download_template/<int:template_id>', methods=['GET'])
def api_download_template(template_id):
    # For testing, just return a blank PDF stream or a small dummy file.
    # Here we generate a one-page PDF on the fly.
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=letter)
    c.drawString(100, 700, f"Dummy PDF for template {template_id}")
    c.showPage()
    c.save()
    packet.seek(0)
    return send_file(
        packet,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=f"dummy_{template_id}.pdf"
    )

# ------------------------------------------------------------------------------
# RUN THE APP
# ------------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True)
