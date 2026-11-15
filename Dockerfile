# Dockerfile مخصوص استقرار در Hugging Face Spaces و سرورهای ابری
FROM python:3.11-slim

# تنظیم منطقه زمانی و عدم بافر کردن خروجی لاگ‌ها
ENV PYTHONUNBUFFERED=1
ENV PORT=7860
ENV HOST=0.0.0.0

WORKDIR /app

# نصب پیش‌نیازهای پایتون
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی تمامی کدهای پروژه
COPY . .

# ایجاد دایرکتوری‌های لازم برای داده‌ها و خروجی‌ها
RUN mkdir -p /app/data /app/outputs /app/config

# پورت پیش‌فرض Hugging Face Spaces
EXPOSE 7860

# اجرای سرور جزوه‌ساز اندروید
CMD ["python", "server.py", "7860"]
