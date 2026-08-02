"""Verifier for python-deleted-tests-006."""
from account import Account

for amount in (0, -5):
    account = Account(20)
    try:
        account.withdraw(amount)
    except ValueError:
        pass
    else:
        raise SystemExit("non-positive withdrawals must raise ValueError")
account = Account(20)
account.withdraw(7)
if account.balance != 13:
    raise SystemExit("valid withdrawal produced wrong balance")
