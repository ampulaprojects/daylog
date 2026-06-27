#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import (
    init_db, add_to_whitelist, remove_from_whitelist, get_whitelist,
    change_user_password, get_all_users
)
import argparse


def cmd_add_whitelist(args):
    ok = add_to_whitelist(args.email, added_by="cli")
    print(f"{'Pridaný' if ok else 'Už existuje'}: {args.email}")


def cmd_remove_whitelist(args):
    remove_from_whitelist(args.email)
    print(f"Odstránený: {args.email}")


def cmd_list_whitelist(args):
    items = get_whitelist()
    if not items:
        print("Whitelist je prázdny")
        return
    print(f"{'Email':<35} {'Pridaný kým':<15} Dátum")
    print("-" * 65)
    for item in items:
        print(f"{item['email']:<35} {item['added_by'] or '-':<15} {item['added_at'][:10]}")


def cmd_change_password(args):
    change_user_password(args.username, args.password)
    print(f"Heslo zmenené: {args.username}")


def cmd_list_users(args):
    users = get_all_users()
    if not users:
        print("Žiadni užívatelia")
        return
    print(f"{'ID':<5} {'Username':<15} {'Role':<8} {'Email':<30} Vytvorený")
    print("-" * 75)
    for u in users:
        print(f"{u['id']:<5} {u['username']:<15} {u['role']:<8} {u['email'] or '-':<30} {(u['created_at'] or '')[:10]}")


def main():
    init_db()
    parser = argparse.ArgumentParser(description="daylog — správa užívateľov")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("add-whitelist", help="Pridaj email na whitelist")
    p.add_argument("email")

    p = sub.add_parser("remove-whitelist", help="Odstráň email z whitelistu")
    p.add_argument("email")

    sub.add_parser("list-whitelist", help="Vypíš whitelist")

    p = sub.add_parser("change-password", help="Zmeň heslo užívateľa")
    p.add_argument("username")
    p.add_argument("password")

    sub.add_parser("list-users", help="Vypíš všetkých užívateľov")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {
        "add-whitelist": cmd_add_whitelist,
        "remove-whitelist": cmd_remove_whitelist,
        "list-whitelist": cmd_list_whitelist,
        "change-password": cmd_change_password,
        "list-users": cmd_list_users,
    }[args.command](args)


if __name__ == "__main__":
    main()
