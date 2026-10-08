"""IOR profile for the unchanged shared d3 Osiris transport and lifecycle."""
import os
from pathlib import Path
from workspace.utils.osiris_runtime.config import ServiceProfile, common_timeout_settings

ROOT=Path(__file__).resolve().parents[3]
SERVICE=ServiceProfile(
    service_name='ior', job_name=os.getenv('IOR_OSIRIS_JOB_NAME','srbd6'),
    image=os.getenv('IOR_OSIRIS_IMAGE','registry.ca.sbrf.ru/ci02684173/ci02697916/notebooks/python3.12/cuda12.4/d-04.000.00:d-04.000.00-geometric'), pool=os.getenv('IOR_OSIRIS_POOL','common'),
    worker_script=os.getenv('IOR_OSIRIS_WORKER_SCRIPT',str(ROOT/'skills/ior-analyzer/rerank_osiris_worker.py')),
    num_gpus=1,nfs_root=Path(os.getenv('IOR_OSIRIS_NFS_ROOT',str(ROOT/'data_store/osiris/ior'))),
    capabilities=('embed','build_index','search','retrieve','rerank'), **common_timeout_settings())
