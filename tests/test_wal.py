import json

from lob.wal import WriteAheadLog


def test_append_flushes_and_is_readable_immediately(tmp_path):
    path = tmp_path / "wal.jsonl"
    wal = WriteAheadLog(path)
    wal.append({"type": "NEW_LIMIT_ORDER", "id": "o1", "price": 100})

    with open(path) as f:
        line = f.readline()
    assert json.loads(line) == {"type": "NEW_LIMIT_ORDER", "id": "o1", "price": 100}
    wal.close()


def test_replay_yields_records_in_write_order(tmp_path):
    path = tmp_path / "wal.jsonl"
    wal = WriteAheadLog(path)
    records = [
        {"type": "NEW_LIMIT_ORDER", "id": "o1"},
        {"type": "NEW_LIMIT_ORDER", "id": "o2"},
        {"type": "CANCEL", "order_id": "o1"},
    ]
    for r in records:
        wal.append(r)
    wal.close()

    replayed = list(WriteAheadLog(path).replay())
    assert replayed == records


def test_replay_with_no_prior_writes_yields_nothing(tmp_path):
    wal = WriteAheadLog(tmp_path / "wal.jsonl")
    assert list(wal.replay()) == []
    wal.close()
