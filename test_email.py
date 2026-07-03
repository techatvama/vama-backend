"""Quick SMTP sanity check, independent of the app.

Usage:
    python3 test_email.py you@example.com

Reads SMTP_* from .env and tries to send a test message, printing a clear
success/failure so you can confirm Gmail credentials before relying on the
activation/reset flows.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

from auth import send_email  # noqa: E402  (after load_dotenv)


def main():
    to = sys.argv[1] if len(sys.argv) > 1 else os.getenv("SMTP_USER")
    if not to:
        print("Usage: python3 test_email.py <recipient-email>")
        sys.exit(1)

    print(f"SMTP_HOST={os.getenv('SMTP_HOST')}  SMTP_USER={os.getenv('SMTP_USER')}  "
          f"SMTP_PASSWORD={'set' if os.getenv('SMTP_PASSWORD') else 'EMPTY'}")
    ok = send_email(
        to,
        "VAMA SMTP test",
        "<p>If you can read this, Gmail SMTP is working. 🎉</p>",
    )
    print("RESULT:", "SUCCESS ✅" if ok else "FAILED ❌")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
