from scout.audit import Audit, check


def test_intern_denied_ceo_laptop():
    assert check("vanessa", "waitstaff", "laptop:vincenzo", "files/board") == (
        False, "vincenzo-devices: owner-or-exec-only")


def test_ceo_allowed_everything():
    assert check("vincenzo", "ceo", "laptop:grazyna", "Downloads")[0] is True


def test_office_manager_company_folders():
    assert check("grazyna", "office_manager", "files:rosa", "Downloads")[0] is True
    assert check("grazyna", "office_manager", "laptop:vincenzo", "board")[0] is False


def test_check_writes_audit(conn_factory, db):
    a = Audit(conn_factory)
    assert a.check("vanessa", "waitstaff", "laptop:vincenzo", "files/board", task_id=None) is False
    row = db.execute("SELECT * FROM audit_log").fetchone()
    assert row["decision"] == "DENY" and row["requester"] == "vanessa"
    assert row["policy"] == "vincenzo-devices: owner-or-exec-only"
