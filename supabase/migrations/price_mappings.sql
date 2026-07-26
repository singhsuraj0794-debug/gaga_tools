-- Run this in Supabase SQL Editor to create the price_mappings table
CREATE TABLE IF NOT EXISTS price_mappings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  gajab_product_id TEXT NOT NULL,
  gajab_title TEXT NOT NULL,
  gajab_image_url TEXT,
  gajab_price TEXT,
  gajab_url TEXT,
  meesho_url TEXT,
  meesho_price TEXT,
  meesho_match_score FLOAT,
  flipkart_url TEXT,
  flipkart_price TEXT,
  flipkart_match_score FLOAT,
  amazon_url TEXT,
  amazon_price TEXT,
  amazon_match_score FLOAT,
  search_error TEXT,
  last_checked TIMESTAMPTZ DEFAULT NOW(),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(gajab_product_id)
);
