#!/usr/bin/env python3
"""
One-time login setup for gajab.com synthetic monitoring.
Saves browser session state so the monitor can reuse it without OTP each time.
"""
from __future__ import annotations
import json
import base64
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

STATE_FILE = Path(__file__).parent / ".gajab_session.json"

def save_state():
    print("=" * 60)
    print("gajab.com Login Setup — One-time session save")
    print("=" * 60)
    
    phone = input("Enter your Indian mobile number (10 digits, e.g. 9876543210): ").strip()
    
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 430, "height": 932},
            is_mobile=True, has_touch=True,
            geolocation={"latitude": 19.4560, "longitude": 72.8054},
            locale="en-IN", timezone_id="Asia/Kolkata",
        )
        page = context.new_page()
        
        print("\n1. Opening gajab.com login page...")
        page.goto("https://gajab.com/auth/signin", wait_until="domcontentloaded")
        page.wait_for_load_state("load", timeout=15000)
        time.sleep(1)
        
        print("2. Entering phone number...")
        phone_input = page.locator('input[type="tel"]')
        phone_input.fill(phone)
        time.sleep(0.5)
        
        print("3. Checking terms checkbox...")
        checkbox = page.locator('input[type="checkbox"]')
        checkbox.click()
        time.sleep(0.5)
        
        print("4. Requesting OTP...")
        otp_button = page.locator('button:has-text("Request OTP")')
        otp_button.click()
        
        print(f"\n   ⏳ OTP sent to {phone}")
        print("   Check your phone for the SMS")
        otp = input("   Enter the OTP code received: ").strip()
        
        print("5. Entering OTP digit by digit...")
        otp_fields = page.locator("input[maxlength='1'][inputmode='numeric']")
        count = otp_fields.count()
        print(f"   Found {count} OTP digit inputs")
        first = otp_fields.first
        if first.is_visible():
            first.click()
            time.sleep(0.3)
            page.keyboard.type(otp, delay=0.15)
        time.sleep(2)
        
        # Check if there's a submit/verify button
        submit = page.locator("button:has-text('Submit'), button:has-text('Verify'), button:has-text('Login')")
        if submit.count() > 0 and submit.first.is_enabled():
            submit.first.click()
            time.sleep(3)
        
        print(f"6. Current URL: {page.url}")
        print("   If login was successful, the URL should no longer be /auth/signin")
        
        success = input("\nWas login successful? (y/n): ").strip().lower()
        if success == "y":
            state = context.storage_state()
            with open(STATE_FILE, "w") as f:
                json.dump(state, f)
            print(f"\n✅ Session saved to {STATE_FILE}")
            print(f"   File size: {STATE_FILE.stat().st_size / 1024:.1f}KB")
            
            # Also save as base64 for use in GitHub Secrets
            with open(STATE_FILE, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            print(f"   Base64 length: {len(b64)} chars")
            print("\nTo use in GitHub Actions, add this as a secret named GAJAB_SESSION:")
            print(f"   (copy the session file content to a GitHub secret)")
        else:
            print("\n❌ Login failed. Check the browser window and try again.")
        
        browser.close()

if __name__ == "__main__":
    save_state()
