# magazin
Магазин кофе

Это сайт, где можно купить кофе. Выбираешь кофе, кладёшь в корзину и делаешь заказ.

Как запустить
В файле schema.sql замени слово CHANGE_ME на свой пароль.
Сделай базу: createdb shop
Заполни её: psql -d shop -f schema.sql
Поставь нужное: pip install fastapi uvicorn "psycopg[binary]" psycopg-pool argon2-cffi
Запусти сайт:
