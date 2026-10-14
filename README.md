# SmartCart POS System

An intelligent retail point-of-sale application integrating Raspberry Pi, ESP32, barcode scanner, load cell weight sensor, Firebase Realtime Database, Python Flask backend, UPI QR generation, and PDF receipt downloads.

## Features
- **Barcode Scanning & Validation**: Seamlessly scans items and verifies products against Firebase Realtime Database.
- **Weight Sensor Integration (HX711 & ESP32)**: Ensures weight matches expected product weights to prevent theft or scanning errors.
- **Dynamic UPI Payment**: Automatically generates QR codes for precise billing amounts.
- **Admin Panel**: Web interface for managing inventory, pricing, and weights.
- **Automated Bill Generation**: Creates professional PDF receipts for customers.

## Setup Instructions
1. Configure your Firebase credentials and database URL in `app.py` and front-end HTML files.
2. Set up your UPI ID in `app.py`.
3. Install required Python packages:
   ```bash
   pip install flask pyserial firebase-admin evdev qrcode[pil] reportlab
   ```
4. Run the application:
   ```bash
   python app.py
   ```
