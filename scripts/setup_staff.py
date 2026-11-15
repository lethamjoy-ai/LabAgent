"""Local OS-authorized setup. Passwords are read by getpass, never CLI arguments."""
import argparse
import getpass
import json
import secrets
import warnings
from pathlib import Path
from argon2 import PasswordHasher
from app.bootstrap import create_auth


def provision(auth, config):
    created = []
    for account in config:
        existing = auth.staff.by_username(account["username"])
        if existing:
            continue
        # Disabled placeholders: the random password is discarded and cannot be used
        # to log in. An operator supplies the real password in the hidden prompt.
        auth.staff.put_account(account["id"],account["username"],auth.hasher.hash(secrets.token_urlsafe(48)),
            account["first_name"],account["last_name"],account["role"],active=False)
        for weekday in account["weekdays"]:
            auth.staff.set_hours(account["id"],weekday,account["start"],account["end"])
        created.append(account["username"])
    return created


def main():
    parser = argparse.ArgumentParser(description="Create three synthetic staff accounts and securely set local passwords.")
    parser.add_argument("--create-inactive",action="store_true",help="Provision disabled accounts/hours without setting credentials.")
    parser.add_argument("--apply-hours",action="store_true",help="Explicitly replace working hours from the configuration, without changing passwords.")
    parser.add_argument("--set-password",metavar="USERNAME",help="Explicitly reset a local account password and revoke its sessions.")
    parser.add_argument("--config",default="config/demo_staff.json")
    args = parser.parse_args()
    auth = create_auth()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    created = provision(auth,config)
    if args.apply_hours:
        for account in config:
            row = auth.staff.by_username(account["username"])
            auth.staff.replace_hours(row["id"],account["weekdays"],account["start"],account["end"])
        print("Working hours applied from local configuration. Passwords and appointments were not changed.")
        return
    if args.create_inactive:
        print(f"Created {len(created)} disabled synthetic staff accounts. Run setup again interactively to set passwords and activate them.")
        return
    usernames = [args.set_password] if args.set_password else [a["username"] for a in config]
    warnings.simplefilter("error",getpass.GetPassWarning)  # fail closed if input would echo
    for username in usernames:
        row = auth.staff.by_username(username)
        if not row:
            raise SystemExit("The requested local account does not exist.")
        if row["is_active"] and not args.set_password:
            print(f"{username}: already active; no password or schedule changes.")
            continue
        password = getpass.getpass(f"Set password for {username} (at least 12 characters): ")
        confirmation = getpass.getpass("Repeat password: ")
        if len(password) < 12 or password != confirmation:
            raise SystemExit("Passwords must match and contain at least 12 characters. This account was not activated.")
        auth.staff.activate_password(row["id"],auth.hasher.hash(password))
        del password, confirmation
        print(f"{username}: activated. Previous sessions revoked.")


if __name__ == "__main__":
    main()
