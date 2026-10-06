-- Add seller column to products table (run in Supabase SQL editor)
ALTER TABLE products ADD COLUMN IF NOT EXISTS seller TEXT;

CREATE INDEX IF NOT EXISTS idx_products_seller ON products (seller);