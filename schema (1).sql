-- psql -d shop -f schema.sql  (от владельца БД)
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE users (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email         citext NOT NULL UNIQUE CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  password_hash text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
  token_hash bytea PRIMARY KEY,                       -- sha256 токена, не сам токен
  user_id    bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  expires_at timestamptz NOT NULL
);
CREATE INDEX sessions_user_idx ON sessions (user_id);
CREATE INDEX sessions_exp_idx  ON sessions (expires_at);

CREATE TABLE products (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name        text NOT NULL,
  origin      text NOT NULL,
  notes       text NOT NULL,
  roast       int  NOT NULL CHECK (roast BETWEEN 1 AND 5),
  price_cents int  NOT NULL CHECK (price_cents > 0),   -- деньги только целыми копейками
  stock       int  NOT NULL CHECK (stock >= 0)         -- склад не уйдёт в минус
);

CREATE TABLE cart_items (
  user_id    bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  product_id bigint NOT NULL REFERENCES products,
  qty        int NOT NULL CHECK (qty BETWEEN 1 AND 20),
  PRIMARY KEY (user_id, product_id)
);

CREATE TABLE orders (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id     bigint NOT NULL REFERENCES users,
  total_cents int NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX orders_user_idx ON orders (user_id, id DESC);

CREATE TABLE order_items (                              -- цена фиксируется на момент покупки
  order_id    bigint NOT NULL REFERENCES orders ON DELETE CASCADE,
  product_id  bigint NOT NULL REFERENCES products,
  qty         int NOT NULL CHECK (qty > 0),
  price_cents int NOT NULL CHECK (price_cents > 0),
  PRIMARY KEY (order_id, product_id)
);

-- Оформление заказа: одна транзакция, всё или ничего.
CREATE FUNCTION place_order(p_user bigint) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_order bigint; v_n int;
BEGIN
  PERFORM 1 FROM users WHERE id = p_user FOR UPDATE;      -- двойной клик не создаст два заказа
  PERFORM 1 FROM products
   WHERE id IN (SELECT product_id FROM cart_items WHERE user_id = p_user)
   ORDER BY id FOR UPDATE;                                -- порядок id: без deadlock между покупателями
  IF NOT EXISTS (SELECT 1 FROM cart_items WHERE user_id = p_user) THEN
    RAISE EXCEPTION 'empty cart' USING ERRCODE = 'SH001';
  END IF;
  UPDATE products p SET stock = p.stock - c.qty            -- списываем только если хватает
    FROM cart_items c
   WHERE c.user_id = p_user AND c.product_id = p.id AND p.stock >= c.qty;
  GET DIAGNOSTICS v_n = ROW_COUNT;
  IF v_n <> (SELECT count(*) FROM cart_items WHERE user_id = p_user) THEN
    RAISE EXCEPTION 'out of stock' USING ERRCODE = 'SH002';   -- откатит и все списания выше
  END IF;
  INSERT INTO orders (user_id) VALUES (p_user) RETURNING id INTO v_order;
  INSERT INTO order_items (order_id, product_id, qty, price_cents)
    SELECT v_order, p.id, c.qty, p.price_cents               -- цена берётся из БД, не от клиента
      FROM cart_items c JOIN products p ON p.id = c.product_id WHERE c.user_id = p_user;
  UPDATE orders SET total_cents = (SELECT sum(qty * price_cents) FROM order_items WHERE order_id = v_order)
   WHERE id = v_order;
  DELETE FROM cart_items WHERE user_id = p_user;
  RETURN v_order;
END $$;

-- Права по минимуму: приложение не может менять склад, цены и заказы напрямую.
REVOKE EXECUTE ON FUNCTION place_order FROM PUBLIC;
CREATE ROLE shop_app LOGIN PASSWORD 'CHANGE_ME';
GRANT SELECT, INSERT         ON users       TO shop_app;
GRANT SELECT, INSERT, DELETE ON sessions    TO shop_app;
GRANT SELECT                 ON products, orders, order_items TO shop_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON cart_items TO shop_app;
GRANT EXECUTE ON FUNCTION place_order(bigint) TO shop_app;

INSERT INTO products (name, origin, notes, roast, price_cents, stock) VALUES
 ('Йирга-Чеффе', 'Эфиопия',   'жасмин, бергамот, чёрный чай', 1, 89000, 24),
 ('Гуатемала Уэуэтенанго', 'Гватемала', 'какао, красное яблоко', 2, 79000, 18),
 ('Кения АА', 'Кения',        'смородина, грейпфрут',          2, 99000, 6),
 ('Сантос', 'Бразилия',       'орех, молочный шоколад',        3, 64000, 40),
 ('Эспрессо-смесь №4', 'Бразилия и Колумбия', 'карамель, тёмный шоколад', 4, 69000, 30),
 ('Суматра Мандхелинг', 'Индонезия', 'пряности, кедр, табак',  5, 84000, 3);
