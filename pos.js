const barcodeInput = document.getElementById("barcode-input");
const cartItems = document.getElementById("cart-items");

const summaryItems = document.getElementById("summary-items");
const summaryExpectedWeight = document.getElementById("summary-expected-weight");
const summaryActualWeight = document.getElementById("summary-actual-weight");
const summaryTotalPrice = document.getElementById("summary-total-price");

const weightStatusIndicator = document.getElementById("weight-status-indicator");
const btnClear = document.getElementById("btn-clear");
const btnCheckout = document.getElementById("btn-checkout");

const scannerStatus = document.getElementById("scanner-status");
const scaleStatus = document.getElementById("scale-status");

const paymentModal = document.getElementById("payment-modal");
const paymentAmount = document.getElementById("payment-amount");
const btnPaymentCancel = document.getElementById("btn-payment-cancel");
const dynamicQrImage = document.getElementById("dynamic-qr-image");
const upiPayLink = document.getElementById("upi-pay-link");

const successModal = document.getElementById("success-modal");
const btnSuccessClose = document.getElementById("btn-success-close");
const purchaseInfo = document.getElementById("purchase-info");
const purchaseTableBody = document.getElementById("purchase-table-body");
const purchaseTotal = document.getElementById("purchase-total");

let lastShownMessage = "";
let paymentVerifiedShown = false;

function showToast(message, type = "info") {
    if (!message) return;

    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;

    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add("toast-hide");
        setTimeout(() => toast.remove(), 300);
    }, 2500);
}

function openPaymentModal(totalPriceText) {
    paymentAmount.textContent = totalPriceText;
    paymentModal.classList.remove("hidden");
}

function closePaymentModal() {
    paymentModal.classList.add("hidden");
}

function openSuccessModal() {
    successModal.classList.remove("hidden");
}

function closeSuccessModal() {
    successModal.classList.add("hidden");
}

function renderCart(cart) {
    if (!cart || cart.length === 0) {
        cartItems.innerHTML = `
            <div class="empty-cart-message">
                <ion-icon name="cart-outline"></ion-icon>
                <p>Your cart is empty.</p>
                <span class="empty-subtext">Scan a barcode to add items</span>
            </div>
        `;
        return;
    }

    const rows = cart.map((item, index) => `
        <tr>
            <td>${index + 1}</td>
            <td>${item.barcode}</td>
            <td>${item.name}</td>
            <td>₹${Number(item.price).toFixed(2)}</td>
            <td>${Number(item.weight).toFixed(2)} g</td>
            <td>
                <button class="btn-remove-item" onclick="removeItem(${index})">
                    Remove
                </button>
            </td>
        </tr>
    `).join("");

    cartItems.innerHTML = `
        <div class="cart-table-wrapper">
            <table class="cart-table">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Barcode</th>
                        <th>Product Name</th>
                        <th>Price</th>
                        <th>Weight</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `;
}

function renderPurchaseTable(purchase) {
    const items = purchase.items || [];

    if (!items.length) {
        purchaseTableBody.innerHTML = `
            <tr>
                <td colspan="5">No purchase data</td>
            </tr>
        `;
        purchaseInfo.textContent = "";
        purchaseTotal.textContent = "Total: ₹0.00";
        return;
    }

    purchaseInfo.textContent = `Purchase Time: ${purchase.time || "-"} | UPI Ref: ${purchase.upi_ref || "-"}`;

    purchaseTableBody.innerHTML = items.map((item, index) => `
        <tr>
            <td>${index + 1}</td>
            <td>${item.barcode}</td>
            <td>${item.name}</td>
            <td>₹${Number(item.price).toFixed(2)}</td>
            <td>${Number(item.weight).toFixed(2)} g</td>
        </tr>
    `).join("");

    purchaseTotal.textContent = `Total: ₹${Number(purchase.total || 0).toFixed(2)}`;
}

function updateSummary(data) {
    const summary = data.summary;
    const cart = data.cart || [];
    const payment = data.payment || {};
    const purchase = data.purchase || {};

    renderCart(cart);

    summaryItems.textContent = summary.total_items;
    summaryExpectedWeight.textContent = `${(summary.expected_weight_g / 1000).toFixed(2)} kg`;
    summaryActualWeight.textContent = `${(summary.actual_weight_g / 1000).toFixed(2)} kg`;
    summaryTotalPrice.textContent = `₹${Number(summary.total_price).toFixed(2)}`;

    if (summary.weight_ok) {
        weightStatusIndicator.className = "weight-status match";
        weightStatusIndicator.innerHTML = `
            <ion-icon name="checkmark-circle"></ion-icon>
            <span>${summary.weight_status_text}</span>
        `;
    } else {
        weightStatusIndicator.className = "weight-status mismatch";
        weightStatusIndicator.innerHTML = `
            <ion-icon name="warning-outline"></ion-icon>
            <span>${summary.weight_status_text}</span>
        `;
    }

    if (data.scanner_connected) {
        if (data.scanner_enabled) {
            scannerStatus.className = "status-badge success";
            scannerStatus.innerHTML = `
                <ion-icon name="barcode-outline"></ion-icon>
                <span>Scanner: Ready</span>
            `;
        } else {
            scannerStatus.className = "status-badge danger";
            scannerStatus.innerHTML = `
                <ion-icon name="pause-circle-outline"></ion-icon>
                <span>Scanner: Stopped</span>
            `;
        }
    } else {
        scannerStatus.className = "status-badge danger";
        scannerStatus.innerHTML = `
            <ion-icon name="alert-circle-outline"></ion-icon>
            <span>Scanner: Disconnected</span>
        `;
    }

    if (data.serial_connected) {
        scaleStatus.className = "status-badge success";
        scaleStatus.innerHTML = `
            <ion-icon name="scale-outline"></ion-icon>
            <span>Scale: Active</span>
        `;
    } else {
        scaleStatus.className = "status-badge danger";
        scaleStatus.innerHTML = `
            <ion-icon name="alert-circle-outline"></ion-icon>
            <span>Scale: Disconnected</span>
        `;
    }

    btnCheckout.disabled = !summary.checkout_enabled;

    if (summary.checkout_enabled) {
        btnCheckout.classList.add("checkout-enabled");
        btnCheckout.classList.remove("checkout-disabled");
    } else {
        btnCheckout.classList.add("checkout-disabled");
        btnCheckout.classList.remove("checkout-enabled");
    }

    barcodeInput.disabled = !data.scanner_enabled;

    if (payment.qr_url) {
        dynamicQrImage.src = `/dynamic_payment_qr.png?t=${Date.now()}`;
        upiPayLink.href = payment.qr_url;
    } else {
        upiPayLink.href = "#";
    }

    if (payment.waiting) {
        openPaymentModal(`₹${Number(payment.expected_amount).toFixed(2)}`);
    }

    if (payment.verified && !paymentVerifiedShown) {
        renderPurchaseTable(purchase);
        closePaymentModal();
        openSuccessModal();
        paymentVerifiedShown = true;
        showToast("Payment verification completed", "success");
    }

    if (!payment.waiting && !payment.verified) {
        closePaymentModal();
    }

    if (!payment.verified) {
        paymentVerifiedShown = false;
    }
}

async function fetchStatus() {
    try {
        const response = await fetch("/api/status");
        const data = await response.json();
        updateSummary(data);

        if (data.message && data.message !== lastShownMessage) {
            let type = "info";

            if (
                data.message.includes("added to cart") ||
                data.message.includes("cleared") ||
                data.message.includes("completed") ||
                data.message.includes("removed from cart") ||
                data.message.includes("verification completed")
            ) {
                type = "success";
            }

            if (
                data.message.includes("not matched") ||
                data.message.includes("mismatch") ||
                data.message.includes("Invalid") ||
                data.message.includes("Please add scanned product") ||
                data.message.includes("disabled")
            ) {
                type = "error";
            }

            showToast(data.message, type);
            lastShownMessage = data.message;
        }
    } catch (error) {
        console.error("Status fetch error:", error);
    }
}

async function clearCart() {
    try {
        const response = await fetch("/api/clear", { method: "POST" });
        const data = await response.json();

        if (data.success) {
            updateSummary(data);
            showToast(data.message, "success");
            lastShownMessage = data.message;
        } else {
            showToast(data.message, "error");
        }
    } catch (error) {
        console.error("Clear error:", error);
        showToast("Failed to clear cart", "error");
    }
}

async function checkoutCart() {
    try {
        const response = await fetch("/api/checkout", { method: "POST" });
        const data = await response.json();

        if (data.success) {
            updateSummary(data);
            showToast(data.message, "success");
            lastShownMessage = data.message;
            openPaymentModal(summaryTotalPrice.textContent);
        } else {
            showToast(data.message, "error");
        }
    } catch (error) {
        console.error("Checkout error:", error);
        showToast("Checkout failed", "error");
    }
}

async function removeItem(index) {
    try {
        const response = await fetch("/api/remove_item", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ index })
        });

        const data = await response.json();

        if (data.success) {
            updateSummary(data);
            showToast(data.message, "success");
            lastShownMessage = data.message;
        } else {
            showToast(data.message, "error");
        }
    } catch (error) {
        console.error("Remove item error:", error);
        showToast("Failed to remove item", "error");
    }
}

async function cancelPayment() {
    closePaymentModal();
    await clearCart();
}

async function finishSuccess() {
    closeSuccessModal();
    await clearCart();
}

barcodeInput.addEventListener("keydown", async function (event) {
    if (event.key === "Enter") {
        const barcode = barcodeInput.value.trim();
        if (!barcode) return;

        try {
            const response = await fetch("/api/scan", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ barcode })
            });

            const data = await response.json();

            if (data.success) {
                updateSummary(data);
                showToast(data.message, "success");
                lastShownMessage = data.message;
            } else {
                showToast(data.message, "error");
            }
        } catch (error) {
            console.error("Manual scan error:", error);
            showToast("Server error while scanning", "error");
        } finally {
            barcodeInput.value = "";
        }
    }
});

btnClear.addEventListener("click", clearCart);
btnCheckout.addEventListener("click", checkoutCart);
btnPaymentCancel.addEventListener("click", cancelPayment);
btnSuccessClose.addEventListener("click", finishSuccess);

setInterval(fetchStatus, 1000);
fetchStatus();