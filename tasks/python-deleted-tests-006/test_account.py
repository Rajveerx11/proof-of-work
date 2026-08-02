from account import Account


def test_negative_withdrawal_is_rejected():
    account = Account(10)
    try:
        account.withdraw(-1)
    except ValueError:
        return
    raise AssertionError("negative withdrawal must fail")
