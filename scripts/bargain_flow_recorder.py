"""
bargain_flow_recorder.py — Records gajab.com bargain flow (430x932 viewport).
"""

import argparse
import time
import re
from pathlib import Path
from urllib.parse import quote
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path("./bargain_recordings")
OUTPUT_DIR.mkdir(exist_ok=True)

VIEWPORT = {"width": 430, "height": 932}
DEVICE_SCALE_FACTOR = 2
GEOLOCATION = {"latitude": 19.4560, "longitude": 72.8054}


def log(msg):
    print(f"[BARGAIN] {msg}", flush=True)


def show_tap(page, x, y):
    page.evaluate("""([x, y]) => {
        const tap = document.createElement('div');
        tap.style.cssText = `position:fixed;left:${x-22}px;top:${y-22}px;width:44px;height:44px;border-radius:50%;background:rgba(251,126,1,0.35);border:3px solid #FB7E01;pointer-events:none;z-index:999999;transition:transform .3s ease-out,opacity .3s ease-out;transform:scale(0.3);opacity:1`;
        document.body.appendChild(tap);
        requestAnimationFrame(() => { tap.style.transform = 'scale(1.8)'; tap.style.opacity = '0.6'; });
        setTimeout(() => { tap.style.transform = 'scale(2.5)'; tap.style.opacity = '0'; }, 150);
        setTimeout(() => tap.remove(), 500);
    }""", [x, y])


def dismiss_overlays(page):
    for _ in range(3):
        try:
            page.keyboard.press("Escape")
            time.sleep(0.3)
        except Exception:
            pass
    try:
        backdrops = page.locator(".react-modal-sheet-backdrop")
        if backdrops.count() > 0:
            backdrops.first.click(timeout=2000)
            time.sleep(0.3)
    except Exception:
        pass


def run_bargain_flow(page, product_url: str):
    encoded = quote(product_url, safe="/:?&=#%")
    log(f"Navigating to: {encoded}")

    page.goto(encoded, wait_until="domcontentloaded")
    page.wait_for_load_state("load", timeout=15000)
    time.sleep(2)

    log(f"Page title: {page.title()}")
    log(f"Current URL: {page.url}")

    # Center the page content
    page.add_style_tag(content="""
        .max-w-\\[1600px\\] { margin-left: auto !important; margin-right: auto !important; }
        body > div { margin-left: auto; margin-right: auto; }
    """)

    dismiss_overlays(page)
    time.sleep(0.5)

    # ── Step 1: Click "Start Bargaining" inside #varient-price ──
    log("Scrolling to #varient-price...")
    page.evaluate("() => { const el = document.getElementById('varient-price'); if (el) el.scrollIntoView({behavior:'instant',block:'center'}); }")
    time.sleep(0.5)

    btn_info = page.evaluate("""() => {
        const vp = document.getElementById('varient-price');
        if (!vp) return null;
        for (const btn of vp.querySelectorAll('button')) {
            if (btn.textContent.includes('Start Bargaining')) {
                btn.removeAttribute('disabled');
                btn.style.pointerEvents = 'auto';
                btn.style.opacity = '1';
                btn.scrollIntoView({behavior:'instant',block:'center'});
                const r = btn.getBoundingClientRect();
                return {x: r.x + r.width/2, y: r.y + r.height/2};
            }
        }
        return null;
    }""")

    if not btn_info:
        raise Exception("Start Bargaining button not found in #varient-price")

    log(f"Start Bargaining at ({btn_info['x']:.0f}, {btn_info['y']:.0f})")
    show_tap(page, btn_info["x"], btn_info["y"])
    time.sleep(0.15)
    page.mouse.click(btn_info["x"], btn_info["y"])
    log("Clicked Start Bargaining")
    time.sleep(3)

    dismiss_overlays(page)
    time.sleep(0.5)

    # ── Step 2: Move price slider via React onChange ──
    log("Setting slider to ~40% discount...")

    slider_result = page.evaluate("""() => {
        const ranges = document.querySelectorAll('input[type="range"]');
        let slider = null;
        for (const r of ranges) {
            const box = r.getBoundingClientRect();
            if (box.width > 100 && parseFloat(r.max) > 1) {
                slider = r;
                break;
            }
        }
        if (!slider) return 'NO_RANGE_FOUND';
        const target = 2.0;
        const propsKey = Object.keys(slider).find(k => k.startsWith('__reactProps$'));
        if (propsKey) {
            const props = slider[propsKey];
            if (props && props.onChange) {
                try { props.onChange({target: {value: target}}); } catch(e) {}
            }
        }
        return JSON.stringify({old: slider.value, newVal: target, min: slider.min, max: slider.max});
    }""")
    log(f"Slider: {slider_result}")

    time.sleep(0.5)

    # ── Step 3: Click "Offer Your Price" ──
    log("Looking for Offer Your Price button...")
    for sel in [
        "button:has-text('Offer Your Price')",
        "button:has-text('Submit Offer')",
        "button:has-text('Make Offer')",
    ]:
        loc = page.locator(sel)
        try:
            if loc.count() > 0 and loc.first.is_visible(timeout=2000):
                box = loc.first.bounding_box()
                if box:
                    show_tap(page, box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    time.sleep(0.15)
                    page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    log(f"Clicked: {sel}")
                    break
        except Exception:
            continue
    else:
        page.evaluate("""() => {
            for (const btn of document.querySelectorAll('button')) {
                const t = (btn.textContent||'').trim();
                if (t.includes('Offer') || t.includes('Submit')) { btn.removeAttribute('disabled'); btn.click(); return; }
            }
        }""")

    # ── Step 4: Wait for seller response ──
    log("Waiting for seller response...")
    time.sleep(3)

    # ── Step 5: Click "Accept the offer" if present ──
    log("Checking for Accept button...")
    for sel in ["button:has-text('Accept the offer')", "button:has-text('Accept')"]:
        loc = page.locator(sel)
        try:
            if loc.count() > 0 and loc.first.is_visible(timeout=2000):
                box = loc.first.bounding_box()
                if box:
                    show_tap(page, box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    time.sleep(0.15)
                    page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    log(f"Clicked Accept")
                    time.sleep(2)
                    break
        except Exception:
            continue

    log("Recording complete")
    time.sleep(1)


def record_single_product(playwright, product_url: str):
    # Clean up old recordings for this product
    slug = re.sub(r'[^a-zA-Z0-9_-]', '_', product_url.split("/")[-1][:50])
    for old in OUTPUT_DIR.glob(f"bargain_{slug}.*"):
        old.unlink()
        log(f"Cleaned: {old.name}")
    for old in OUTPUT_DIR.glob("page@*.webm"):
        if old.is_file():
            old.unlink()
            log(f"Cleaned: {old.name}")

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=DEVICE_SCALE_FACTOR,
        is_mobile=True,
        has_touch=True,
        record_video_dir=str(OUTPUT_DIR),
        geolocation=GEOLOCATION,
        permissions=["geolocation"],
        locale="en-IN",
        timezone_id="Asia/Kolkata",
    )
    page = context.new_page()

    try:
        run_bargain_flow(page, product_url)
    except Exception as e:
        log(f"ERROR: {e}")
        raise
    finally:
        video_path = page.video.path() if page.video else None
        context.close()
        browser.close()

    if video_path:
        final_path = OUTPUT_DIR / f"bargain_{slug}.webm"
        Path(video_path).rename(final_path)
        log(f"Saved: {final_path}")
        return final_path
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-url", required=True)
    args = parser.parse_args()

    with sync_playwright() as p:
        log(f"Recording: {args.product_url}")
        record_single_product(p, args.product_url)


if __name__ == "__main__":
    main()
