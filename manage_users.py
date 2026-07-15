#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import (
    init_db, get_all_users, update_user_password, get_user_by_username,
    create_user,
)
import argparse


def cmd_list_users(args):
    users = get_all_users()
    if not users:
        print("Ziadni pouzivatelia")
        return
    print(f"{'ID':<5} {'Username':<15} {'Role':<8} Vytvoreny")
    print("-" * 50)
    for u in users:
        print(f"{u['id']:<5} {u['username']:<15} {u['role']:<8} {(u['created_at'] or '')[:10]}")


def cmd_change_password(args):
    user = get_user_by_username(args.username)
    if not user:
        print(f"Pouzivatel nenajdeny: {args.username}")
        return
    update_user_password(user["id"], args.password)
    print(f"Heslo zmenene: {args.username}")


def cmd_add_user(args):
    if get_user_by_username(args.username):
        print(f"Pouzivatel uz existuje: {args.username}")
        return
    if not create_user(args.username, args.password, role=args.role):
        print(f"Nepodarilo sa vytvorit pouzivatela: {args.username}")
        return
    print(f"Pouzivatel vytvoreny: {args.username} (rola: {args.role})")


def main():
    init_db()
    parser = argparse.ArgumentParser(description="daylog - sprava pouzivatelov")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list-users", help="Vypis vsetkych pouzivatelov")

    p = sub.add_parser("change-password", help="Zmen heslo pouzivatela")
    p.add_argument("username")
    p.add_argument("password")

    pa = sub.add_parser("add-user", help="Vytvor noveho pouzivatela")
    pa.add_argument("username")
    pa.add_argument("password")
    pa.add_argument("--role", choices=["admin", "user"], default="user",
                    help="Rola pouzivatela (predvolene: user)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {
        "list-users": cmd_list_users,
        "change-password": cmd_change_password,
        "add-user": cmd_add_user,
    }[args.command](args)


if __name__ == "__main__":
    main()
