from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from creator_intel.io_utils import read_json, write_csv, write_json, write_text


def test_concurrent_json_publish_uses_unique_temporary_files(tmp_path: Path) -> None:
    destination = tmp_path / "shared.json"
    values = list(range(40))
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda value: write_json(destination, {"value": value}), values))
    assert read_json(destination)["value"] in values
    assert list(tmp_path.glob("*.tmp")) == []


def test_concurrent_text_and_csv_publish_remain_whole(tmp_path: Path) -> None:
    text_path = tmp_path / "report.md"
    csv_path = tmp_path / "videos.csv"
    values = list(range(30))

    def publish(value: int) -> None:
        write_text(text_path, f"report-{value}\n")
        write_csv(csv_path, [{"value": value}], fieldnames=["value"])

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(publish, values))
    assert text_path.read_text(encoding="utf-8").strip() in {f"report-{value}" for value in values}
    rows = csv_path.read_text(encoding="utf-8-sig").splitlines()
    assert rows[0] == "value"
    assert int(rows[1]) in values
    assert len(rows) == 2
    assert list(tmp_path.glob("*.tmp")) == []
