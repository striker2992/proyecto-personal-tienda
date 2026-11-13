const STORAGE_KEY = 'container-store-cart';

const cartButton = document.getElementById('cart-button');
const cartPanel = document.getElementById('cart-panel');
const cartOverlay = document.getElementById('cart-overlay');
const closeCartBtn = document.getElementById('close-cart');
const productList = document.getElementById('product-list');
const cartItems = document.getElementById('cart-items');
const cartCounter = document.getElementById('cart-count');
const cartQtySummary = document.getElementById('cart-qty-summary');
const cartSubtotal = document.getElementById('cart-subtotal');
const cartTotal = document.getElementById('cart-total');
const checkoutForm = document.getElementById('order-checkout-form');
const checkoutItems = document.getElementById('checkout-items');
const checkoutButton = document.getElementById('checkout-submit');
const catalog = Array.from(document.querySelectorAll('.btn-add')).map((button) => ({
  id: button.dataset.id,
  name: button.dataset.name,
  price: Number(button.dataset.price),
  emoji: button.dataset.emoji,
  stock: Number(button.dataset.stock),
}));
const catalogById = new Map(catalog.map((product) => [product.id, product]));

const formatCurrency = (value) =>
  new Intl.NumberFormat('es-CL', {
    style: 'currency',
    currency: 'CLP',
    maximumFractionDigits: 0,
  }).format(value);

document.querySelectorAll('.price[data-price]').forEach((priceElement) => {
  priceElement.textContent = formatCurrency(Number(priceElement.dataset.price));
});

const contactDisclosure = document.getElementById('contact-disclosure');
if (contactDisclosure) {
  document.querySelectorAll('a[href$="#contacto"]').forEach((link) => {
    link.addEventListener('click', () => {
      contactDisclosure.open = true;
    });
  });

  if (window.location.hash === '#contacto') {
    contactDisclosure.open = true;
  }
}

function loadCart() {
  try {
    const savedCart = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    if (!Array.isArray(savedCart)) return [];

    return savedCart
      .filter((entry) => entry && typeof entry === 'object'
        && catalogById.has(String(entry.id))
        && Number.isSafeInteger(entry.quantity)
        && entry.quantity > 0
        && catalogById.get(String(entry.id)).stock > 0)
      .map((entry) => ({
        id: String(entry.id),
        quantity: Math.min(entry.quantity, catalogById.get(String(entry.id)).stock),
      }));
  } catch (error) {
    console.warn('No se pudo leer el carrito guardado; se iniciará vacío.', error);
    return [];
  }
}

let cart = loadCart();

function saveCart() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(cart));
}

function getCartCount() {
  return cart.reduce((sum, item) => sum + item.quantity, 0);
}

function getCartSubtotal() {
  return cart.reduce((sum, item) => sum + catalogById.get(item.id).price * item.quantity, 0);
}

function renderCart() {
  const subtotal = getCartSubtotal();
  const count = getCartCount();

  cartCounter.textContent = count;
  cartQtySummary.textContent = count;
  cartSubtotal.textContent = formatCurrency(subtotal);
  cartTotal.textContent = formatCurrency(subtotal);
  if (checkoutButton) checkoutButton.disabled = cart.length === 0;
  catalog.forEach((product) => {
    const addButton = document.querySelector(`.btn-add[data-id="${product.id}"]`);
    const stockLabel = document.querySelector(`[data-stock-label="${product.id}"]`);
    const quantityInCart = cart.find((item) => item.id === product.id)?.quantity || 0;
    const remaining = Math.max(0, product.stock - quantityInCart);

    addButton.disabled = remaining === 0;
    stockLabel.textContent = remaining > 0
      ? `Existencias: ${product.stock} · Puedes añadir: ${remaining}`
      : product.stock === 0 ? 'Agotado' : 'Límite de existencias alcanzado';
    stockLabel.classList.toggle('out-of-stock', remaining === 0);
  });

  if (!cart.length) {
    cartItems.innerHTML = '<div class="empty-cart">Tu carrito está vacío. Agrega un contenedor para comenzar.</div>';
    return;
  }

  cartItems.innerHTML = cart.map((entry) => {
    const product = catalogById.get(entry.id);
    return `
    <div class="cart-item">
      <div class="cart-item-emoji">${escapeHtml(product.emoji)}</div>
      <div>
        <span class="cart-item-name">${escapeHtml(product.name)}</span>
        <div class="cart-item-price">${formatCurrency(product.price)} c/u</div>
      </div>
      <div class="cart-item-controls">
        <button class="qty-button" type="button" data-action="decrease" data-id="${entry.id}" aria-label="Disminuir cantidad">−</button>
        <span class="qty-value">${entry.quantity}</span>
        <button class="qty-button" type="button" data-action="increase" data-id="${entry.id}" aria-label="Aumentar cantidad">+</button>
        <button class="remove-link" type="button" data-action="remove" data-id="${entry.id}">Eliminar</button>
      </div>
    </div>
  `;
  }).join('');
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]);
}

function addToCart(productId) {
  const product = catalogById.get(String(productId));
  if (!product) return;

  const existing = cart.find((item) => item.id === product.id);
  if ((existing?.quantity || 0) >= product.stock) return;

  if (existing) {
    existing.quantity += 1;
  } else {
    cart.push({ id: product.id, quantity: 1 });
  }

  saveCart();
  renderCart();
  openCart();
}

function updateQuantity(productId, delta) {
  const item = cart.find((entry) => entry.id === productId);
  if (!item) return;

  const product = catalogById.get(item.id);
  if (delta > 0 && item.quantity >= product.stock) return;

  if (delta < 0 && item.quantity <= 1) {
    cart = cart.filter((entry) => entry.id !== productId);
  } else {
    item.quantity += delta;
  }

  saveCart();
  renderCart();
}

function openCart() {
  cartPanel.classList.add('open');
  cartOverlay.classList.add('open');
  cartPanel.setAttribute('aria-hidden', 'false');
}

function closeCart() {
  cartPanel.classList.remove('open');
  cartOverlay.classList.remove('open');
  cartPanel.setAttribute('aria-hidden', 'true');
}

productList.addEventListener('click', (event) => {
  const button = event.target.closest('.btn-add');
  if (!button) return;
  addToCart(button.dataset.id);
});

cartButton.addEventListener('click', openCart);
closeCartBtn.addEventListener('click', closeCart);
cartOverlay.addEventListener('click', closeCart);

cartItems.addEventListener('click', (event) => {
  const trigger = event.target.closest('[data-action]');
  if (!trigger) return;

  const { action, id } = trigger.dataset;

  if (action === 'increase') updateQuantity(id, 1);
  if (action === 'decrease') updateQuantity(id, -1);
  if (action === 'remove') {
    cart = cart.filter((item) => item.id !== id);
    saveCart();
    renderCart();
  }
});

if (checkoutForm) {
  checkoutForm.addEventListener('submit', (event) => {
    if (!cart.length) {
      event.preventDefault();
      return;
    }

    checkoutItems.value = JSON.stringify(cart);
    checkoutButton.disabled = true;
  });
}

renderCart();
