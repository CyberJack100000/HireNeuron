import pytest
from fastapi.testclient import TestClient

from app import db, llm


@pytest.fixture()
def client(tmp_path):
    db.reset(tmp_path)
    llm.set_client(None)
    from app import main
    main._failed_logins.clear()
    yield TestClient(main.app)
    db.reset()


KEY = {"X-Recruiter-Key": "demo-recruiter"}
