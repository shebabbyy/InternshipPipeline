"""Store ONE default portal password in Windows Credential Manager.
Typed here on your laptop only (hidden as you type). Never put it in a file, chat, or the profile."""
import getpass

import keyring

SERVICE = "ShehabInternshipPipeline"
pw = getpass.getpass("Default internship portal password (hidden): ")
again = getpass.getpass("Type it again: ")
if not pw or pw != again:
    print("Passwords didn't match. Nothing saved.")
elif len(pw) < 10 or pw.isalpha() or pw.isdigit():
    print("Too weak for most portals (use 10+ chars with a letter, number, and symbol). Nothing saved.")
else:
    keyring.set_password(SERVICE, "__default__", pw)
    print("Saved to Windows Credential Manager. Set password_style: default in config.yaml to use it.")
