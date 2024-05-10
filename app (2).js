let currentUser = null;
let products = [];
let cart = { items: [], total_cents: 0 };

const $ = (id) => document.getElementById(id);
const money = (c) => (c / 100).toLocaleString('ru-RU', { style: 'currency', currency: 'RUB' });

function el(tag, props = {}, ...kids) {
    const n = Object.assign(document.createElement(tag), props); // только textContent, без innerHTML
    n.append(...kids);
    return n;
}

async function api(path, method = 'GET', body) {
    const res = await fetch('/api' + path, {
        method,
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
        body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 422) throw new Error('Проверьте email и пароль (пароль от 10 символов).');
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Ошибка запроса');
    return data;
}

function showNotification(text) {
    const t = el('div', { className: 'toast', textContent: text });
    $('toasts').append(t);
    setTimeout(() => t.remove(), 3500);
}

function updateProductsDisplay() {
    $('products').replaceChildren(...products.map((p) => {
        const inCart = cart.items.find((i) => i.id === p.id)?.qty || 0;
        const limit = Math.min(20, p.stock);
        const add = el('button', {
            className: 'main',
            textContent: p.stock ? 'В корзину' : 'Нет в наличии',
            disabled: !p.stock || inCart >= limit,
            onclick: () => setQty(p.id, inCart + 1),
        });
        return el('li', {},
            el('div', {},
                el('b', { textContent: p.name }), el('br'),
                el('span', { className: 'sub', textContent: `${p.origin}: ${p.notes}` }), el('br'),
                el('span', { className: 'roast', title: 'Обжарка', textContent: '■'.repeat(p.roast) + '□'.repeat(5 - p.roast) }),
                p.stock > 0 && p.stock <= 5 ? el('span', { className: 'low', textContent: `  осталось ${p.stock}` }) : ''),
            el('div', { className: 'price' }, el('div', { textContent: money(p.price_cents) }), add));
    }));
}

function updateCartDisplay() {
    $('cart').replaceChildren(...(cart.items.length ? cart.items.map((i) => el('li', {},
        el('span', { textContent: i.name }),
        el('b', { textContent: money(i.price_cents * i.qty) }),
        el('div', { className: 'qty' },
            el('button', { textContent: '−', title: 'Убрать одну', onclick: () => setQty(i.id, i.qty - 1) }),
            el('span', { textContent: i.qty }),
            el('button', { textContent: '+', title: 'Добавить ещё', disabled: i.qty >= Math.min(20, i.stock), onclick: () => setQty(i.id, i.qty + 1) }))))
        : [el('li', { className: 'sub', textContent: 'Пока пусто. Выберите кофе слева.' })]));
    $('total').textContent = money(cart.total_cents);
    $('btn-checkout').disabled = !cart.items.length;
}

async function loadProducts() {
    products = await api('/products');
}

async function loadOrders() {
    const orders = await api('/orders');
    $('orders-box').hidden = !orders.length;
    $('orders').replaceChildren(...orders.map((o) => el('li', { className: 'order' },
        el('b', { textContent: `Заказ №${o.id} на ${money(o.total_cents)}` }),
        el('div', { className: 'sub', textContent: new Date(o.created_at).toLocaleDateString('ru-RU') }),
        el('div', { textContent: o.items.map((i) => `${i.name} × ${i.qty}`).join(', ') }))));
}

async function loadProfile() {
    try { currentUser = await api('/me'); } catch { currentUser = null; }
    $('guest').hidden = !!currentUser;
    $('cartbox').hidden = !currentUser;
    $('btn-logout').hidden = !currentUser;
    $('who').textContent = currentUser ? currentUser.email : '';
    cart = { items: [], total_cents: 0 };
    await loadProducts();
    if (currentUser) {
        cart = await api('/cart');
        await loadOrders();
    } else {
        $('orders-box').hidden = true;
    }
    updateProductsDisplay();
    updateCartDisplay();
}

async function setQty(productId, qty) {
    if (!currentUser) return showNotification('Войдите, чтобы добавить товар в корзину');
    try {
        await api('/cart', 'PUT', { product_id: productId, qty });
    } catch (e) { showNotification(e.message); }
    await loadProfile();
}

async function checkout() {
    try {
        const { order_id } = await api('/checkout', 'POST');
        showNotification(`Заказ №${order_id} оформлен`);
    } catch (e) { showNotification(e.message); }
    await loadProfile();
}

async function submitAuth(mode) {
    try {
        await api('/' + mode, 'POST', { email: $('email').value.trim(), password: $('password').value });
        $('password').value = '';
        await loadProfile();
    } catch (e) { showNotification(e.message); }
}

$('btn-login').onclick = () => submitAuth('login');
$('btn-register').onclick = () => submitAuth('register');
$('btn-checkout').onclick = checkout;
$('btn-logout').onclick = async () => { await api('/logout', 'POST'); await loadProfile(); };

loadProfile();
