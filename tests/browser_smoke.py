"""Optional real-browser integration smoke test; local temp data, no API calls."""
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from playwright.sync_api import sync_playwright


def main():
    with tempfile.TemporaryDirectory() as temp:
        patches = []
        for name, folder in (("BASE_DIR", ""), ("CONFIG_DIR", "config"), ("OUTPUTS_DIR", "outputs"), ("DATA_DIR", "data")):
            path = os.path.join(temp, folder)
            os.makedirs(path, exist_ok=True)
            item = patch.object(server, name, path)
            item.start()
            patches.append(item)
        state = server.AppState()
        item = patch.object(server, "GLOBAL_STATE", state)
        item.start()
        patches.append(item)
        httpd = server.ThreadedHTTPServer(("127.0.0.1", 0), server.RequestHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # External fonts are irrelevant to app behavior and not required offline.
                page.route("https://**/*", lambda route: route.abort())
                page.goto(f"http://127.0.0.1:{httpd.server_port}")
                page.wait_for_function("currentStatus.ready === false")
                assert page.locator("#btnStart").is_disabled()
                page.evaluate("switchTab('projects')")
                page.wait_for_function("allProjects.length === 0")
                page.evaluate("openNewProjectModal()")
                page.locator("#projTitle").fill("آزمون مرورگر")
                page.locator("#projDelay").fill("0")
                page.evaluate("saveCurrentProject()")
                page.wait_for_function("allProjects.length === 1 && !mutationBusy")
                page.locator("#fileUploader").set_input_files({"name": "source.txt", "mimeType": "text/plain", "buffer": "این متن آزمایشی منبع است و درخواست پولی ارسال نمی‌شود.".encode()})
                page.wait_for_function("currentStatus.ready === true && !mutationBusy")
                assert state.get_active_project()["delay"] == 0
                assert state.runner.ledger.get_stats()["total"] > 0
                page.evaluate("switchTab('chunks')")
                page.locator(".chunk-item").first.wait_for()
                page.locator(".chunk-item").first.click()
                page.wait_for_function("document.getElementById('chunkModalArabic').textContent.includes('این متن')")
                assert "این متن" in page.locator("#chunkModalArabic").inner_text()
                page.evaluate("closeChunkModal(); switchTab('providers')")
                page.wait_for_function("allProviders.length === 0")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "Mobile horizontal overflow"
                assert not errors, errors
                # Secure service-worker install should have no JS exception; API stays live.
                page.reload()
                page.wait_for_function("currentStatus.ready === true")
                assert not errors, errors
                context.close()
                browser.close()
                print("PASS: Chromium mobile 390x844: new project, zero delay, upload, status, chunks, reload, no JS errors/overflow")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()
            if state.runner:
                state.discard_runner()
            for item in reversed(patches):
                item.stop()


if __name__ == "__main__":
    main()
