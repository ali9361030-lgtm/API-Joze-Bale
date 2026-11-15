"""سیستم جامع رندر اسناد Word (.docx) و کتاب‌خوان HTML تعاملی
کاملاً منطبق بر معماری و کیفیت Mix jozve و API Jozve با رعایت کامل تایپوگرافی، علائم نگارشی فارسی و تم‌های مطالعه.
"""
import html
from html.parser import HTMLParser
import os
import re
import tempfile
from pathlib import Path

try:
    import markdown
except ImportError:
    markdown = None

from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from .prompt_builder import strip_end_marker

HTML_READER_HEADER_TEMPLATE = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{DOCUMENT_TITLE}</title>
    <style>
        :root {
            --bg-color: #fbf0d9;
            --text-color: #3e3124;
            --heading-color: #8c2d19;
            --card-bg: #fffbf2;
            --card-border: rgba(80, 50, 20, 0.15);
            --accent-color: #b87333;
            --accent-hover: #9c5d23;
            --border-color: rgba(80, 50, 20, 0.18);
            --quote-bg: rgba(80, 50, 20, 0.06);
            --quote-border: #c88a3e;
            --arabic-color: #9c2717;
            --divider-color: #d1bfa3;
            --shadow: 0 4px 20px rgba(75, 56, 42, 0.08);
            --font-size: 19px;
            --line-height: 2.15;
        }

        body.theme-light {
            --bg-color: #f8f9fa;
            --text-color: #2c3e50;
            --heading-color: #1a252f;
            --card-bg: #ffffff;
            --card-border: rgba(0, 0, 0, 0.08);
            --accent-color: #007bff;
            --accent-hover: #0056b3;
            --border-color: rgba(0, 0, 0, 0.12);
            --quote-bg: rgba(0, 0, 0, 0.035);
            --quote-border: #f39c12;
            --arabic-color: #b71c1c;
            --divider-color: #bdc3c7;
            --shadow: 0 2px 12px rgba(0, 0, 0, 0.05);
        }

        body.theme-sepia {
            --bg-color: #fbf0d9;
            --text-color: #3e3124;
            --heading-color: #8c2d19;
            --card-bg: #fffbf2;
            --card-border: rgba(80, 50, 20, 0.15);
            --accent-color: #b87333;
            --accent-hover: #9c5d23;
            --border-color: rgba(80, 50, 20, 0.18);
            --quote-bg: rgba(80, 50, 20, 0.06);
            --quote-border: #c88a3e;
            --arabic-color: #9c2717;
            --divider-color: #d1bfa3;
            --shadow: 0 4px 20px rgba(75, 56, 42, 0.08);
        }

        body.theme-dark {
            --bg-color: #18191a;
            --text-color: #e4e6eb;
            --heading-color: #f5f6f7;
            --card-bg: #242526;
            --card-border: rgba(255, 255, 255, 0.1);
            --accent-color: #4dabf7;
            --accent-hover: #339af0;
            --border-color: rgba(255, 255, 255, 0.15);
            --quote-bg: rgba(255, 255, 255, 0.05);
            --quote-border: #f1c40f;
            --arabic-color: #ff8a65;
            --divider-color: #4e4f50;
            --shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
        }

        * {
            box-sizing: border-box;
            transition: background-color 0.25s ease, color 0.25s ease, border-color 0.25s ease;
        }

        body {
            margin: 0;
            padding: 0;
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: 'IRYekan', 'Vazirmatn', 'IRANYekan', 'IRAN Yekan', Tahoma, Arial, sans-serif !important;
            font-size: var(--font-size);
            line-height: var(--line-height);
            text-rendering: optimizeLegibility;
            -webkit-font-smoothing: antialiased;
        }

        /* Top Slim Progress Bar */
        #progressContainer {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 4px;
            background: transparent;
            z-index: 1000;
        }
        #progressBar {
            height: 100%;
            background: var(--accent-color);
            width: 0%;
            transition: width 0.1s ease-out;
            box-shadow: 0 0 8px var(--accent-color);
        }

        /* Sticky Navigation Bar */
        .reader-navbar {
            position: sticky;
            top: 4px;
            z-index: 998;
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            padding: 9px 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            max-width: 900px;
            margin: 0 auto 20px auto;
            border-radius: 12px;
            box-shadow: var(--shadow);
            opacity: 0.96;
        }

        .navbar-title {
            font-weight: 700;
            font-size: 0.95rem;
            color: var(--heading-color);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 340px;
        }

        .navbar-tools {
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .btn-tool {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            color: var(--text-color);
            padding: 6px 12px;
            border-radius: 8px;
            font-family: inherit;
            font-size: 0.84rem;
            font-weight: 500;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 4px;
            transition: all 0.2s ease;
        }
        .btn-tool:hover {
            border-color: var(--accent-color);
            color: var(--accent-color);
            transform: translateY(-1px);
        }

        /* Container & Reader Layout */
        .reader-container {
            max-width: 880px;
            width: 100%;
            margin: 0 auto;
            padding: 10px 22px 100px 22px;
            box-sizing: border-box;
        }

        .book-header {
            text-align: center;
            padding: 35px 20px 25px 20px;
            margin-bottom: 30px;
            border-bottom: 2px dashed var(--card-border);
        }
        .book-header h1 {
            font-size: 2.1rem;
            color: var(--heading-color);
            margin: 0 0 10px 0;
            font-weight: 800;
        }
        .book-header .meta {
            font-size: 0.88rem;
            opacity: 0.75;
        }

        /* Chunk Cards */
        .chunk-card {
            width: 100%;
            max-width: 100%;
            box-sizing: border-box;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 32px 36px;
            margin-bottom: 32px;
            box-shadow: var(--shadow);
            position: relative;
            overflow-wrap: break-word;
            word-wrap: break-word;
        }

        .chunk-card .chunk-card {
            border: none !important;
            padding: 22px 0 0 0 !important;
            margin-bottom: 20px !important;
            box-shadow: none !important;
            background: transparent !important;
            width: 100% !important;
        }

        .chunk-badge {
            display: inline-block;
            background: var(--quote-bg);
            color: var(--heading-color);
            border: 1px solid var(--border-color);
            padding: 4px 14px;
            border-radius: 20px;
            font-size: 0.82rem;
            font-weight: 700;
            margin-bottom: 18px;
        }

        /* Arabic Source Box */
        .source-book-box {
            background: var(--quote-bg);
            border-right: 5px solid var(--heading-color);
            border-radius: 0 12px 12px 0;
            padding: 16px 20px;
            margin-bottom: 25px;
            box-shadow: inset 0 0 8px rgba(0,0,0,0.02);
        }

        .source-header {
            font-size: 0.88rem;
            font-weight: 700;
            color: var(--heading-color);
            margin-bottom: 8px;
        }

        .arabic-source {
            font-size: 1.25em;
            line-height: 2.2;
            color: var(--arabic-color);
            margin: 0;
            text-align: justify;
        }

        .arabic, blockquote {
            font-size: 1.22em;
            line-height: 2.2;
            color: var(--arabic-color);
            background: var(--quote-bg);
            padding: 15px 22px;
            border-right: 5px solid var(--accent-color);
            border-radius: 0 10px 10px 0;
            margin: 20px 0;
        }

        /* Typography */
        h1, h2, h3, h4 {
            color: var(--heading-color);
            font-weight: 800;
            margin-top: 25px;
            margin-bottom: 12px;
            line-height: 1.5;
        }
        h2 { font-size: 1.45rem; border-bottom: 1px solid var(--card-border); padding-bottom: 8px; }
        h3 { font-size: 1.22rem; color: var(--accent-color); }

        p {
            text-align: justify;
            text-justify: inter-word;
            margin: 0 0 18px 0;
            line-height: var(--line-height);
        }

        ul, ol {
            padding-right: 24px !important;
            padding-left: 0 !important;
            margin: 12px 0 16px 0;
        }

        li {
            margin-bottom: 8px;
            line-height: var(--line-height);
        }

        strong, b {
            color: var(--heading-color);
            font-weight: 700;
        }

        em, i {
            font-style: italic;
            color: var(--accent-color);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            font-size: 0.95em;
        }

        th, td {
            border: 1px solid var(--card-border);
            padding: 10px 14px;
            text-align: right;
        }

        th {
            background: var(--quote-bg);
            color: var(--heading-color);
            font-weight: 700;
        }

        hr {
            border: none;
            border-top: 1px dashed var(--card-border);
            margin: 30px 0;
        }

        /* Section Dividers */
        .book-section-divider {
            margin: 45px auto 18px auto;
            max-width: 880px;
            padding-bottom: 10px;
            border-bottom: 2px dashed var(--heading-color);
        }

        .book-section-title {
            font-size: 1.6rem;
            font-weight: 800;
            color: var(--heading-color);
            margin: 0;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        /* Floating Action Button (FAB) - Minimal & Non-intrusive */
        .floating-fab {
            position: fixed;
            bottom: 22px;
            left: 22px;
            width: 48px;
            height: 48px;
            border-radius: 50%;
            border: 1px solid var(--border-color);
            background: var(--card-bg);
            color: var(--text-color);
            font-size: 22px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            z-index: 1000;
            transition: opacity 0.3s, transform 0.2s, background 0.3s;
            opacity: 0.85;
            outline: none;
        }
        .floating-fab:hover, .floating-fab:focus {
            opacity: 1 !important;
            transform: scale(1.08);
        }
        .floating-fab.faded {
            opacity: 0.22;
        }

        /* Settings Modal / Bottom Sheet */
        .settings-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.35);
            backdrop-filter: blur(3px);
            -webkit-backdrop-filter: blur(3px);
            z-index: 1050;
            display: none;
            opacity: 0;
            transition: opacity 0.25s ease;
        }
        .settings-overlay.active {
            display: flex;
            opacity: 1;
        }

        .settings-sheet {
            position: fixed;
            bottom: 80px;
            left: 22px;
            width: 330px;
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 18px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.25);
            z-index: 1060;
            display: none;
            flex-direction: column;
            gap: 15px;
            animation: sheetPopup 0.25s ease-out;
        }
        .settings-sheet.active {
            display: flex;
        }

        @keyframes sheetPopup {
            from { opacity: 0; transform: translateY(12px) scale(0.96); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }

        @media (max-width: 600px) {
            .reader-container { padding: 5px 15px 70px 15px; }
            .chunk-card { padding: 22px 20px; border-radius: 12px; }
            .navbar-title { max-width: 140px; font-size: 0.82rem; }
            .settings-sheet {
                left: 0;
                bottom: 0;
                width: 100%;
                border-radius: 20px 20px 0 0;
                padding: 22px 20px 30px 20px;
                animation: sheetSlideUp 0.25s ease-out;
            }
            @keyframes sheetSlideUp {
                from { transform: translateY(100%); }
                to { transform: translateY(0); }
            }
        }

        .settings-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 10px;
        }
        .settings-header h3 {
            margin: 0;
            font-size: 16px;
            color: var(--heading-color);
        }
        .close-btn {
            background: none;
            border: none;
            color: var(--text-color);
            font-size: 18px;
            cursor: pointer;
            padding: 4px 8px;
            opacity: 0.7;
        }
        .close-btn:hover { opacity: 1; }

        .settings-section {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .section-label {
            font-size: 13px;
            color: var(--text-color);
            opacity: 0.85;
            font-weight: bold;
        }

        .theme-buttons {
            display: flex;
            gap: 8px;
        }
        .theme-pill {
            flex: 1;
            padding: 8px 10px;
            border-radius: 10px;
            border: 2px solid var(--border-color);
            background: var(--bg-color);
            color: var(--text-color);
            cursor: pointer;
            font-family: inherit;
            font-size: 13px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 4px;
            transition: 0.2s;
        }
        .theme-pill.active {
            border-color: var(--accent-color);
            background: var(--quote-bg);
            font-weight: bold;
            color: var(--accent-color);
        }

        .font-controls {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .font-btn {
            width: 40px;
            height: 38px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            background: var(--bg-color);
            color: var(--text-color);
            font-size: 15px;
            font-weight: bold;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: 0.2s;
        }
        .font-btn:hover { border-color: var(--accent-color); }
        .font-size-val {
            min-width: 44px;
            text-align: center;
            font-size: 14px;
            font-weight: bold;
        }
        .font-reset-btn {
            flex-grow: 1;
            height: 38px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            background: var(--bg-color);
            color: var(--text-color);
            font-family: inherit;
            font-size: 12px;
            cursor: pointer;
            transition: 0.2s;
        }
        .font-reset-btn:hover { border-color: var(--accent-color); }

        .settings-select {
            width: 100%;
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            background: var(--bg-color);
            color: var(--text-color);
            font-family: inherit;
            font-size: 13px;
            outline: none;
        }

        .stats-box {
            background: var(--quote-bg);
            border-radius: 10px;
            padding: 10px 14px;
            gap: 6px;
        }
        .stat-row {
            display: flex;
            justify-content: space-between;
            font-size: 12.5px;
        }
        .stat-row strong {
            color: var(--accent-color);
        }

        .sheet-action-btn {
            background: var(--accent-color);
            color: white;
            border: none;
            border-radius: 8px;
            padding: 9px 12px;
            font-family: inherit;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: 0.2s;
        }
        .sheet-action-btn:hover { opacity: 0.9; }
        .sheet-action-btn.secondary {
            background: var(--bg-color);
            color: var(--text-color);
            border: 1px solid var(--border-color);
        }

        /* Toast notification */
        #bookmark-toast {
            position: fixed;
            bottom: 25px;
            right: 25px;
            background: var(--card-bg);
            color: var(--text-color);
            border: 1px solid var(--accent-color);
            padding: 12px 20px;
            border-radius: 12px;
            box-shadow: 0 8px 25px rgba(0,0,0,0.18);
            display: none;
            align-items: center;
            gap: 10px;
            z-index: 10001;
            font-size: 0.9rem;
            animation: slideIn 0.3s ease;
        }

        @keyframes slideIn {
            from { transform: translateY(20px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
    </style>

    <script>
        const pageId = "gemini_read_" + encodeURIComponent(window.location.pathname || document.title);
        let currentFontSize = 19;
        let isMenuOpen = false;

        // --- 1. Settings Menu Toggle ---
        function toggleSettingsMenu() {
            if (isMenuOpen) closeSettingsMenu();
            else openSettingsMenu();
        }

        function openSettingsMenu() {
            document.getElementById('settingsOverlay').classList.add('active');
            document.getElementById('settingsSheet').classList.add('active');
            document.getElementById('menuFab').style.opacity = '1';
            isMenuOpen = true;
        }

        function closeSettingsMenu() {
            document.getElementById('settingsOverlay').classList.remove('active');
            document.getElementById('settingsSheet').classList.remove('active');
            isMenuOpen = false;
        }

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && isMenuOpen) closeSettingsMenu();
        });

        // --- 2. Theme Management ---
        function setExportTheme(theme) {
            document.body.className = theme;
            localStorage.setItem(pageId + '_theme', theme);
            localStorage.setItem('readerTheme', theme);

            const btnLight = document.getElementById('btnThemeLight');
            const btnSepia = document.getElementById('btnThemeSepia');
            const btnDark = document.getElementById('btnThemeDark');
            if (btnLight) btnLight.classList.toggle('active', theme === 'theme-light');
            if (btnSepia) btnSepia.classList.toggle('active', theme === 'theme-sepia');
            if (btnDark) btnDark.classList.toggle('active', theme === 'theme-dark');
        }

        // --- 3. Font Size Management ---
        function changeExportFontSize(step) {
            currentFontSize += step;
            if (currentFontSize < 14) currentFontSize = 14;
            if (currentFontSize > 34) currentFontSize = 34;
            applyFontSize();
        }

        function resetExportFontSize() {
            currentFontSize = 19;
            applyFontSize();
        }

        function applyFontSize() {
            document.documentElement.style.setProperty('--font-size', currentFontSize + 'px');
            const display = document.getElementById('fontSizeDisplay');
            if (display) display.innerText = currentFontSize;
            localStorage.setItem(pageId + '_fontSize', currentFontSize);
            localStorage.setItem('readerFontSize', currentFontSize);
        }

        // --- 4. Scroll Tracking & Progress Bar & FAB Auto-fade ---
        let lastScrollY = window.scrollY;
        let scrollTimeout;

        function updateProgress() {
            const winScroll = document.body.scrollTop || document.documentElement.scrollTop;
            const height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
            const scrolled = height > 0 ? (winScroll / height) * 100 : 0;
            
            const pBar = document.getElementById('progressBar');
            if (pBar) pBar.style.width = scrolled + "%";

            const pBadge = document.getElementById('scrollPercentBadge');
            if (pBadge) pBadge.innerText = Math.round(scrolled) + "%";

            // Non-intrusive FAB auto-fade on scroll
            const fab = document.getElementById('menuFab');
            if (fab) {
                if (winScroll > lastScrollY && winScroll > 120 && !isMenuOpen) {
                    fab.classList.add('faded');
                } else {
                    fab.classList.remove('faded');
                }
            }
            lastScrollY = winScroll;

            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {
                localStorage.setItem(pageId + '_scroll', winScroll);
            }, 350);
        }

        window.addEventListener('scroll', updateProgress);

        // --- 5. Reading Time Tracker ---
        let timeSpent = parseInt(localStorage.getItem(pageId + '_time') || '0', 10);

        function formatReadingTime(seconds) {
            const h = Math.floor(seconds / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            const s = seconds % 60;
            if (h > 0) return h + ' ساعت و ' + m + ' دقیقه';
            if (m > 0) return m + ' دقیقه و ' + s + ' ثانیه';
            return s + ' ثانیه';
        }

        setInterval(() => {
            if (!document.hidden) {
                timeSpent++;
                const timerBadge = document.getElementById('timerBadge');
                if (timerBadge) timerBadge.innerText = formatReadingTime(timeSpent);
                if (timeSpent % 5 === 0) {
                    localStorage.setItem(pageId + '_time', timeSpent);
                }
            }
        }, 1000);

        // --- 6. Section Jumping & Bookmarking ---
        function jumpToSection(id) {
            if (!id) return;
            const el = document.getElementById(id);
            if (el) {
                el.scrollIntoView({ behavior: 'smooth' });
                showToast("پرش به " + (el.querySelector('.chunk-badge')?.textContent || id));
            }
        }

        function resetBookmark() {
            localStorage.setItem(pageId + '_scroll', window.scrollY);
            showToast("نشانک در موقعیت فعلی ثبت شد.");
        }

        function showToast(msg) {
            const t = document.getElementById('bookmark-toast');
            if (t) {
                if (msg) t.querySelector('span').innerText = msg;
                t.style.display = 'flex';
                setTimeout(() => { t.style.display = 'none'; }, 2600);
            }
        }

        // --- 7. Table of Contents Population ---
        function populateTOC() {
            const navSelect = document.getElementById('toc-select');
            const sheetSelect = document.getElementById('toc-select-sheet');
            const cards = document.querySelectorAll('.chunk-card');

            if (navSelect) {
                while (navSelect.options.length > 1) navSelect.remove(1);
            }
            if (sheetSelect) {
                while (sheetSelect.options.length > 1) sheetSelect.remove(1);
            }

            cards.forEach(card => {
                if (!card.id) return;
                const badge = card.querySelector('.chunk-badge');
                const title = badge ? badge.textContent.replace(/[🔖📍]/g, '').trim() : card.id;

                if (navSelect) {
                    const opt = document.createElement('option');
                    opt.value = card.id;
                    opt.textContent = title;
                    navSelect.appendChild(opt);
                }
                if (sheetSelect) {
                    const opt2 = document.createElement('option');
                    opt2.value = card.id;
                    opt2.textContent = title;
                    sheetSelect.appendChild(opt2);
                }
            });
        }

        // --- 8. Initialization on Page Load ---
        window.addEventListener('DOMContentLoaded', () => {
            // Restore Theme
            const savedTheme = localStorage.getItem(pageId + '_theme') || localStorage.getItem('readerTheme') || 'theme-sepia';
            setExportTheme(savedTheme);

            // Restore Font Size
            const savedFont = localStorage.getItem(pageId + '_fontSize') || localStorage.getItem('readerFontSize');
            if (savedFont) {
                currentFontSize = parseInt(savedFont, 10);
                applyFontSize();
            }

            // Restore Timer Display
            const timerBadge = document.getElementById('timerBadge');
            if (timerBadge) timerBadge.innerText = formatReadingTime(timeSpent);

            // Populate Sections
            populateTOC();

            // Observe dynamic chunk insertions
            const contentStream = document.getElementById('content-stream');
            if (contentStream) {
                const observer = new MutationObserver(() => {
                    populateTOC();
                });
                observer.observe(contentStream, { childList: true, subtree: true });
            }

            // Restore Scroll Position
            const savedScroll = localStorage.getItem(pageId + '_scroll');
            if (savedScroll && parseInt(savedScroll, 10) > 120) {
                setTimeout(() => {
                    window.scrollTo({
                        top: parseInt(savedScroll, 10),
                        behavior: 'smooth'
                    });
                    showToast("به آخرین صفحه مطالعه‌شده بازگشتید.");
                    updateProgress();
                }, 350);
            } else {
                updateProgress();
            }
        });
    </script>
</head>
<body class="theme-sepia">

    <!-- Top Reading Progress Bar -->
    <div id="progressContainer"><div id="progressBar"></div></div>

    <!-- Sticky Navigation Bar -->
    <div class="reader-navbar">
        <div class="navbar-title" title="{DOCUMENT_TITLE}">📖 {DOCUMENT_TITLE}</div>
        <div class="navbar-tools">
            <select class="btn-tool" id="toc-select" onchange="jumpToSection(this.value)" title="فهرست و پرش به قطعات">
                <option value="">📑 رفتن به قطعه...</option>
            </select>
            <button class="btn-tool" onclick="changeExportFontSize(1)" title="بزرگ‌نمایی قلم">A+</button>
            <button class="btn-tool" onclick="changeExportFontSize(-1)" title="کوچک‌نمایی قلم">A-</button>
            <button class="btn-tool" onclick="toggleSettingsMenu()" id="themeNavBtn" title="تنظیمات مطالعه">⚙️ تنظیمات</button>
            <button class="btn-tool" onclick="resetBookmark()" title="علامت‌گذاری موقعیت فعلی">🔖 نشانک</button>
        </div>
    </div>

    <!-- Minimal Floating Action Button -->
    <button id="menuFab" class="floating-fab" onclick="toggleSettingsMenu()" title="تنظیمات مطالعه" aria-label="تنظیمات مطالعه">
        ⚙️
    </button>

    <!-- Overlay & Settings Modal / Bottom Sheet -->
    <div id="settingsOverlay" class="settings-overlay" onclick="closeSettingsMenu()"></div>
    <div id="settingsSheet" class="settings-sheet">
        <div class="settings-header">
            <h3>⚙️ تنظیمات مطالعه</h3>
            <button class="close-btn" onclick="closeSettingsMenu()" title="بستن">✕</button>
        </div>
        
        <div class="settings-section">
            <div class="section-label">🎨 تم صفحه:</div>
            <div class="theme-buttons">
                <button id="btnThemeLight" class="theme-pill" onclick="setExportTheme('theme-light')">☀️ روز</button>
                <button id="btnThemeSepia" class="theme-pill active" onclick="setExportTheme('theme-sepia')">📜 کاهی</button>
                <button id="btnThemeDark" class="theme-pill" onclick="setExportTheme('theme-dark')">🌙 شب</button>
            </div>
        </div>

        <div class="settings-section">
            <div class="section-label">🔤 اندازه قلم متن:</div>
            <div class="font-controls">
                <button class="font-btn" onclick="changeExportFontSize(-1)" title="کوچک‌نمایی قلم">A-</button>
                <span id="fontSizeDisplay" class="font-size-val">19</span>
                <button class="font-btn" onclick="changeExportFontSize(1)" title="بزرگ‌نمایی قلم">A+</button>
                <button class="font-reset-btn" onclick="resetExportFontSize()">↺ بازنشانی قلم</button>
            </div>
        </div>

        <div class="settings-section">
            <div class="section-label">📑 رفتن به قطعه:</div>
            <select class="settings-select" id="toc-select-sheet" onchange="jumpToSection(this.value); closeSettingsMenu();">
                <option value="">انتخاب قطعه...</option>
            </select>
        </div>

        <div class="settings-section stats-box">
            <div class="stat-row">
                <span>⏱️ زمان مطالعه:</span>
                <strong id="timerBadge">۰ ثانیه</strong>
            </div>
            <div class="stat-row">
                <span>📖 پیشرفت صفحه:</span>
                <strong id="scrollPercentBadge">۰٪</strong>
            </div>
        </div>

        <div style="display: flex; gap: 8px;">
            <button class="sheet-action-btn" onclick="resetBookmark()" style="flex: 1;" title="ثبت نشانک در همین نقطه">🔖 ثبت نشانک</button>
            <button class="sheet-action-btn secondary" onclick="window.scrollTo({top: 0, behavior: 'smooth'}); closeSettingsMenu();" title="پرش به ابتدای صفحه">⬆️ بالای صفحه</button>
        </div>
    </div>

    <!-- Bookmark Toast -->
    <div id="bookmark-toast">
        <span>🔖 به آخرین محل مطالعه بازگشتید</span>
    </div>

    <!-- Reader Main Content Stream -->
    <div id="content-stream" class="reader-container">
        <div class="book-header">
            <h1>{DOCUMENT_TITLE}</h1>
            <div class="meta">تدوین هوشمند با Gemini Auto Doc • همراه با امکانات پیشرفته مطالعه کتابخوان</div>
        </div>
"""

HTML_READER_FOOTER_TEMPLATE = """
    </div>
</body>
</html>
"""


def fix_inline_spaces_in_html(html_str: str) -> str:
    """تضمین وجود فاصله مناسب و تصحیح علائم نگارشی و تگ‌های درون‌خطی."""
    if not html_str:
        return ""

    pattern_before = re.compile(
        r'(?<=[^\s\(\[\{\u00ab\"\'\u201c\u201d\u2018>])(</(?:span|b|strong|i|em)>)(<(?:b|strong|i|em)>)',
        re.IGNORECASE
    )
    res = pattern_before.sub(r'\1 \2', html_str)

    pattern_raw_before = re.compile(
        r'(?<=[^\s\(\[\{\u00ab\"\'\u201c\u201d\u2018>])(<(?:b|strong|i|em)>)',
        re.IGNORECASE
    )
    res = pattern_raw_before.sub(r' \1', res)

    pattern_after = re.compile(
        r'(</(?:b|strong|i|em)>)(<(?:span|b|strong|i|em)>)(?=[^\s\.\،\›\!\؟\)\]\}\u00bb\:\…\,\;\?])',
        re.IGNORECASE
    )
    res = pattern_after.sub(r'\1 \2', res)

    pattern_raw_after = re.compile(
        r'(</(?:b|strong|i|em)>)(?=[^\s\.\،\›\!\؟\)\]\}\u00bb\:\…\,\;\?<])',
        re.IGNORECASE
    )
    res = pattern_raw_after.sub(r'\1 ', res)

    res = re.sub(r'(</(?:span|b|strong|i|em)>)\s{2,}(<(?:b|strong|i|em)>)', r'\1 \2', res)
    return res


def markdown_to_clean_html(md_text: str) -> str:
    """تبدیل خروجی مارک‌داون هوش مصنوعی به HTML استاندارد با علائم نگارشی فارسی."""
    if not md_text:
        return ""
    if markdown:
        html_raw = markdown.markdown(md_text, extensions=["extra", "nl2br", "sane_lists"])
    else:
        # Fallback parser if markdown is not loaded
        lines = []
        for line in md_text.splitlines():
            line_s = line.strip()
            if not line_s:
                continue
            if line_s.startswith("### "):
                lines.append(f"<h3>{html.escape(line_s[4:])}</h3>")
            elif line_s.startswith("## "):
                lines.append(f"<h2>{html.escape(line_s[3:])}</h2>")
            elif line_s.startswith("# "):
                lines.append(f"<h1>{html.escape(line_s[2:])}</h1>")
            elif line_s.startswith("- ") or line_s.startswith("* "):
                lines.append(f"<li>{html.escape(line_s[2:])}</li>")
            else:
                lines.append(f"<p>{html.escape(line_s)}</p>")
        html_raw = "\n".join(lines)
    return fix_inline_spaces_in_html(html_raw).strip()


def make_paragraph_rtl(paragraph):
    """اعمال جهت راست به چپ استاندارد روی پاراگراف ورد."""
    pPr = paragraph._p.get_or_add_pPr()
    bidi = pPr.find(qn("w:bidi"))
    if bidi is None:
        bidi = OxmlElement("w:bidi")
        bidi.set(qn("w:val"), "1")
        pPr.append(bidi)


def format_run_rtl(run, font_name, pt_size, color_rgb=None, is_bold=False, is_italic=False):
    """قالب‌بندی کامل قلم و جهت راست به چپ برای قطعه متن در ورد."""
    run.bold = is_bold
    run.italic = is_italic
    if color_rgb:
        run.font.color.rgb = color_rgb
    rPr = run._r.get_or_add_rPr()
    rtl = rPr.find(qn("w:rtl"))
    if rtl is None:
        rtl = OxmlElement("w:rtl")
        rtl.set(qn("w:val"), "1")
        rPr.append(rtl)
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:cs"), font_name)
    rFonts.set(qn("w:ascii"), font_name)
    rFonts.set(qn("w:hAnsi"), font_name)
    szCs = rPr.find(qn("w:szCs"))
    if szCs is None:
        szCs = OxmlElement("w:szCs")
        rPr.append(szCs)
    szCs.set(qn("w:val"), str(int(pt_size * 2)))


class WordHTMLParser(HTMLParser):
    """تبدیل ساختار HTML به پاراگراف‌های RTL و فرمت‌بندی‌شده دقیق Word همانند پروژه میکس."""

    def __init__(self, doc):
        super().__init__()
        self.doc = doc
        self.current_paragraph = None
        self.active_tags = set()
        self.list_style = None
        self.list_stack = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self.active_tags.add(tag)
        if tag == "br":
            if self.current_paragraph:
                self.current_paragraph.add_run().add_break()
            return
        if tag == "hr":
            p = self.doc.add_paragraph()
            make_paragraph_rtl(p)
            p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = p.add_run("───────────────")
            format_run_rtl(r, "IRANYekan", 10, RGBColor(160, 160, 160))
            self.current_paragraph = None
            return
        if tag in ("p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre"):
            style = None
            if tag == "li":
                style = "List Bullet" if self.list_style == "ul" else "List Number"
            self.current_paragraph = self.doc.add_paragraph(style=style)
            make_paragraph_rtl(self.current_paragraph)
            if tag in ("h1", "h2", "h3", "h4"):
                self.current_paragraph.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                self.current_paragraph.paragraph_format.space_before = Pt(14)
                self.current_paragraph.paragraph_format.space_after = Pt(6)
            elif tag == "blockquote":
                self.current_paragraph.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                self.current_paragraph.paragraph_format.right_indent = Pt(18)
                self.current_paragraph.paragraph_format.space_after = Pt(8)
                self.current_paragraph.paragraph_format.line_spacing = 1.25
            else:
                self.current_paragraph.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                self.current_paragraph.paragraph_format.space_after = Pt(8)
                self.current_paragraph.paragraph_format.line_spacing = 1.25
        elif tag in ("ul", "ol"):
            self.list_stack.append(tag)
            self.list_style = tag

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.active_tags:
            self.active_tags.remove(tag)
        if tag in ("p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre"):
            self.current_paragraph = None
        elif tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            self.list_style = self.list_stack[-1] if self.list_stack else None

    def handle_data(self, data):
        if not data.strip() and not any(t in self.active_tags for t in ("p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre")):
            return
        # رعایت علائم نگارشی استاندارد فارسی
        data = re.sub(r"(?<=[^\d]),(?=[^\d])", "،", data)
        data = data.replace("?", "؟").replace(";", "؛")
        if self.current_paragraph is None:
            self.current_paragraph = self.doc.add_paragraph()
            make_paragraph_rtl(self.current_paragraph)
            self.current_paragraph.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        run = self.current_paragraph.add_run(data)
        is_bold = any(t in self.active_tags for t in ("strong", "b", "h1", "h2", "h3", "h4"))
        is_italic = any(t in self.active_tags for t in ("em", "i"))
        if any(h in self.active_tags for h in ("h1", "h2", "h3", "h4")):
            format_run_rtl(run, "IRANYekan", pt_size=13.5, color_rgb=RGBColor(142, 68, 173), is_bold=True, is_italic=is_italic)
        elif "blockquote" in self.active_tags:
            format_run_rtl(run, "IRANYekan", pt_size=11.0, color_rgb=RGBColor(93, 64, 55), is_bold=is_bold, is_italic=True)
        else:
            color = RGBColor(192, 57, 43) if is_bold else RGBColor(44, 62, 80)
            format_run_rtl(run, "IRANYekan", pt_size=12.0, color_rgb=color, is_bold=is_bold, is_italic=is_italic)


def _completed_chunks(chunks):
    """استخراج قطعات تکمیل‌شده مرتب بر اساس شماره ایندکس."""
    result = []
    seen = set()
    for chunk in chunks:
        if chunk.get("state") not in ("done", "completed"):
            continue
        idx = chunk.get("idx")
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0 or idx in seen:
            continue
        seen.add(idx)
        if not strip_end_marker(chunk.get("persian_text") or ""):
            continue
        result.append(chunk)
    return sorted(result, key=lambda chunk: chunk["idx"])


def _atomic_write(output_path, writer):
    """ذخیره‌سازی ایمن فایل‌ها به صورت اتمیک."""
    target = Path(output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".jozve-render-", suffix=target.suffix, dir=target.parent)
    os.close(fd)
    try:
        writer(temporary)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except Exception:
                pass


def render_html_document(chunks: list, project_title: str, pure: bool, output_path: str):
    """تولید کتاب‌خوان HTML تعاملی مدرن با تم‌های روز، کاهی، شب و نشانک‌ها."""
    cards_html = []
    for chunk in _completed_chunks(chunks):
        idx = chunk["idx"] + 1
        heading = chunk.get("heading") or ""
        badge_text = f"# قطعه {idx}" + (f" • {heading}" if heading else "")
        arabic = (chunk.get("arabic_text") or "").strip()
        persian_clean = markdown_to_clean_html(strip_end_marker(chunk.get("persian_text") or ""))

        card = [f'<div class="chunk-card" id="chunk-{idx}" data-title="{html.escape(badge_text)}">']
        card.append(f'  <span class="chunk-badge">{html.escape(badge_text)}</span>')

        if arabic and not pure:
            card.append('  <div class="source-book-box">')
            card.append('    <div class="source-header">📖 متن عبارت کتاب (عربی):</div>')
            card.append(f'    <p class="arabic-source">{html.escape(arabic)}</p>')
            card.append('  </div>')

        card.append('  <div class="persian-teaching">')
        card.append(f'    {persian_clean}')
        card.append('  </div>')
        card.append('</div>')
        cards_html.append("\n".join(card))

    title_escaped = html.escape(project_title or "جزوه هوشمند")
    header_content = HTML_READER_HEADER_TEMPLATE.replace("{DOCUMENT_TITLE}", title_escaped)
    middle_content = "\n".join(cards_html)
    full_html = header_content + "\n" + middle_content + "\n" + HTML_READER_FOOTER_TEMPLATE

    def write_file(dest):
        with open(dest, "w", encoding="utf-8") as f:
            f.write(full_html)

    _atomic_write(output_path, write_file)


def render_docx_document(chunks: list, project_title: str, pure: bool, output_path: str):
    """تولید فایل Word (.docx) استاندارد با فونت‌های فارسی و چیدمان RTL پروژه میکس."""
    doc = Document()
    done_chunks = _completed_chunks(chunks)

    # عنوان اصلی سند
    if project_title:
        p_title = doc.add_paragraph()
        make_paragraph_rtl(p_title)
        p_title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_title.paragraph_format.space_before = Pt(10)
        p_title.paragraph_format.space_after = Pt(20)
        r_title = p_title.add_run(project_title)
        format_run_rtl(r_title, "IRANYekan", Pt(20), RGBColor(140, 45, 25), is_bold=True)

    for chunk in done_chunks:
        idx = chunk["idx"] + 1
        heading = chunk.get("heading") or ""
        badge_title = f"# قطعه {idx}" + (f" • {heading}" if heading else "")
        arabic = (chunk.get("arabic_text") or "").strip()
        persian_clean_html = markdown_to_clean_html(strip_end_marker(chunk.get("persian_text") or ""))

        # ۱. سرفصل در صورت وجود
        if heading:
            p_h = doc.add_paragraph()
            make_paragraph_rtl(p_h)
            p_h.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p_h.paragraph_format.space_before = Pt(24)
            p_h.paragraph_format.space_after = Pt(8)
            r_h = p_h.add_run(f"📑 {heading}")
            format_run_rtl(r_h, "IRANYekan", Pt(15), RGBColor(140, 45, 25), is_bold=True)

        # ۲. بج و متن عربی (برای نسخه اصلی)
        if arabic and not pure:
            p_badge = doc.add_paragraph()
            make_paragraph_rtl(p_badge)
            p_badge.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p_badge.paragraph_format.space_before = Pt(14)
            p_badge.paragraph_format.space_after = Pt(4)
            r_b = p_badge.add_run(f"📖 {badge_title} (متن عبارت کتاب):")
            format_run_rtl(r_b, "IRANYekan", Pt(11.5), RGBColor(180, 83, 9), is_bold=True)

            p_src = doc.add_paragraph()
            make_paragraph_rtl(p_src)
            p_src.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p_src.paragraph_format.right_indent = Pt(14)
            p_src.paragraph_format.space_after = Pt(8)
            r_src = p_src.add_run(arabic)
            format_run_rtl(r_src, "Traditional Arabic", Pt(13.0), RGBColor(60, 60, 60), is_italic=True)

            p_sep = doc.add_paragraph()
            make_paragraph_rtl(p_sep)
            p_sep.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p_sep.paragraph_format.space_after = Pt(6)
            r_sep = p_sep.add_run("✍️ شرح و تدریس عبارت فوق:")
            format_run_rtl(r_sep, "IRANYekan", Pt(11), RGBColor(142, 68, 173), is_bold=True)
        else:
            p_badge = doc.add_paragraph()
            make_paragraph_rtl(p_badge)
            p_badge.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p_badge.paragraph_format.space_before = Pt(16)
            p_badge.paragraph_format.space_after = Pt(6)
            r_b = p_badge.add_run(f"🔖 {badge_title}:")
            format_run_rtl(r_b, "IRANYekan", Pt(11.5), RGBColor(46, 107, 158), is_bold=True)

        # ۳. محتوای تدریس فارسی
        WordHTMLParser(doc).feed(persian_clean_html)

    def write_doc(dest):
        doc.save(dest)

    _atomic_write(output_path, write_doc)


def render_all_outputs(chunks: list, project_title: str, output_base: str) -> dict:
    """تولید هر ۴ فایل خروجی استاندارد: ورد اصلی، ورد خالص، کتابخوان HTML اصلی، کتابخوان خالص."""
    paths = {
        "docx": output_base + ".docx",
        "docx_pure": output_base + "_تدریس.docx",
        "html": output_base + ".html",
        "html_pure": output_base + "_تدریس.html"
    }
    render_docx_document(chunks, project_title, pure=False, output_path=paths["docx"])
    render_docx_document(chunks, project_title, pure=True, output_path=paths["docx_pure"])
    render_html_document(chunks, project_title, pure=False, output_path=paths["html"])
    render_html_document(chunks, project_title, pure=True, output_path=paths["html_pure"])
    return paths
