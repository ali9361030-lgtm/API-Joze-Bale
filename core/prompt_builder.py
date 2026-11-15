"""سازنده پرامپت و مدیریت کانتکست زنجیره‌ای (برگرفته از Mix jozve).

امکانات:
- تزریق قطعه قبلی و پاسخ پیشین برای حفظ یکدستی و لحن تدریس
- حالت‌های مختلف کانتکست (کاهش ۷۵٪ حجم توکن با برش یک‌چهارم پایانی)
- کنترل نشانگر #پایان
"""
import re


def strip_end_marker(text: str) -> str:
    """پاکسازی نشانگر #پایان از انتهای متن تولیدی."""
    if not text:
        return ""
    text = re.sub(r"#\s*پایان\s*\Z", "", text.strip())
    return text.strip()


def extract_tail_slice(text: str, fraction: float = 0.25) -> str:
    """استخراج یک‌چهارم پایانی متن برای کاهش مصرف توکن در کانتکست."""
    if not text:
        return ""
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    text = text.strip()
    cut_idx = int(len(text) * (1.0 - fraction))
    # تلاش برای برش از ابتدای پاراگراف یا جمله
    sub = text[cut_idx:]
    nl = sub.find("\n")
    if nl != -1 and nl < 100:
        return sub[nl:].strip()
    return sub.strip()


def build_system_and_user_prompts(project: dict, current_chunk: dict,
                                   prev_arabic: str = None, prev_persian: str = None):
    system_prompt = (project.get("main_prompt") or "").strip()
    user_arabic = current_chunk.get("arabic_text", "").strip()

    context_mode = project.get("context_mode", "tail_quarter")
    enable_context = project.get("enable_context", True)

    prev_persian = strip_end_marker(prev_persian)
    context_str = ""
    if enable_context and prev_arabic and prev_persian and context_mode != "disabled":
        if context_mode == "tail_quarter":
            p_ar = extract_tail_slice(prev_arabic, 0.25)
            p_pe = extract_tail_slice(prev_persian, 0.25)
        else:  # full
            p_ar = prev_arabic.strip()
            p_pe = prev_persian.strip()

        tpl = project.get("context_prompt") or (
            "<previous_context>\n"
            "- متن عربی قطعه‌ی قبلی:\n{PREVIOUS_ARABIC_TEXT}\n\n"
            "- آخرین تدریس فارسی تو (نمونه لحن و پیوستگی):\n{PREVIOUS_PERSIAN_OUTPUT}\n"
            "</previous_context>"
        )
        context_str = tpl.replace("{PREVIOUS_ARABIC_TEXT}", p_ar).replace("{PREVIOUS_PERSIAN_OUTPUT}", p_pe)

    user_content = ""
    if context_str:
        user_content += f"{context_str}\n\n"

    heading = current_chunk.get("heading")
    if heading:
        user_content += f"عنوان / سرفصل: {heading}\n\n"

    user_content += (
        "<current_text>\n"
        f"{user_arabic}\n"
        "</current_text>\n\n"
        "الزام مهم: متن عربی فوق را تدریس کن و در آخرین خط خروجی، حتماً فقط بنویس: #پایان"
    )

    return system_prompt, user_content
