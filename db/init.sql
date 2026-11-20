CREATE TABLE IF NOT EXISTS bank_transactions_raw (
  account_number text, booking_date text, value_date text, transaction_type text,
  payment_reference text, counterparty text, counterparty_account text,
  bank_code text, amount text, currency text, info text
);
\copy bank_transactions_raw FROM '/tmp/statement.csv' WITH (FORMAT csv, DELIMITER ';', HEADER true)
CREATE OR REPLACE VIEW bank_transactions AS
SELECT to_date(booking_date, 'DD.MM.YY') AS booking_date,
       to_date(value_date, 'DD.MM.YY')   AS value_date,
       transaction_type, payment_reference, counterparty,
       counterparty_account, bank_code,
       replace(replace(amount, '.', ''), ',', '.')::numeric AS amount,
       currency
FROM bank_transactions_raw;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO cfo;
