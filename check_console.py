from playwright.sync_api import sync_playwright

def get_console_errors():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        
        errors = []
        page.on("console", lambda msg: errors.append(f"{msg.type}: {msg.text}"))
        page.on("pageerror", lambda exc: errors.append(f"PAGE ERROR: {exc}"))
        
        page.goto("http://localhost:5000/automation-testing")
        page.wait_for_load_state("networkidle")
        
        for e in errors:
            print("BROWSER ERROR:", e)
            
        # also print body innerhtml to see if run-section is there
        try:
            print("RUN SECTION HTML:", page.locator("#run-section").inner_html())
        except Exception as e:
            print("RUN SECTION NOT FOUND", e)
            
        browser.close()

if __name__ == "__main__":
    get_console_errors()
