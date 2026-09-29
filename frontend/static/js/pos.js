(() => {
  const app = document.querySelector("#pos-app");
  if (!app) return;

  const csrf = app.querySelector("[name=csrfmiddlewaretoken]").value;
  const cart = new Map();
  let heldBillId = null;
  let customerModal;
  let paymentModal;
  let heldModal;
  const money = (value) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(value || 0);
  const number = (value) => Number.parseFloat(value) || 0;
  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  const request = async (url, options = {}) => {
    const response = await fetch(url, {
      ...options,
      headers: { "X-CSRFToken": csrf, ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
    });
    let data;
    try { data = await response.json(); }
    catch { data = { errors: ["The server returned an unexpected response. Please retry or contact an administrator."] }; }
    if (!response.ok) throw new Error((data.errors || ["The request could not be completed."]).join(" "));
    return data;
  };
  const notify = (message, isError = false) => {
    const live = document.querySelector("#pos-live");
    live.innerHTML = `<div class="pos-toast ${isError ? "error" : ""}">${escapeHtml(message)}</div>`;
    window.setTimeout(() => { live.innerHTML = ""; }, 3500);
  };
  const selectedCustomer = () => document.querySelector("#customer-select").value || null;

  function totals() {
    const inclusive = document.querySelector("#tax-inclusive").checked;
    const rawBase = [...cart.values()].reduce((sum, item) => {
      const raw = item.qty * item.price;
      return sum + (inclusive ? raw / (1 + item.tax / 100) : raw);
    }, 0);
    let itemDiscount = 0;
    let afterItemDiscount = 0;
    for (const item of cart.values()) {
      const divisor = inclusive ? 1 + item.tax / 100 : 1;
      const base = item.qty * item.price / divisor;
      const discount = Math.min(item.discount / divisor, base);
      itemDiscount += discount;
      afterItemDiscount += base - discount;
    }
    const discountInput = number(document.querySelector("#bill-discount").value);
    const billDiscount = document.querySelector("#discount-type").value === "PERCENT"
      ? afterItemDiscount * Math.min(discountInput, 100) / 100
      : Math.min(discountInput, afterItemDiscount);
    const taxable = Math.max(afterItemDiscount - billDiscount, 0);
    let tax = 0;
    for (const item of cart.values()) {
      const divisor = inclusive ? 1 + item.tax / 100 : 1;
      const base = item.qty * item.price / divisor;
      const itemNet = Math.max(base - item.discount / divisor, 0);
      const allocation = afterItemDiscount ? billDiscount * itemNet / afterItemDiscount : 0;
      tax += Math.round((itemNet - allocation) * item.tax) / 100;
    }
    const subtotal = rawBase;
    const discount = itemDiscount + billDiscount;
    let grand = Math.max(taxable + tax, 0);
    if (document.querySelector("#round-total").checked) grand = Math.round(grand);
    const discountLimit = app.dataset.isAdmin === "true" ? Number.POSITIVE_INFINITY : rawBase * number(app.dataset.maxDiscount) / 100;
    return { subtotal, discount, tax, grand, discountLimit, discountOverLimit: discount > discountLimit + 0.000001 };
  }

  function syncTotals(result) {
    document.querySelector("#subtotal").textContent = money(result.subtotal);
    document.querySelector("#discount-total").textContent = `−${money(result.discount)}`;
    document.querySelector("#tax-total").textContent = money(result.tax);
    document.querySelector("#grand-total").textContent = money(result.grand);
    document.querySelector("#charge-label").textContent = money(result.grand);
    document.querySelector("#pay-open").disabled = !cart.size || result.discountOverLimit;
    const help = document.querySelector("#discount-limit-help");
    if (help && app.dataset.isAdmin !== "true" && app.dataset.canDiscount === "true") {
      help.textContent = result.discountOverLimit
        ? `Maximum total discount ${money(result.discountLimit)}. Reduce the item or bill discount.`
        : `Maximum total discount ${money(result.discountLimit)} (${app.dataset.maxDiscount}%).`;
      help.classList.toggle("text-danger", result.discountOverLimit);
    }
    const submit = document.querySelector("#checkout-submit");
    if (submit) submit.disabled = result.discountOverLimit;
  }

  function renderCart() {
    const container = document.querySelector("#cart-lines");
    if (!cart.size) {
      container.innerHTML = '<div class="cart-empty"><i class="bi bi-basket"></i><span>Your cart is empty</span><small>Scan a barcode or choose a product.</small></div>';
    } else {
      container.innerHTML = [...cart.values()].map((item) => `
        <article class="cart-line" data-id="${item.id}">
          <div class="line-main"><div><strong>${escapeHtml(item.product)}</strong><small>${escapeHtml([item.variant, item.sku].filter(Boolean).join(" · "))} / ${money(item.price)} ${escapeHtml(item.unit)}</small></div><button class="line-remove" type="button" aria-label="Remove ${escapeHtml(item.product)}"><i class="bi bi-x-lg"></i></button></div>
          <div class="line-edit"><label>Qty <input class="line-qty" type="number" min="0.001" step="0.001" value="${item.qty}"></label><label>Discount <span class="line-discount-wrap"><span>₹</span><input class="line-discount" type="number" min="0" step="0.01" value="${item.discount}"></span></label><strong class="line-total">${money(item.qty * item.price)}</strong></div>
        </article>`).join("");
    }
    document.querySelector("#cart-count").textContent = [...cart.values()].reduce((sum, item) => sum + item.qty, 0).toLocaleString("en-IN", { maximumFractionDigits: 3 });
    syncTotals(totals());
  }

  function addProduct(product) {
    const old = cart.get(product.variant_id);
    cart.set(product.variant_id, old ? { ...old, qty: old.qty + 1 } : {
      id: product.variant_id,
      product: product.product,
      variant: product.variant,
      sku: product.sku,
      unit: product.unit,
      price: number(product.price),
      tax: number(product.tax_percent),
      qty: 1,
      discount: 0,
    });
    heldBillId = null;
    renderCart();
    document.querySelector("#product-search").focus();
  }

  function renderProducts(products) {
    const container = document.querySelector("#product-results");
    document.querySelector("#product-count").textContent = `${products.length} result${products.length === 1 ? "" : "s"}`;
    if (!products.length) {
      container.innerHTML = '<div class="catalog-empty"><i class="bi bi-search"></i><strong>No matching products</strong><span>Try another name, SKU, or barcode.</span></div>';
      return;
    }
    container.innerHTML = products.map((product) => `<button class="product-result" data-product='${escapeHtml(JSON.stringify(product))}' type="button"><span class="product-result-icon"><i class="bi bi-box-seam"></i></span><span class="product-result-name"><strong>${escapeHtml(product.product)}</strong><small>${escapeHtml([product.variant, product.sku].filter(Boolean).join(" · "))}</small></span><span class="product-result-price"><strong>${money(number(product.price))}</strong><small>${escapeHtml(product.unit)} · Stock ${escapeHtml(product.stock)}</small></span><i class="bi bi-plus-circle add-indicator"></i></button>`).join("");
    const query = document.querySelector("#product-search").value.trim().toLowerCase();
    const exact = products.find((item) => [item.sku, item.barcode].some((key) => key && key.toLowerCase() === query));
    if (exact && query.length > 2) {
      addProduct(exact);
      document.querySelector("#product-search").value = "";
      document.querySelector("#product-results").innerHTML = '<div class="catalog-empty"><i class="bi bi-upc-scan"></i><strong>Ready to scan</strong><span>Type a product, SKU, or barcode to begin.</span></div>';
    }
  }

  let searchTimer;
  async function searchProducts() {
    const query = document.querySelector("#product-search").value.trim();
    const category = document.querySelector("#category-filter").value;
    if (!query && !category) {
      document.querySelector("#product-results").innerHTML = '<div class="catalog-empty"><i class="bi bi-upc-scan"></i><strong>Ready to scan</strong><span>Type a product, SKU, or barcode to begin.</span></div>';
      document.querySelector("#product-count").textContent = "Search or scan to add";
      return;
    }
    try {
      const url = new URL(app.dataset.searchUrl, location.origin);
      url.searchParams.set("q", query);
      if (category) url.searchParams.set("category", category);
      const data = await request(url);
      renderProducts(data.results);
    } catch (error) { notify(error.message, true); }
  }

  function paymentRow(mode = "CASH", amount = "") {
    const rows = document.querySelector("#payment-rows");
    const row = document.createElement("div");
    row.className = "payment-row";
    row.innerHTML = `<select class="form-select payment-mode" aria-label="Payment method"><option value="CASH">Cash</option><option value="UPI">UPI</option><option value="CARD">Card</option><option value="CREDIT">Customer credit</option></select><input class="form-control payment-amount" type="number" min="0.01" step="0.01" placeholder="Amount" aria-label="Payment amount" value="${escapeHtml(amount)}"><input class="form-control payment-reference" type="text" maxlength="100" placeholder="Reference (optional)" aria-label="Payment reference"><button class="icon-action payment-remove" type="button" title="Remove payment" aria-label="Remove payment"><i class="bi bi-x-lg"></i></button>`;
    row.querySelector(".payment-mode").value = mode;
    rows.append(row);
    row.querySelectorAll("input,select").forEach((input) => input.addEventListener("input", updateChange));
    row.querySelector(".payment-remove").addEventListener("click", () => { row.remove(); updateChange(); });
  }

  function updateChange() {
    const due = totals().grand;
    const paymentRows = [...document.querySelectorAll(".payment-row")];
    const cash = paymentRows.filter((row) => row.querySelector(".payment-mode").value === "CASH").reduce((sum, row) => sum + number(row.querySelector(".payment-amount").value), 0);
    const other = paymentRows.filter((row) => row.querySelector(".payment-mode").value !== "CASH").reduce((sum, row) => sum + number(row.querySelector(".payment-amount").value), 0);
    document.querySelector("#payment-due").textContent = money(due);
    document.querySelector("#change-preview strong").textContent = money(Math.max(cash - Math.max(due - other, 0), 0));
    document.querySelector("#payment-error").textContent = other > due ? "Non-cash payments cannot exceed the amount due." : "";
  }

  async function loadHeldBills() {
    const list = document.querySelector("#held-list");
    try {
      const data = await request(app.dataset.heldUrl);
      document.querySelector("#held-count").textContent = data.results.length;
      list.innerHTML = data.results.length ? data.results.map((held) => `<div class="held-entry"><button class="held-resume" data-id="${held.id}" type="button"><strong>${escapeHtml(held.label)}</strong><small>${escapeHtml(held.customer)} · ${held.item_count} item(s)</small></button><button class="icon-action held-delete" data-id="${held.id}" type="button" title="Delete held bill" aria-label="Delete held bill"><i class="bi bi-trash3"></i></button></div>`).join("") : '<div class="held-empty">No held bills</div>';
      list.querySelectorAll(".held-resume").forEach((button) => button.addEventListener("click", () => resumeHeld(button.dataset.id)));
      list.querySelectorAll(".held-delete").forEach((button) => button.addEventListener("click", () => deleteHeld(button.dataset.id)));
    } catch (error) { list.innerHTML = `<div class="held-empty text-danger">${escapeHtml(error.message)}</div>`; }
  }

  async function resumeHeld(id) {
    try {
      const data = await request(`${app.dataset.heldUrl}${id}/`);
      cart.clear();
      heldBillId = data.id;
      for (const saved of data.items) {
        const result = await request(`${app.dataset.searchUrl}?q=${encodeURIComponent(saved.sku || "")}`);
        const product = result.results.find((item) => item.variant_id === Number(saved.variant_id));
        if (product) cart.set(product.variant_id, { ...product, id: product.variant_id, price: number(product.price), tax: number(product.tax_percent), qty: number(saved.qty), discount: number(saved.discount) });
      }
      document.querySelector("#customer-select").value = data.customer_id || "";
      renderCart();
      heldModal.hide();
    } catch (error) { notify(error.message, true); }
  }

  async function deleteHeld(id) {
    try { await request(`${app.dataset.heldUrl}${id}/`, { method: "DELETE" }); await loadHeldBills(); }
    catch (error) { notify(error.message, true); }
  }

  document.querySelector("#product-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(searchProducts, 160); });
  document.querySelector("#product-search").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); clearTimeout(searchTimer); searchProducts(); } });
  document.querySelector("#category-filter").addEventListener("change", searchProducts);
  document.querySelector("#tax-inclusive").addEventListener("change", renderCart);
  document.querySelector("#bill-discount").addEventListener("input", renderCart);
  document.querySelector("#discount-type").addEventListener("change", renderCart);
  document.querySelector("#round-total").addEventListener("change", renderCart);
  document.querySelector("#product-results").addEventListener("click", (event) => {
    const button = event.target.closest(".product-result");
    if (button) addProduct(JSON.parse(button.dataset.product));
  });
  document.querySelector("#cart-lines").addEventListener("input", (event) => {
    const row = event.target.closest(".cart-line");
    if (!row) return;
    const item = cart.get(Number(row.dataset.id));
    if (event.target.classList.contains("line-qty")) item.qty = Math.max(number(event.target.value), 0);
    if (event.target.classList.contains("line-discount")) item.discount = Math.max(number(event.target.value), 0);
    const lineTotal = item.qty * item.price;
    row.querySelector(".line-total").textContent = money(lineTotal);
    document.querySelector("#cart-count").textContent = [...cart.values()].reduce((sum, cartItem) => sum + cartItem.qty, 0).toLocaleString("en-IN", { maximumFractionDigits: 3 });
    syncTotals(totals());
    updateChange();
  });
  document.querySelector("#cart-lines").addEventListener("click", (event) => {
    const remove = event.target.closest(".line-remove");
    if (!remove) return;
    cart.delete(Number(remove.closest(".cart-line").dataset.id));
    renderCart();
  });
  document.querySelector("#clear-cart").addEventListener("click", () => {
    if (!cart.size || confirm("Clear all items from this sale?")) { cart.clear(); heldBillId = null; renderCart(); }
  });
  document.querySelector("#hold-bill").addEventListener("click", async () => {
    if (!cart.size) return notify("Add items before holding a bill.", true);
    const items = [...cart.values()].map((item) => ({ variant_id: item.id, qty: item.qty, discount: item.discount, sku: item.sku }));
    try {
      await request(app.dataset.heldUrl, { method: "POST", body: JSON.stringify({ items, customer_id: selectedCustomer(), label: "" }) });
      cart.clear(); heldBillId = null; renderCart(); await loadHeldBills(); notify("Bill held for later.");
    } catch (error) { notify(error.message, true); }
  });
  document.querySelector("#held-open").addEventListener("click", loadHeldBills);
  document.querySelector("#payment-modal").addEventListener("show.bs.modal", () => {
    document.querySelector("#payment-rows").innerHTML = "";
    paymentRow("CASH", totals().grand.toFixed(2));
    updateChange();
  });
  document.querySelector("#add-payment").addEventListener("click", () => { paymentRow("UPI", ""); updateChange(); });
  document.querySelector("#checkout-submit").addEventListener("click", async () => {
    const button = document.querySelector("#checkout-submit");
    const payments = [...document.querySelectorAll(".payment-row")].map((row) => ({
      mode: row.querySelector(".payment-mode").value,
      amount: row.querySelector(".payment-amount").value,
      reference: row.querySelector(".payment-reference").value,
    }));
    if (totals().discountOverLimit) {
      document.querySelector("#payment-error").textContent = `Maximum total discount is ${money(totals().discountLimit)} for this bill.`;
      return;
    }
    const payload = {
      items: [...cart.values()].map((item) => ({ variant_id: item.id, qty: item.qty, discount: item.discount })),
      customer_id: selectedCustomer(),
      discount_type: document.querySelector("#discount-type").value,
      discount_value: document.querySelector("#bill-discount").value,
      tax_inclusive: document.querySelector("#tax-inclusive").checked,
      round_total: document.querySelector("#round-total").checked,
      held_bill_id: heldBillId,
      payments,
    };
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving sale';
    try {
      const data = await request(app.dataset.checkoutUrl, { method: "POST", body: JSON.stringify(payload) });
      location.assign(data.redirect_url);
    } catch (error) {
      document.querySelector("#payment-error").textContent = error.message;
    } finally {
      button.disabled = false;
      button.innerHTML = '<i class="bi bi-check2 me-1"></i>Complete sale';
    }
  });
  document.querySelector("#customer-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const errorBox = document.querySelector("#customer-error");
    errorBox.textContent = "";
    try {
      const data = await request(app.dataset.customerUrl, { method: "POST", body: JSON.stringify({ name: document.querySelector("#customer-name").value, phone: document.querySelector("#customer-phone").value }) });
      const option = new Option(`${data.customer.name}${data.customer.phone ? ` · ${data.customer.phone}` : ""}`, data.customer.id, true, true);
      document.querySelector("#customer-select").add(option);
      customerModal.hide();
      event.target.reset();
    } catch (error) { errorBox.textContent = error.message; }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "F2") { event.preventDefault(); document.querySelector("#product-search").focus(); }
    if (event.key === "F4" && cart.size && !totals().discountOverLimit) { event.preventDefault(); paymentModal.show(); }
    if (event.key === "F8" && cart.size) { event.preventDefault(); document.querySelector("#hold-bill").click(); }
  });

  customerModal = bootstrap.Modal.getOrCreateInstance(document.querySelector("#customer-modal"));
  paymentModal = bootstrap.Modal.getOrCreateInstance(document.querySelector("#payment-modal"));
  heldModal = bootstrap.Modal.getOrCreateInstance(document.querySelector("#held-modal"));
  renderCart();
  loadHeldBills();
})();