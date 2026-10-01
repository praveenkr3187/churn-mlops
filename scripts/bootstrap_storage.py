"""LOCAL ONLY (docker-compose / kind): create buckets on MinIO and upload a
generated training set. In the cloud, buckets come from Terraform and data
from your pipelines. Uses fsspec/s3fs only (what the images ship with).

    python scripts/bootstrap_storage.py --rows 10000
"""
import argparse
import tempfile
from pathlib import Path

import fsspec

from churn import config
from generate_data import generate


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=int, default=10_000)
    a = p.parse_args()

    for uri in (config.MODEL_REGISTRY, config.DATA_PATH):
        fs, path = fsspec.core.url_to_fs(uri)
        bucket = path.split("/")[0]
        if not fs.exists(bucket):
            fs.mkdir(bucket)  # s3fs: creates the bucket
            print(f"created bucket {bucket}")

    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "customers.csv"
        generate(a.rows).to_csv(local, index=False)
        fs, path = fsspec.core.url_to_fs(config.DATA_PATH)
        fs.put_file(str(local), path)
    print(f"uploaded {a.rows:,} rows to {config.DATA_PATH}")


if __name__ == "__main__":
    main()
