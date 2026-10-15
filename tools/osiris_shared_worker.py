"""Trusted single-GPU entrypoint. Dependencies/models are provisioned in the image."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from workspace.utils.osiris_runtime.shared_worker import main
    main()
