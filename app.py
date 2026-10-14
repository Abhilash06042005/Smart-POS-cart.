from flask import Flask, request, jsonify, send_from_directory, Response
import threading
import time
import re
import io
from datetime import datetime
from urllib.parse import quote

import serial
from serial.tools import list_ports

import firebase_admin
from firebase_admin import credentials, db

from evdev import InputDevice, ecodes, list_devices

import qrcode
from PIL import Image

from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor, black, white, lightgrey
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# =====================================================
# CONFIGURATION
# =====================================================
BAUD_RATE = 115200
TOLERANCE_G = 5.0

FIREBASE_KEY_FILE = "firebase_key.json"
DATABASE_URL = "https://abhi5318k-default-rtdb.asia-southeast1.firebasedatabase.app/"

UPI_ID = "7893530610@axl"
UPI_NAME = "Agurla Abhilash"
UPI_NOTE = "SmartCart Payment"

QR_FILE_NAME = "dynamic_payment_qr.png"

app = Flask(__name__, static_folder=".", static_url_path="")

# =====================================================
# GLOBAL STATE
# =====================================================
latest_weight = 0.0
serial_connected = False
scanner_connected = False
scanner_enabled = True

cart = []
cart_lock = threading.Lock()

message_lock = threading.Lock()
last_message = "Ready"

barcode_buffer = ""

payment_lock = threading.Lock()
payment_waiting = False
payment_verified = False
payment_expected_amount = 0.0
payment_received_amount = 0.0
payment_upi_ref = ""
payment_sms_text = ""
payment_qr_url = ""

bill_lock = threading.Lock()
last_purchase_items = []
last_purchase_total = 0.0
last_purchase_time = ""
last_purchase_upi_ref = ""

KEY_MAP = {
    "KEY_1": "1",
    "KEY_2": "2",
    "KEY_3": "3",
    "KEY_4": "4",
    "KEY_5": "5",
    "KEY_6": "6",
    "KEY_7": "7",
    "KEY_8": "8",
    "KEY_9": "9",
    "KEY_0": "0",
    "KEY_KP1": "1",
    "KEY_KP2": "2",
    "KEY_KP3": "3",
    "KEY_KP4": "4",
    "KEY_KP5": "5",
    "KEY_KP6": "6",
    "KEY_KP7": "7",
    "KEY_KP8": "8",
    "KEY_KP9": "9",
    "KEY_KP0": "0",
    "KEY_MINUS": "-",
    "KEY_KPMINUS": "-"
}

# =====================================================
# FIREBASE
# =====================================================
def init_firebase():
    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(FIREBASE_KEY_FILE)
        firebase_admin.initialize_app(cred, {
            "databaseURL": DATABASE_URL
        })

# =====================================================
# HELPERS
# =====================================================
def set_last_message(msg: str):
    global last_message
    with message_lock:
        last_message = msg


def get_last_message() -> str:
    with message_lock:
        return last_message


def to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_expected_weight() -> float:
    with cart_lock:
        return round(sum(to_float(item.get("weight", 0.0)) for item in cart), 2)


def get_total_price() -> float:
    with cart_lock:
        return round(sum(to_float(item.get("price", 0.0)) for item in cart), 2)


def get_total_items() -> int:
    with cart_lock:
        return len(cart)


def get_cart_copy():
    with cart_lock:
        return [dict(item) for item in cart]


def is_weight_matched() -> bool:
    total_items = get_total_items()
    if total_items == 0:
        return False

    expected_weight = get_expected_weight()
    actual_weight = round(latest_weight, 2)
    difference = abs(actual_weight - expected_weight)
    return difference <= TOLERANCE_G


def is_checkout_enabled() -> bool:
    return (
        get_total_items() > 0 and
        scanner_enabled and
        serial_connected and
        is_weight_matched()
    )


def reset_payment_state():
    global payment_waiting, payment_verified, payment_expected_amount
    global payment_received_amount, payment_upi_ref, payment_sms_text, payment_qr_url

    with payment_lock:
        payment_waiting = False
        payment_verified = False
        payment_expected_amount = 0.0
        payment_received_amount = 0.0
        payment_upi_ref = ""
        payment_sms_text = ""
        payment_qr_url = ""


def save_last_purchase():
    global last_purchase_items, last_purchase_total, last_purchase_time, last_purchase_upi_ref

    with bill_lock:
        with cart_lock:
            last_purchase_items = [dict(item) for item in cart]
            last_purchase_total = round(sum(to_float(item.get("price", 0.0)) for item in cart), 2)

        last_purchase_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with payment_lock:
            last_purchase_upi_ref = payment_upi_ref


def get_last_purchase():
    with bill_lock:
        return {
            "items": [dict(item) for item in last_purchase_items],
            "total": round(last_purchase_total, 2),
            "time": last_purchase_time,
            "upi_ref": last_purchase_upi_ref
        }


def extract_amount_from_sms(msg: str) -> float:
    patterns = [
        r'rs\.?\s*([0-9]+(?:\.[0-9]+)?)',
        r'inr\s*([0-9]+(?:\.[0-9]+)?)',
        r'₹\s*([0-9]+(?:\.[0-9]+)?)'
    ]

    lower_msg = msg.lower()
    for pattern in patterns:
        match = re.search(pattern, lower_msg, re.IGNORECASE)
        if match:
            return to_float(match.group(1), 0.0)

    return 0.0


def extract_upi_ref(msg: str) -> str:
    match = re.search(r'upi\s*ref\s*no[:\s]*([0-9]+)', msg, re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def create_upi_payment_url(amount: float) -> str:
    amount_str = f"{amount:.2f}"
    return (
        f"upi://pay?pa={quote(UPI_ID)}"
        f"&pn={quote(UPI_NAME)}"
        f"&am={quote(amount_str)}"
        f"&cu=INR"
        f"&tn={quote(UPI_NOTE)}"
    )


def generate_dynamic_qr(amount: float):
    global payment_qr_url

    upi_url = create_upi_payment_url(amount)

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4
    )
    qr.add_data(upi_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    if hasattr(img, "convert"):
        img = img.convert("RGB")

    img.save(QR_FILE_NAME)
    payment_qr_url = upi_url


def build_status_response(message=None):
    expected_weight = get_expected_weight()
    actual_weight = round(latest_weight, 2)
    total_items = get_total_items()

    if total_items == 0:
        weight_ok = False
        status_text = "Scale Ready"
    else:
        difference = abs(actual_weight - expected_weight)
        weight_ok = difference <= TOLERANCE_G
        status_text = "Weight Matched" if weight_ok else "Please add only scanned product into the cart"

    with payment_lock:
        payment_state = {
            "waiting": payment_waiting,
            "verified": payment_verified,
            "expected_amount": round(payment_expected_amount, 2),
            "received_amount": round(payment_received_amount, 2),
            "upi_ref": payment_upi_ref,
            "sms_text": payment_sms_text,
            "qr_url": payment_qr_url
        }

    purchase_state = get_last_purchase()

    return {
        "success": True,
        "message": message if message is not None else get_last_message(),
        "scanner_enabled": scanner_enabled,
        "serial_connected": serial_connected,
        "scanner_connected": scanner_connected,
        "cart": get_cart_copy(),
        "summary": {
            "total_items": total_items,
            "expected_weight_g": expected_weight,
            "actual_weight_g": actual_weight,
            "total_price": get_total_price(),
            "weight_ok": weight_ok,
            "weight_status_text": status_text,
            "checkout_enabled": is_checkout_enabled()
        },
        "payment": payment_state,
        "purchase": purchase_state
    }

# =====================================================
# DEVICE DETECTION
# =====================================================
def find_esp32_serial_port():
    ports = list_ports.comports()

    for port in ports:
        description = (port.description or "").lower()
        manufacturer = (port.manufacturer or "").lower()
        device = (port.device or "").lower()

        print(f"[DEBUG] Checking serial port: {port.device} | {port.description} | {port.manufacturer}")

        keywords = ["cp210", "ch340", "ch910", "usb serial", "uart", "esp32", "silicon labs", "wch"]

        if any(k in description for k in keywords):
            return port.device

        if any(k in manufacturer for k in keywords):
            return port.device

        if "ttyusb" in device or "ttyacm" in device:
            return port.device

    return None


def find_scanner_device():
    for path in list_devices():
        try:
            dev = InputDevice(path)
            name = (dev.name or "").lower()

            print(f"[DEBUG] Checking input device: {path} | {dev.name}")

            if "usb adapter usb device" in name:
                return path

            if "barcode" in name or "scanner" in name:
                return path

            if "usb" in name and "device" in name and "hdmi" not in name:
                return path

        except Exception as e:
            print(f"[WARN] Could not inspect {path}: {e}")

    return None

# =====================================================
# BARCODE PROCESSING
# =====================================================
def process_barcode(barcode: str):
    global scanner_enabled

    barcode = str(barcode).strip()
    if not barcode:
        return False, "Barcode is empty"

    if not scanner_enabled:
        return False, "Checkout completed. Clear cart to start new shopping."

    try:
        ref = db.reference("products/" + barcode)
        product = ref.get()
    except Exception as e:
        return False, f"Firebase read failed: {e}"

    if not product:
        return False, "Product is not matched"

    name = product.get("name", "N/A")
    price = to_float(product.get("price", 0.0), 0.0)
    product_weight = to_float(product.get("weight", 0.0), 0.0)

    if product_weight <= 0:
        return False, "Invalid product weight in database"

    with cart_lock:
        current_expected = round(sum(to_float(item.get("weight", 0.0)) for item in cart), 2)
        current_items = len(cart)

    actual_weight = round(latest_weight, 2)

    print(f"[DEBUG] Barcode          : {barcode}")
    print(f"[DEBUG] Product name     : {name}")
    print(f"[DEBUG] Product wt (db)  : {product_weight} g")
    print(f"[DEBUG] Current expected : {current_expected} g")
    print(f"[DEBUG] Actual wt        : {actual_weight} g")
    print(f"[DEBUG] Current items    : {current_items}")

    if current_items == 0:
        with cart_lock:
            cart.append({
                "barcode": barcode,
                "name": name,
                "price": round(price, 2),
                "weight": round(product_weight, 2)
            })

        return True, f"{name} added to cart"

    difference = abs(actual_weight - current_expected)

    if difference > TOLERANCE_G:
        return False, "Please add scanned product into cart and scan next product"

    with cart_lock:
        cart.append({
            "barcode": barcode,
            "name": name,
            "price": round(price, 2),
            "weight": round(product_weight, 2)
        })

    return True, f"{name} added to cart"

# =====================================================
# THREADS
# =====================================================
def esp32_weight_reader():
    global latest_weight, serial_connected

    while True:
        ser = None
        try:
            serial_port = find_esp32_serial_port()

            if not serial_port:
                serial_connected = False
                print("[ERROR] ESP32 serial port not found")
                time.sleep(2)
                continue

            print(f"[INFO] Trying ESP32 serial on {serial_port}")
            ser = serial.Serial(serial_port, BAUD_RATE, timeout=1)
            serial_connected = True
            print(f"[INFO] Connected to ESP32 on {serial_port}")

            while True:
                line = ser.readline().decode(errors="ignore").strip()

                if not line:
                    continue

                try:
                    value = float(line)

                    if value == -9999:
                        print("[WARN] ESP32 says HX711 not ready")
                        continue

                    latest_weight = round(value, 2)

                except ValueError:
                    print(f"[WARN] Invalid serial data: {line}")

        except Exception as e:
            serial_connected = False
            print(f"[ERROR] Serial connection failed: {e}")
            time.sleep(2)

        finally:
            try:
                if ser and ser.is_open:
                    ser.close()
            except Exception:
                pass


def barcode_scanner_reader():
    global scanner_connected, barcode_buffer

    while True:
        dev = None
        try:
            device_path = find_scanner_device()

            if not device_path:
                scanner_connected = False
                print("[ERROR] Scanner device not found")
                time.sleep(2)
                continue

            print(f"[INFO] Trying scanner on {device_path}")
            dev = InputDevice(device_path)
            dev.grab()

            scanner_connected = True
            barcode_buffer = ""

            print(f"[INFO] Scanner connected on {device_path}")
            print(f"[INFO] Scanner name: {dev.name}")
            print("[INFO] Scanner input grabbed by backend")

            for event in dev.read_loop():
                if event.type != ecodes.EV_KEY:
                    continue

                if event.value != 1:
                    continue

                key_event = ecodes.KEY[event.code]

                if key_event in ("KEY_ENTER", "KEY_KPENTER"):
                    scanned_barcode = barcode_buffer.strip()
                    barcode_buffer = ""

                    if scanned_barcode:
                        print(f"[SCAN] Barcode received: {scanned_barcode}")
                        success, msg = process_barcode(scanned_barcode)

                        if success:
                            print(f"[OK] {msg}")
                        else:
                            print(f"[ALERT] {msg}")

                        set_last_message(msg)

                elif key_event == "KEY_BACKSPACE":
                    barcode_buffer = barcode_buffer[:-1]

                elif key_event in KEY_MAP:
                    barcode_buffer += KEY_MAP[key_event]

        except Exception as e:
            scanner_connected = False
            print(f"[ERROR] Scanner connection failed: {e}")
            time.sleep(2)

        finally:
            if dev is not None:
                try:
                    dev.ungrab()
                except Exception:
                    pass

# =====================================================
# PDF GENERATION
# =====================================================
def generate_bill_pdf(purchase):
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleStyle",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        alignment=TA_CENTER,
        textColor=HexColor("#233b84"),
        spaceAfter=8
    )
    subtitle_style = ParagraphStyle(
        "SubtitleStyle",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        alignment=TA_CENTER,
        textColor=HexColor("#52637f"),
        spaceAfter=14
    )
    body_style = ParagraphStyle(
        "BodyStyle",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
        textColor=black
    )

    story = []
    story.append(Paragraph("Pi Team - Purchase Bill", title_style))
    story.append(Paragraph("Thank you for shopping with Pi Team", subtitle_style))
    story.append(Spacer(1, 6))

    info_data = [
        ["Purchase Time", purchase.get("time", "-")],
        ["UPI Ref", purchase.get("upi_ref", "-") or "-"],
        ["Total Amount", f'Rs. {purchase.get("total", 0.0):.2f}']
    ]

    info_table = Table(info_data, colWidths=[120, 360])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), HexColor("#eef4ff")),
        ("TEXTCOLOR", (0, 0), (-1, -1), black),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.6, lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 16))

    items = purchase.get("items", [])
    table_data = [[
        "S.No",
        "Barcode",
        "Product Name",
        "Price (INR)",
        "Weight (g)"
    ]]

    for i, item in enumerate(items, start=1):
        table_data.append([
            str(i),
            str(item.get("barcode", "")),
            Paragraph(str(item.get("name", "")), body_style),
            f'{to_float(item.get("price", 0.0), 0.0):.2f}',
            f'{to_float(item.get("weight", 0.0), 0.0):.2f}'
        ])

    item_table = Table(
        table_data,
        colWidths=[42, 120, 180, 80, 80],
        repeatRows=1
    )
    item_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#233b84")),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.6, lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (1, 1), (1, -1), "CENTER"),
        ("ALIGN", (3, 1), (4, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, HexColor("#f8fbff")]),
    ]))
    story.append(item_table)
    story.append(Spacer(1, 14))

    total_table = Table(
        [["Final Total", f'Rs. {purchase.get("total", 0.0):.2f}']],
        colWidths=[340, 162]
    )
    total_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#eafaf3")),
        ("TEXTCOLOR", (0, 0), (-1, -1), black),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("GRID", (0, 0), (-1, -1), 0.7, HexColor("#b7e4c7")),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(total_table)

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

# =====================================================
# ROUTES
# =====================================================
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route(f"/{QR_FILE_NAME}")
def dynamic_qr_file():
    return send_from_directory(".", QR_FILE_NAME)


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(build_status_response())


@app.route("/api/scan", methods=["POST"])
def api_scan():
    data = request.get_json(silent=True) or {}
    barcode = str(data.get("barcode", "")).strip()

    success, msg = process_barcode(barcode)
    set_last_message(msg)

    if success:
        return jsonify(build_status_response(message=msg))

    return jsonify({
        "success": False,
        "message": msg
    }), 400


@app.route("/api/remove_item", methods=["POST"])
def api_remove_item():
    data = request.get_json(silent=True) or {}
    index = data.get("index", None)

    if index is None:
        return jsonify({"success": False, "message": "Item index is required"}), 400

    try:
        index = int(index)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid item index"}), 400

    with cart_lock:
        if index < 0 or index >= len(cart):
            return jsonify({"success": False, "message": "Item not found"}), 404

        removed_item = cart.pop(index)

    item_name = removed_item.get("name", "Item")
    msg = f"{item_name} removed from cart"
    set_last_message(msg)
    return jsonify(build_status_response(message=msg))


@app.route("/api/clear", methods=["POST"])
def api_clear():
    global scanner_enabled

    with cart_lock:
        cart.clear()

    scanner_enabled = True
    reset_payment_state()
    set_last_message("Cart cleared")

    return jsonify(build_status_response(message="Cart cleared"))


@app.route("/api/checkout", methods=["POST"])
def api_checkout():
    global scanner_enabled, payment_waiting, payment_verified, payment_expected_amount
    global payment_received_amount, payment_upi_ref, payment_sms_text

    if not is_checkout_enabled():
        return jsonify({
            "success": False,
            "message": "Weight is not matched. Checkout disabled."
        }), 400

    scanner_enabled = False
    total_amount = round(get_total_price(), 2)

    generate_dynamic_qr(total_amount)

    with payment_lock:
        payment_waiting = True
        payment_verified = False
        payment_expected_amount = total_amount
        payment_received_amount = 0.0
        payment_upi_ref = ""
        payment_sms_text = ""

    set_last_message("Waiting for payment verification...")

    return jsonify(build_status_response(message="Waiting for payment verification..."))


@app.route("/payment", methods=["POST"])
def payment_webhook():
    global payment_waiting, payment_verified, payment_received_amount
    global payment_upi_ref, payment_sms_text

    data = request.get_json(silent=True) or {}
    sms = str(data.get("message", "")).strip()

    if not sms:
        return jsonify({"status": "error", "message": "No message received"}), 400

    print("\n==============================")
    print("SMS RECEIVED:")
    print(sms)

    lower_sms = sms.lower()

    with payment_lock:
        waiting = payment_waiting
        expected_amount = round(payment_expected_amount, 2)

    if not waiting:
        print("[INFO] Payment SMS received but payment is not waiting")
        print("==============================\n")
        return jsonify({"status": "ignored", "paid": False, "message": "Payment session not active"}), 200

    if "credited" not in lower_sms:
        print("[INFO] SMS ignored because it is not a credited message")
        print("==============================\n")
        return jsonify({"status": "ignored", "paid": False, "message": "Not a credited payment message"}), 200

    amount = round(extract_amount_from_sms(sms), 2)
    upi_ref = extract_upi_ref(sms)

    print(f"[DEBUG] Expected Amount : {expected_amount}")
    print(f"[DEBUG] SMS Amount      : {amount}")
    print(f"[DEBUG] UPI Ref         : {upi_ref}")

    if abs(amount - expected_amount) <= 0.01:
        with payment_lock:
            payment_verified = True
            payment_waiting = False
            payment_received_amount = amount
            payment_upi_ref = upi_ref
            payment_sms_text = sms

        save_last_purchase()
        set_last_message("Payment verification completed")

        print("[OK] PAYMENT VERIFICATION COMPLETED")
        print("==============================\n")

        return jsonify({
            "status": "success",
            "paid": True,
            "amount": amount,
            "upi_ref": upi_ref,
            "message": "Payment verification completed"
        }), 200

    print("[ALERT] Payment amount does not match expected total")
    print("==============================\n")

    return jsonify({
        "status": "mismatch",
        "paid": False,
        "expected_amount": expected_amount,
        "received_amount": amount,
        "upi_ref": upi_ref,
        "message": "Payment amount does not match"
    }), 200


@app.route("/api/download_bill", methods=["GET"])
def download_bill():
    purchase = get_last_purchase()
    items = purchase["items"]

    if not items:
        return jsonify({"success": False, "message": "No bill available"}), 404

    pdf_bytes = generate_bill_pdf(purchase)
    filename = f'bill_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# =====================================================
# MAIN
# =====================================================
if __name__ == "__main__":
    print("[INFO] Starting SmartCart POS Web Server...")

    init_firebase()
    print("[INFO] Firebase connected successfully")

    weight_thread = threading.Thread(target=esp32_weight_reader, daemon=True)
    weight_thread.start()

    scanner_thread = threading.Thread(target=barcode_scanner_reader, daemon=True)
    scanner_thread.start()

    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)